#!/usr/bin/env node
/*
 * Render the model-owned review-result.json into the one Markdown shape that
 * the GitLab gate accepts. This is deliberately dependency-free: CI ships
 * Node but not Python/jq.
 */
"use strict";

const fs = require("node:fs");

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("Usage: render_review_result.js <review-result.json> <review-body.md>");
  process.exit(2);
}

function fail(message) {
  console.error(`ERROR: invalid code-review result: ${message}`);
  process.exit(2);
}

let result;
try {
  result = JSON.parse(fs.readFileSync(input, "utf8"));
} catch (error) {
  fail(`cannot parse JSON (${error.message})`);
}

if (!result || typeof result !== "object" || Array.isArray(result)) fail("root must be an object");
if (!new Set(["1.0", "1.1"]).has(result.schema_version)) fail("schema_version must be 1.0 or 1.1");

const requiredText = ["scope", "documentation", "quality", "e2e"];
for (const field of requiredText) {
  if (typeof result[field] !== "string" || !result[field].trim()) fail(`${field} must be a non-empty string`);
}

const verdicts = new Map([
  ["通过", true],
  ["有条件通过", true],
  ["不通过", false],
  ["审查未完成", false],
]);
if (!verdicts.has(result.conclusion)) fail("conclusion is not supported");
if (typeof result.should_pass !== "boolean") fail("should_pass must be boolean");
if (result.should_pass !== verdicts.get(result.conclusion)) fail("conclusion and should_pass are inconsistent");
const scoreIsNumber =
  typeof result.score === "number" &&
  Number.isFinite(result.score) &&
  result.score >= 0 &&
  result.score <= 10 &&
  Number.isInteger(result.score * 10);
const scoreIsString =
  typeof result.score === "string" && /^(?:[0-9](?:\.[0-9])?|10(?:\.0)?)\/10$|^N\/A$/.test(result.score);
if (!scoreIsNumber && !scoreIsString) {
  fail("score must be N/A or a 0.0-10.0 /10 value");
}
const normalizedScore = scoreIsNumber ? `${result.score}/10` : result.score;
if (result.conclusion === "审查未完成" && normalizedScore !== "N/A") fail("incomplete review must use score N/A");
if (!Array.isArray(result.red_lines) || !Array.isArray(result.suggestions)) fail("red_lines and suggestions must be arrays");
for (const [name, values] of [["red_lines", result.red_lines], ["suggestions", result.suggestions]]) {
  if (values.some((value) => typeof value !== "string" || !value.trim())) fail(`${name} entries must be non-empty strings`);
}
if (result.conclusion === "不通过" && result.red_lines.length === 0) {
  fail("a rejected review requires red_lines");
}
if (result.red_lines.length > 0 && result.should_pass) fail("red_lines require should_pass=false");

const incompleteReasons = result.incomplete_reasons === undefined ? [] : result.incomplete_reasons;
if (!Array.isArray(incompleteReasons) || incompleteReasons.some((value) => typeof value !== "string" || !value.trim())) {
  fail("incomplete_reasons must be an array of non-empty strings");
}
if (incompleteReasons.length > 0 && result.conclusion !== "审查未完成") {
  fail("incomplete_reasons require conclusion=审查未完成");
}

const allowedTddLevels = new Set(["T0", "T1", "T2", "T3", "T4", "N/A", "UNVERIFIED"]);
const allowedTddApplicability = new Set(["applicable", "not_applicable", "unknown"]);
const maxTddSummaryChars = 2000;
const maxTddListItems = 20;
const maxTddItemChars = 1000;

function unverifiedTddAssessment(reason) {
  return {
    level: "UNVERIFIED",
    applicability: "unknown",
    summary: "TDD assessment 未能形成可信等级；Review 仍按非门禁方式继续。",
    evidence: [],
    gaps: [reason],
  };
}

function isTextArray(values) {
  return Array.isArray(values)
    && values.length <= maxTddListItems
    && values.every(
      (value) => typeof value === "string" && value.trim() && value.length <= maxTddItemChars,
    );
}

function normalizeTddAssessment() {
  if (result.tdd_assessment === undefined) {
    const reason = result.schema_version === "1.0"
      ? "legacy schema 1.0 result 未提供 TDD assessment。"
      : "schema 1.1 result 未提供 tdd_assessment。";
    return unverifiedTddAssessment(reason);
  }

  const assessment = result.tdd_assessment;
  if (!assessment || typeof assessment !== "object" || Array.isArray(assessment)) {
    return unverifiedTddAssessment("tdd_assessment 不是 object。");
  }
  if (!allowedTddLevels.has(assessment.level)) {
    return unverifiedTddAssessment("tdd_assessment.level 不受支持。");
  }
  if (!allowedTddApplicability.has(assessment.applicability)) {
    return unverifiedTddAssessment("tdd_assessment.applicability 不受支持。");
  }
  if (
    typeof assessment.summary !== "string"
    || !assessment.summary.trim()
    || assessment.summary.length > maxTddSummaryChars
  ) {
    return unverifiedTddAssessment("tdd_assessment.summary 缺失或超限。");
  }
  if (!isTextArray(assessment.evidence) || !isTextArray(assessment.gaps)) {
    return unverifiedTddAssessment("tdd_assessment evidence/gaps 格式无效。");
  }
  if (assessment.level !== "UNVERIFIED" && assessment.evidence.length === 0) {
    return unverifiedTddAssessment(`TDD level ${assessment.level} 缺少可定位 evidence。`);
  }
  if (assessment.level === "N/A" && assessment.applicability !== "not_applicable") {
    return unverifiedTddAssessment("TDD level N/A 与 applicability 不一致。");
  }
  if (assessment.applicability === "not_applicable" && assessment.level !== "N/A") {
    return unverifiedTddAssessment("applicability=not_applicable 与 TDD level 不一致。");
  }
  if (assessment.level === "UNVERIFIED" && assessment.applicability === "not_applicable") {
    return unverifiedTddAssessment("TDD level UNVERIFIED 不能对应 applicability=not_applicable。");
  }
  if (assessment.applicability === "unknown" && assessment.level !== "UNVERIFIED") {
    return unverifiedTddAssessment("applicability=unknown 与 TDD level 不一致。");
  }
  if (/^T[0-4]$/.test(assessment.level) && assessment.applicability !== "applicable") {
    return unverifiedTddAssessment(`TDD level ${assessment.level} 与 applicability 不一致。`);
  }
  return {
    level: assessment.level,
    applicability: assessment.applicability,
    summary: assessment.summary.trim(),
    evidence: assessment.evidence.map((value) => value.trim()),
    gaps: assessment.gaps.map((value) => value.trim()),
  };
}

const tddAssessment = normalizeTddAssessment();
const tddSignal = Buffer.from(JSON.stringify(tddAssessment), "utf8").toString("base64url");

function safeSection(value) {
  // Agent text remains Markdown, but cannot create a sixth top-level heading.
  return value
    .trim()
    .replace(/<!--\s*code-review-tdd-assessment\s*:/gi, "&lt;!-- code-review-tdd-assessment:")
    .replace(/^[ \t]{0,3}#{1,6}(\s+)/gm, "####$1")
    // Match the summary parser's whitespace, including vertical tabs and form feeds.
    // Process each line separately so whitespace cannot consume another line's prefix.
    .split("\n")
    .map((line) => line.replace(/^([\s\u0085]*[-*]?[\s\u0085]*\**(?:结论|评分|是否应通过|红线问题|建议改进)\**[\s\u0085]*[:：])/, "> $1"))
    .join("\n");
}

function list(values) {
  if (values.length === 0) return "无";
  return values.map((value, index) => `${index + 1}. ${safeSection(value)}`).join("\n");
}

function detailList(label, values) {
  if (values.length === 0) return `- **${label}**: 无`;
  return [
    `- **${label}**:`,
    ...values.map((value, index) => `  ${index + 1}. ${safeSection(value)}`),
  ].join("\n");
}

const tddSection = [
  "**TDD Level（观测项，不参与门禁）**",
  `- **Level**: ${tddAssessment.level}`,
  `- **适用性**: ${tddAssessment.applicability}`,
  `- **摘要**: ${safeSection(tddAssessment.summary)}`,
  detailList("证据", tddAssessment.evidence),
  detailList("缺口", tddAssessment.gaps),
].join("\n");

const body = [
  "### 1. 范围（Scope）",
  safeSection(result.scope),
  "",
  "### 2. 文档规范",
  safeSection(result.documentation),
  "",
  "### 3. 内容质量",
  safeSection(result.quality),
  "",
  tddSection,
  "",
  "### 4. 端到端一致性",
  safeSection(result.e2e),
  "",
  "### 5. 总结",
  `- **审查状态**: ${result.conclusion === "审查未完成" ? "incomplete" : "complete"}`,
  `- **结论**: ${result.conclusion}`,
  `- **评分**: ${normalizedScore}`,
  `- **是否应通过**: ${result.should_pass ? "是" : result.conclusion === "审查未完成" ? "否（审查未完成，暂不可合并）" : "否"}`,
  `- **TDD Level（观测项，不参与门禁）**: ${tddAssessment.level}`,
  `- **红线问题**: ${list(result.red_lines)}`,
  `- **未完成原因**: ${list(incompleteReasons)}`,
  `- **建议改进**: ${list(result.suggestions)}`,
  "- **执行方式**: CI 在 Agent 退出后按结构化结果确定性渲染并发布本评论。",
  "",
  `<!-- code-review-tdd-assessment:v1 ${tddSignal} -->`,
  "",
].join("\n");

fs.writeFileSync(output, body, "utf8");
console.log(`rendered_review_body=${output}`);

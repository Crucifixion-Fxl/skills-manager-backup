"use strict";

const dimensions = [
  ["requirements", "设计范围与需求"], ["content", "内容、双语与品牌"],
  ["visual", "版式与视觉"], ["production", "源文件、导出与印前"],
  ["approval", "版本审批与结论"],
];
const statuses = new Set(["PASS", "FAIL", "NEEDS_HUMAN", "NOT_CHECKED"]);
function validateDesign(result, fail) {
  const profile = result.review_profile === undefined ? "technical" : result.review_profile;
  if (!["technical", "design", "mixed"].includes(profile)) fail("review_profile is unsupported");
  if (profile === "technical") {
    if (result.design_review !== undefined) fail("technical profile cannot hide design_review");
    return null;
  }
  if (result.schema_version !== "1.1") fail("design profile requires schema 1.1");
  if (result.score !== "N/A") fail("design profile uses score N/A, not a code score");
  const report = result.design_review;
  const object = (v) => v && typeof v === "object" && !Array.isArray(v);
  const text = (v) => typeof v === "string" && v.trim() && v.length <= 16000;
  if (!object(report) || !object(report.dimensions)) fail("design_review and dimensions are required");
  if (!["concept", "design-review", "prepress-handoff"].includes(report.stage)) fail("design stage is unsupported");
  const keys = dimensions.map(([key]) => key);
  if (Object.keys(report.dimensions).some((key) => !keys.includes(key))) fail("unknown design dimension");
  for (const [key] of dimensions) {
    const item = report.dimensions[key];
    if (!object(item) || !statuses.has(item.status) || typeof item.required !== "boolean" || !text(item.summary)) {
      fail(`invalid design dimension ${key}`);
    }
    const evidencePath = `design_review.dimensions.${key}.evidence`;
    if (!Array.isArray(item.evidence)) {
      fail(`${evidencePath} must be an array of at most 30 non-empty strings`);
    }
    if (item.evidence.length > 30) fail(`${evidencePath} must contain at most 30 entries`);
    if (item.evidence.some((value) => !text(value))) {
      fail(`${evidencePath} entries must be non-empty strings of at most 16000 UTF-16 code units (JavaScript String.length)`);
    }
    if (["PASS", "FAIL"].includes(item.status) && item.evidence.length === 0) fail(`${key} status needs evidence`);
    if (item.required && item.status === "FAIL" && result.should_pass) fail(`required failed dimension ${key} cannot pass`);
    if (item.required && ["NOT_CHECKED", "NEEDS_HUMAN"].includes(item.status) && result.conclusion !== "审查未完成") {
      fail(`required unchecked dimension ${key} requires incomplete conclusion`);
    }
  }
  if (!Array.isArray(report.findings) || report.findings.length > 100) fail("design findings must be a bounded array");
  for (const finding of report.findings) {
    if (!object(finding) || !keys.includes(finding.dimension) || !["P0", "P1", "P2", "P3"].includes(finding.severity)) fail("invalid design finding");
    if (typeof finding.blocking !== "boolean") fail("finding requires explicit blocking boolean");
    if (finding.severity === "P0" && !finding.blocking) fail("P0 cannot be nonblocking");
    if (finding.blocking && (result.should_pass || result.red_lines.length === 0)) fail("blocking finding requires rejection and red_lines");
    for (const key of ["path", "location", "actual", "expected", "source", "impact", "fix", "human_action"]) {
      if (!text(finding[key])) fail(`design finding requires ${key}`);
    }
  }
  if (profile === "mixed" && !text(report.technical_appendix)) fail("mixed profile requires relevant technical_appendix");
  if (profile === "design" && report.technical_appendix !== undefined) fail("design profile cannot hide a technical appendix");
  if (result.conclusion === "审查未完成" && (!Array.isArray(result.incomplete_reasons) || !result.incomplete_reasons.length)) {
    fail("incomplete design review requires specific reasons");
  }
  return report;
}

function renderDesign(result, report, section) {
  // Reviewed text cannot close the trusted evidence disclosure or forge a summary.
  const safeSection = (value) => section(value).replace(/<\/?(?:details|summary)\b[^>]*>/gi,
    (tag) => tag.replace(/</g, "&lt;").replace(/>/g, "&gt;"));
  const list = (values) => values.length ? values.map((value, index) => `${index + 1}. ${safeSection(value)}`).join("\n") : "无";
  const stageNames = { concept: "概念讨论", "design-review": "设计评审", "prepress-handoff": "印前交付" };
  const statusNames = { PASS: "已核对", FAIL: "需要修改", NEEDS_HUMAN: "还需确认", NOT_CHECKED: "尚未核对" };
  const conclusionNames = {
    "通过": "本阶段已检查的内容符合要求。",
    "有条件通过": "本阶段可以继续推进，请一并处理下方建议。",
    "不通过": "请先修改下方必须改的问题，再继续本阶段交付。",
    "审查未完成": "目前还不能给出完整结论，请先补齐下方材料或确认。",
  };
  const body = [`**本次结论：${conclusionNames[result.conclusion]}**`,
    `本次评审处于「${stageNames[report.stage]}」阶段。`,
    "本结论仅覆盖本阶段设计审查；印刷、量产或发送供应商仍需对应版本的授权。", ""];
  if (result.red_lines.length) body.push("**这次先处理这些问题**", list(result.red_lines), "");
  for (const [index, [key, title]] of dimensions.entries()) {
    const item = report.dimensions[key];
    body.push(`### ${index + 1}. ${title}`, `**${statusNames[item.status]}${item.required ? "" : "（本阶段非必需，见说明）"}**`,
      safeSection(item.summary));
    for (const finding of report.findings.filter((f) => f.dimension === key)) {
      const action = finding.blocking ? "必须改" : item.status === "NEEDS_HUMAN" ? "还需确认" : finding.severity === "P3" ? "建议" : "需修改";
      body.push(`#### ${action}：${safeSection(finding.path)} · ${safeSection(finding.location)}`,
        `- **看到的问题**：${safeSection(finding.actual)}`,
        `- **为什么要处理**：${safeSection(finding.impact)}`,
        `- **怎么改**：${safeSection(finding.fix)}`,
        `- **由谁判断什么**：${safeSection(finding.human_action)}`,
        "<details>", "<summary>对照要求与依据</summary>", "",
        `- **应达到的要求**：${safeSection(finding.expected)}`,
        `- **要求来源**：${safeSection(finding.source)}`,
        `- **机器记录**：${finding.severity}；blocking=${finding.blocking}`, "", "</details>");
    }
    body.push("<details>", "<summary>查看本项核查记录</summary>", "",
      `- **检查状态**: ${item.status}`, `- **证据**: ${list(item.evidence)}`, "", "</details>", "");
  }
  if ((result.incomplete_reasons || []).length) body.push("**还需补齐或确认**", list(result.incomplete_reasons), "");
  if (result.suggestions.length) body.push("**其他建议**", list(result.suggestions), "");
  body.push("<details>", "<summary>审查范围、版本与技术记录</summary>", "", safeSection(result.scope));
  if (result.review_profile === "mixed") body.push("#### 相关技术附录", safeSection(report.technical_appendix), "");
  body.push(`- **审查状态**: ${result.conclusion === "审查未完成" ? "incomplete" : "complete"}`,
    `- **结论**: ${result.conclusion}`, `- **是否应通过**: ${result.should_pass ? "是" : "否"}`,
    `- **红线问题**: ${list(result.red_lines)}`,
    "- **放行记录**: manufacturing_release=false。",
    "- **发布方式**: CI 按结构化结果生成并发布本评论。", "", "</details>",
    `<!-- code-review-profile:${result.review_profile} -->`, "");
  return body.join("\n");
}
module.exports = { validateDesign, renderDesign };

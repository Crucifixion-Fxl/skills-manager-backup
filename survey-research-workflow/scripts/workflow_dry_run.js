#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");

const ALLOWED_READINESS = new Set(["ready", "not_ready", "blocked"]);
const ALLOWED_SAMPLE_SUFFICIENCY = new Set(["met", "underfilled", "uneven", "unknown"]);
const ALLOWED_COHORT_STATUS = new Set(["ready", "underfilled", "quality_risk", "blocked"]);
const ALLOWED_ROUTES = new Set(["revise_survey", "revise_email", "sample_action", "sample_status_memo"]);
const ALLOWED_SAMPLE_ACTIONS = new Set(["wait", "resend", "expand_audience", "split_new_wave"]);

const PROJECT_FILES = {
  monitorStatus: ["monitoring/monitor_status.json", "monitor_status.json"],
  followupStrategy: ["follow-up-and-report/followup_strategy.json", "followup_strategy.json"],
  sampleStatus: ["follow-up-and-report/sample-status.md", "sample-status.md"],
  researchReport: ["follow-up-and-report/research-report.md", "research-report.md"],
  findingsData: ["monitoring/findings_data.json", "findings_data.json"],
  workflowState: ["workflow-state.json"],
  artifactIndex: ["artifact-index.md"],
  decisionLog: ["decision-log.md"],
};

function usage() {
  return [
    "Usage:",
    "  node scripts/workflow_dry_run.js check <project_dir> [--out-dir <dir>]",
    "  node scripts/workflow_dry_run.js self-test",
  ].join("\n");
}

function parseOptions(args) {
  const options = { positional: [] };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg.startsWith("--")) {
      const key = arg.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      if (!args[i + 1] || args[i + 1].startsWith("--")) {
        options[key] = true;
      } else {
        options[key] = args[i + 1];
        i += 1;
      }
    } else {
      options.positional.push(arg);
    }
  }
  return options;
}

function nowIso() {
  return new Date().toISOString();
}

function exists(filePath) {
  return fs.existsSync(filePath);
}

function resolveFirst(projectDir, candidates) {
  for (const relativePath of candidates) {
    const filePath = path.join(projectDir, relativePath);
    if (exists(filePath)) return filePath;
  }
  return null;
}

function readJson(filePath, state, label) {
  if (!filePath) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    state.blockers.push(`${label} is not valid JSON: ${error.message}`);
    return null;
  }
}

function pushMissingAction(state, message) {
  if (!state.next_actions.includes(message)) state.next_actions.push(message);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function relative(projectDir, filePath) {
  if (!filePath) return null;
  return path.relative(projectDir, filePath);
}

function initState(projectDir) {
  const found = {};
  for (const [key, candidates] of Object.entries(PROJECT_FILES)) {
    found[key] = resolveFirst(projectDir, candidates);
  }
  return {
    generated_at: nowIso(),
    project_dir: projectDir,
    overall_status: "pass",
    blockers: [],
    warnings: [],
    next_actions: [],
    found_files: Object.fromEntries(
      Object.entries(found).map(([key, value]) => [key, relative(projectDir, value)])
    ),
    summary: {
      report_readiness: null,
      sample_sufficiency: null,
      followup_route: null,
      sample_action: null,
      report_gate: "unknown",
    },
    _found: found,
  };
}

function validateRootFiles(state) {
  if (!state._found.workflowState) {
    state.warnings.push("缺少 workflow-state.json；无法检查 workflow 状态。");
    pushMissingAction(state, "从 survey-research-workflow 模板创建或恢复 workflow-state.json。");
  }
  if (!state._found.artifactIndex) {
    state.warnings.push("缺少 artifact-index.md；项目产物导航可能漂移。");
    pushMissingAction(state, "创建或更新 artifact-index.md。");
  }
  if (!state._found.decisionLog) {
    state.warnings.push("缺少 decision-log.md；owner gate 无法审计。");
    pushMissingAction(state, "在任何外部动作前创建或更新 decision-log.md。");
  }
}

function validateMonitorStatus(state, monitorStatus) {
  if (!monitorStatus) {
    state.summary.report_readiness = "missing";
    pushMissingAction(state, "运行 user-research-monitor，并写入 monitoring/monitor_status.json。");
    return;
  }

  if (!isNonEmptyString(monitorStatus.snapshot_time)) {
    state.blockers.push("monitor_status.snapshot_time is required.");
  }

  if (!ALLOWED_READINESS.has(monitorStatus.report_readiness)) {
    state.blockers.push("monitor_status.report_readiness must be ready | not_ready | blocked.");
  } else {
    state.summary.report_readiness = monitorStatus.report_readiness;
  }

  if (!ALLOWED_SAMPLE_SUFFICIENCY.has(monitorStatus.sample_sufficiency)) {
    state.blockers.push("monitor_status.sample_sufficiency must be met | underfilled | uneven | unknown.");
  } else {
    state.summary.sample_sufficiency = monitorStatus.sample_sufficiency;
  }

  if (!Array.isArray(monitorStatus.cohorts) || monitorStatus.cohorts.length === 0) {
    state.blockers.push("monitor_status.cohorts must be a non-empty array.");
  } else {
    for (const [index, cohort] of monitorStatus.cohorts.entries()) {
      const label = `monitor_status.cohorts[${index}]`;
      for (const field of ["cohort_key", "business_label"]) {
        if (!isNonEmptyString(cohort[field])) state.blockers.push(`${label}.${field} is required.`);
      }
      for (const field of ["raw_n", "valid_n_conservative", "valid_n_strict", "minimum_valid_n"]) {
        if (!isNumber(cohort[field])) state.blockers.push(`${label}.${field} must be a number.`);
      }
      if (!ALLOWED_COHORT_STATUS.has(cohort.status)) {
        state.blockers.push(`${label}.status must be ready | underfilled | quality_risk | blocked.`);
      }
      if (isNumber(cohort.valid_n_conservative) && isNumber(cohort.raw_n) && cohort.valid_n_conservative > cohort.raw_n) {
        state.blockers.push(`${label}.valid_n_conservative cannot exceed raw_n.`);
      }
      if (isNumber(cohort.valid_n_strict) && isNumber(cohort.valid_n_conservative) && cohort.valid_n_strict > cohort.valid_n_conservative) {
        state.blockers.push(`${label}.valid_n_strict cannot exceed valid_n_conservative.`);
      }
    }
  }

  if (!Array.isArray(monitorStatus.quality_risks)) {
    state.blockers.push("monitor_status.quality_risks must be an array.");
  }

  if (!monitorStatus.followup_recommendation_inputs || typeof monitorStatus.followup_recommendation_inputs !== "object" || Array.isArray(monitorStatus.followup_recommendation_inputs)) {
    state.blockers.push("monitor_status.followup_recommendation_inputs must be an object.");
  }
}

function validateFollowup(state, monitorStatus, followupStrategy, workflowState) {
  const readiness = monitorStatus?.report_readiness;
  if (!readiness || readiness === "ready") return;

  if (!followupStrategy) {
    pushMissingAction(state, "Run follow-up-strategist and write follow-up-and-report/followup_strategy.json.");
    return;
  }

  const route = followupStrategy.route;
  if (!ALLOWED_ROUTES.has(route)) {
    state.blockers.push("followup_strategy.route must be revise_survey | revise_email | sample_action | sample_status_memo.");
    return;
  }

  state.summary.followup_route = route;

  if (!isNonEmptyString(followupStrategy.diagnosis)) {
    state.blockers.push("followup_strategy.diagnosis is required.");
  }
  if (!isNonEmptyString(followupStrategy.recommended_action)) {
    state.blockers.push("followup_strategy.recommended_action is required.");
  }
  if (!Array.isArray(followupStrategy.owner_decisions_needed)) {
    state.blockers.push("followup_strategy.owner_decisions_needed must be an array.");
  }
  if (!Array.isArray(followupStrategy.next_files_to_update)) {
    state.blockers.push("followup_strategy.next_files_to_update must be an array.");
  }

  if (route === "sample_action") {
    if (!ALLOWED_SAMPLE_ACTIONS.has(followupStrategy.sample_action)) {
      state.blockers.push("followup_strategy.sample_action must be wait | resend | expand_audience | split_new_wave when route=sample_action.");
    } else {
      state.summary.sample_action = followupStrategy.sample_action;
    }
    pushMissingAction(state, "执行 sample_action 前必须由 owner 确认；动作或等待窗口结束后，重新运行 user-research-monitor。");
  }

  if (route === "revise_survey") {
    pushMissingAction(state, "回到 survey-designer 修订问卷，然后重新运行目标平台 preflight、read-back 和全路径 preview。");
  }

  if (route === "revise_email") {
    pushMissingAction(state, "回到 research-email-writer 修订邮件，然后重新运行 Mailchimp build/draft/test。");
  }

  if (route === "sample_status_memo" && !state._found.sampleStatus) {
    pushMissingAction(state, "运行 research-report-writer，创建 follow-up-and-report/sample-status.md。");
  }

  const stateRoute = workflowState?.followup_route;
  if (!stateRoute || stateRoute.route !== route) {
    pushMissingAction(state, "更新 workflow-state.json 的 followup_route.route，使其与 followup_strategy.json 一致。");
  } else if (stateRoute.owner_confirmed !== true) {
    pushMissingAction(state, "在执行 follow-up 或冻结报告前，把 owner confirmation 记录到 workflow-state.json。");
  }
  if (stateRoute && stateRoute.route && stateRoute.route !== route) {
    state.warnings.push(`workflow-state followup_route.route (${stateRoute.route}) 与 followup_strategy.route (${route}) 不一致。`);
  }
}

function findFindingsData(projectDir, state) {
  if (state._found.findingsData) return state._found.findingsData;

  const candidates = [];
  const roots = ["monitoring", "data_analysis"];
  for (const root of roots) {
    const absRoot = path.join(projectDir, root);
    if (!exists(absRoot)) continue;
    collectFiles(absRoot, "findings_data.json", candidates, 4);
  }
  if (candidates.length > 0) {
    state._found.findingsData = candidates[0];
    state.found_files.findingsData = relative(projectDir, candidates[0]);
    return candidates[0];
  }
  return null;
}

function collectFiles(root, targetName, out, maxDepth, depth = 0) {
  if (depth > maxDepth) return;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const filePath = path.join(root, entry.name);
    if (entry.isFile() && entry.name === targetName) {
      out.push(filePath);
    } else if (entry.isDirectory()) {
      collectFiles(filePath, targetName, out, maxDepth, depth + 1);
    }
  }
}

function validateReportGate(state, projectDir, monitorStatus, followupStrategy) {
  const readiness = monitorStatus?.report_readiness;
  const reportExists = Boolean(state._found.researchReport);
  const sampleStatusExists = Boolean(state._found.sampleStatus);
  const findingsData = findFindingsData(projectDir, state);

  if (!readiness) {
    state.summary.report_gate = "waiting_for_monitor_status";
    return;
  }

  if (readiness === "ready") {
    if (!findingsData) {
      state.blockers.push("monitor_status.report_readiness=ready，但缺少 findings_data.json。");
      pushMissingAction(state, "运行 user-research-monitor 项目分析，生成 monitoring/findings_data.json。");
    }
    if (reportExists) {
      state.summary.report_gate = "formal_report_present";
    } else {
      state.summary.report_gate = "ready_for_formal_report";
      pushMissingAction(state, "按 user-research-report 规则运行 research-report-writer，创建 research-report.md。");
    }
    return;
  }

  if (readiness === "not_ready" || readiness === "blocked") {
    if (reportExists) {
      state.blockers.push("monitor_status.report_readiness 尚未 ready，但项目里已经存在正式 research-report.md。");
    }
    if (followupStrategy?.route === "sample_status_memo") {
      state.summary.report_gate = sampleStatusExists ? "sample_status_present" : "sample_status_needed";
      if (!sampleStatusExists) {
        pushMissingAction(state, "创建 sample-status.md，而不是正式 research-report.md。");
      }
    } else {
      state.summary.report_gate = "formal_report_blocked";
    }
  }
}

function finalizeStatus(state) {
  if (state.blockers.length > 0) {
    state.overall_status = "block";
  } else if (state.next_actions.length > 0 || state.warnings.length > 0) {
    state.overall_status = "needs_action";
  } else {
    state.overall_status = "pass";
  }
}

function renderMarkdown(state) {
  return [
    "# 用户研究 Workflow Dry Run 报告",
    "",
    `- 项目：${state.project_dir}`,
    `- 状态：${state.overall_status}`,
    `- 报告就绪状态：${state.summary.report_readiness || "unknown"}`,
    `- 后续跟进路由：${state.summary.followup_route || "none"}`,
    `- 报告准入状态：${state.summary.report_gate}`,
    "",
    "## 阻塞项",
    "",
    markdownList(state.blockers),
    "## 警告",
    "",
    markdownList(state.warnings),
    "## 下一步动作",
    "",
    markdownList(state.next_actions),
    "## 已发现文件",
    "",
    markdownList(Object.entries(state.found_files).map(([key, value]) => `${key}: ${value || "缺失"}`)),
    "",
  ].join("\n");
}

function markdownList(items) {
  if (!items || items.length === 0) return "- 无\n";
  return items.map((item) => `- ${item}`).join("\n") + "\n";
}

function writeOutputs(outDir, state) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, "workflow_dry_run_status.json"), JSON.stringify(stripPrivate(state), null, 2) + "\n");
  fs.writeFileSync(path.join(outDir, "workflow_dry_run_report.md"), renderMarkdown(state));
}

function stripPrivate(state) {
  const clone = { ...state };
  delete clone._found;
  return clone;
}

function checkProject(projectDir, outDir = null) {
  const resolved = path.resolve(projectDir);
  const state = initState(resolved);

  validateRootFiles(state);
  const workflowState = readJson(state._found.workflowState, state, "workflow-state.json");
  const monitorStatus = readJson(state._found.monitorStatus, state, "monitor_status.json");
  const followupStrategy = readJson(state._found.followupStrategy, state, "followup_strategy.json");

  validateMonitorStatus(state, monitorStatus);
  validateFollowup(state, monitorStatus, followupStrategy, workflowState);
  validateReportGate(state, resolved, monitorStatus, followupStrategy);
  finalizeStatus(state);

  if (outDir) writeOutputs(path.resolve(outDir), state);
  return stripPrivate(state);
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2) + "\n");
}

function writeText(filePath, value = "") {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, value);
}

function baseProject(dir) {
  writeText(path.join(dir, "project-context.md"), "# 项目上下文\n");
  writeText(path.join(dir, "artifact-index.md"), "# 产物索引\n");
  writeText(path.join(dir, "decision-log.md"), "# 决策记录\n");
  writeText(path.join(dir, "assumption-log.md"), "# 假设记录\n");
  writeJson(path.join(dir, "workflow-state.json"), {
    project_id: "dry-run-test",
    current_stage: "collection_monitor",
    gate_status: "in_progress",
    followup_route: {
      route: null,
      allowed_routes: [...ALLOWED_ROUTES],
      owner_confirmed: false,
    },
    stages: [],
  });
}

function readyMonitor() {
  return {
    snapshot_time: "2026-05-11T00:00:00.000Z",
    report_readiness: "ready",
    sample_sufficiency: "met",
    cohorts: [
      {
        cohort_key: "all",
        business_label: "全部受访者",
        raw_n: 42,
        valid_n_conservative: 38,
        valid_n_strict: 35,
        minimum_valid_n: 30,
        status: "ready",
      },
    ],
    quality_risks: [],
    followup_recommendation_inputs: {},
  };
}

function notReadyMonitor() {
  return {
    snapshot_time: "2026-05-11T00:00:00.000Z",
    report_readiness: "not_ready",
    sample_sufficiency: "underfilled",
    cohorts: [
      {
        cohort_key: "all",
        business_label: "All respondents",
        raw_n: 12,
        valid_n_conservative: 9,
        valid_n_strict: 8,
        minimum_valid_n: 30,
        status: "underfilled",
      },
    ],
    quality_risks: ["样本量不足"],
    followup_recommendation_inputs: {
      completion_rate: 0.42,
    },
  };
}

function sampleActionStrategy() {
  return {
    status: "followup_needed",
    route: "sample_action",
    sample_action: "wait",
    diagnosis: "样本量不足，但尚未证明问卷或邮件链路存在明确瓶颈。",
    recommended_action: "等待 48 小时后重新运行 monitor。",
    owner_decisions_needed: ["确认等待窗口"],
    requires_new_wave: false,
    next_files_to_update: ["workflow-state.json"],
  };
}

function sampleStatusStrategy() {
  return {
    status: "followup_needed",
    route: "sample_status_memo",
    sample_action: null,
    diagnosis: "样本不足，且不建议继续 follow-up。",
    recommended_action: "只写 sample-status memo。",
    owner_decisions_needed: ["确认冻结报告"],
    requires_new_wave: false,
    next_files_to_update: ["sample-status.md"],
  };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function selfTest() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "research-workflow-dry-run-"));

  const readyDir = path.join(tmp, "ready");
  baseProject(readyDir);
  writeJson(path.join(readyDir, "monitoring/monitor_status.json"), readyMonitor());
  writeJson(path.join(readyDir, "monitoring/findings_data.json"), { metadata: { n_total: 38 }, questions: [] });
  writeText(path.join(readyDir, "follow-up-and-report/research-report.md"), "# 研究报告\n");
  const ready = checkProject(readyDir, path.join(readyDir, "dry-run"));
  assert(ready.overall_status === "pass", "ready project should pass");

  const followupDir = path.join(tmp, "followup");
  baseProject(followupDir);
  writeJson(path.join(followupDir, "monitoring/monitor_status.json"), notReadyMonitor());
  writeJson(path.join(followupDir, "follow-up-and-report/followup_strategy.json"), sampleActionStrategy());
  const followup = checkProject(followupDir, path.join(followupDir, "dry-run"));
  assert(followup.overall_status === "needs_action", "sample_action should need owner/action");
  assert(followup.summary.followup_route === "sample_action", "followup route should be sample_action");

  const sampleStatusDir = path.join(tmp, "sample-status");
  baseProject(sampleStatusDir);
  writeJson(path.join(sampleStatusDir, "monitoring/monitor_status.json"), notReadyMonitor());
  writeJson(path.join(sampleStatusDir, "follow-up-and-report/followup_strategy.json"), sampleStatusStrategy());
  writeText(path.join(sampleStatusDir, "follow-up-and-report/sample-status.md"), "# 样本状态\n");
  const sampleStatus = checkProject(sampleStatusDir, path.join(sampleStatusDir, "dry-run"));
  assert(sampleStatus.overall_status === "needs_action", "sample_status_memo without owner confirmation should need action");
  assert(sampleStatus.summary.report_gate === "sample_status_present", "sample status memo should be accepted when not ready");

  const blockedDir = path.join(tmp, "blocked");
  baseProject(blockedDir);
  writeJson(path.join(blockedDir, "monitoring/monitor_status.json"), notReadyMonitor());
  writeText(path.join(blockedDir, "follow-up-and-report/research-report.md"), "# 不应存在\n");
  const blocked = checkProject(blockedDir, path.join(blockedDir, "dry-run"));
  assert(blocked.overall_status === "block", "formal report with not_ready monitor should block");

  console.log("self-test passed");
}

function main() {
  const [command, ...rawArgs] = process.argv.slice(2);
  const options = parseOptions(rawArgs);

  if (command === "--help" || command === "-h" || command === "help" || options.help) {
    console.log(usage());
    return 0;
  }

  if (command === "self-test") {
    selfTest();
    return 0;
  }

  if (command === "check") {
    const projectDir = options.positional[0];
    if (!projectDir) throw new Error(usage());
    const state = checkProject(projectDir, options.outDir || null);
    console.log(JSON.stringify(state, null, 2));
    return state.overall_status === "block" ? 2 : 0;
  }

  throw new Error(usage());
}

try {
  process.exitCode = main();
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}

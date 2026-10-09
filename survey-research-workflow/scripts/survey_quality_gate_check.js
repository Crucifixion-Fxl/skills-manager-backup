#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const STATUS = new Set(["pass", "needs_fix", "blocked"]);
const EQUIVALENCE = new Set(["full", "partial", "none"]);
const PROVENANCE = new Set(["telemetry", "source_document", "voc", "interview", "usability_test", "external_research", "expert_hypothesis"]);
const OPTION_ORDER = new Set(["natural", "randomized", "rotated", "fixed"]);
const CRITICALITY = new Set(["core", "supporting"]);
const REGRESSION_STATUS = new Set(["preserved", "improved", "weakened", "replaced", "lost"]);
const HUMAN_PRETEST_STATUS = new Set(["not_run", "planned", "completed"]);
const WALKTHROUGH_STATUS = new Set(["pass", "needs_fix", "blocked", "not_required"]);

function usage() {
  return [
    "Usage:",
    "  node scripts/survey_quality_gate_check.js check <survey_quality_gate.json> [--out <derived-result.json>]",
    "  node scripts/survey_quality_gate_check.js self-test",
  ].join("\n");
}

function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function addUnique(list, message) {
  if (!list.includes(message)) list.push(message);
}

function severity(status) {
  if (status === "blocked") return 2;
  if (status === "needs_fix") return 1;
  return 0;
}

function maxStatus(statuses) {
  let result = "pass";
  for (const status of statuses) {
    if (severity(status) > severity(result)) result = status;
  }
  return result;
}

function validate(doc) {
  const result = {
    declared_status: doc.overall_status ?? null,
    derived_status: "pass",
    launch_ready_allowed: false,
    blockers: [],
    needs_fix: [],
    warnings: [],
  };
  const statuses = [];

  if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
    result.blockers.push("Root must be a JSON object.");
    result.derived_status = "blocked";
    return result;
  }

  if (doc.publication_mode !== "agent_publish") addUnique(result.blockers, "Machine survey quality gate is reserved for publication_mode=agent_publish.");
  if (!new Set(["C", "D"]).has(doc.risk_level)) addUnique(result.blockers, "Agent publish quality gate requires risk_level C or D.");

  for (const field of ["source_estimand_checks", "coverage_recovery_checks", "respondent_task_pairs", "platform_workarounds", "reason_option_sets", "closed_question_ids", "option_order_checks", "checks", "blockers"]) {
    if (!Array.isArray(doc[field])) addUnique(result.blockers, `${field} must be an array.`);
  }

  const sources = Array.isArray(doc.source_estimand_checks) ? doc.source_estimand_checks : [];
  for (const [index, item] of sources.entries()) {
    const label = `source_estimand_checks[${index}]`;
    if (!nonEmpty(item.source_signal_id)) addUnique(result.blockers, `${label}.source_signal_id is required.`);
    if (!nonEmpty(item.source_estimand)) addUnique(result.blockers, `${label}.source_estimand is required.`);
    if (!nonEmpty(item.new_estimand)) addUnique(result.blockers, `${label}.new_estimand is required.`);
    if (!EQUIVALENCE.has(item.equivalence)) addUnique(result.blockers, `${label}.equivalence must be full | partial | none.`);

    if (item.equivalence === "full") {
      for (const field of ["population_equivalent", "denominator_equivalent", "estimand_equivalent", "evidence_strength_not_weaker"]) {
        if (item[field] !== true) {
          addUnique(result.blockers, `${label} cannot be full because ${field} is not true.`);
        }
      }
    }

    if ((item.equivalence === "partial" || item.equivalence === "none") && item.owner_confirmation !== "confirmed") {
      addUnique(result.blockers, `${label} is ${item.equivalence} without owner confirmation.`);
    }

    if (!STATUS.has(item.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(item.status);
    }
  }

  const recoveryChecks = Array.isArray(doc.coverage_recovery_checks) ? doc.coverage_recovery_checks : [];
  const recoveryBySignal = new Map();
  for (const [index, item] of recoveryChecks.entries()) {
    const label = `coverage_recovery_checks[${index}]`;
    if (!nonEmpty(item.source_signal_id)) addUnique(result.blockers, `${label}.source_signal_id is required.`);
    else if (recoveryBySignal.has(item.source_signal_id)) addUnique(result.blockers, `${label}.source_signal_id is duplicated.`);
    else recoveryBySignal.set(item.source_signal_id, item);
    if (!CRITICALITY.has(item.decision_criticality)) addUnique(result.blockers, `${label}.decision_criticality must be core | supporting.`);
    if (!REGRESSION_STATUS.has(item.regression_status)) addUnique(result.blockers, `${label}.regression_status is invalid.`);
    if (!nonEmpty(item.required_action_output)) addUnique(result.blockers, `${label}.required_action_output is required.`);
    if (!Array.isArray(item.candidate_carriers_considered) || item.candidate_carriers_considered.length < 1) {
      addUnique(result.blockers, `${label}.candidate_carriers_considered must contain at least one carrier.`);
    }
    if (!nonEmpty(item.selected_carrier)) addUnique(result.blockers, `${label}.selected_carrier is required.`);
    if (typeof item.estimated_percent_shown !== "number" || item.estimated_percent_shown < 0 || item.estimated_percent_shown > 100) {
      addUnique(result.blockers, `${label}.estimated_percent_shown must be between 0 and 100.`);
    }
    if (typeof item.seconds_when_shown !== "number" || item.seconds_when_shown < 0) {
      addUnique(result.blockers, `${label}.seconds_when_shown must be a non-negative number.`);
    }
    if (typeof item.mobile_option_scan_lines !== "number" || item.mobile_option_scan_lines < 0) {
      addUnique(result.blockers, `${label}.mobile_option_scan_lines must be a non-negative number.`);
    }
    if (["preserved", "improved", "replaced"].includes(item.regression_status) && item.action_granularity_preserved !== true) {
      addUnique(result.blockers, `${label} claims ${item.regression_status} without preserving required action granularity.`);
    }
    if (
      item.decision_criticality === "core" &&
      ["weakened", "lost"].includes(item.regression_status) &&
      item.owner_confirmation !== "confirmed"
    ) {
      addUnique(result.blockers, `${label} leaves a core signal ${item.regression_status} without owner confirmation.`);
    }
    if (!STATUS.has(item.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(item.status);
    }
  }
  for (const item of sources) {
    if (nonEmpty(item.source_signal_id) && !recoveryBySignal.has(item.source_signal_id)) {
      addUnique(result.blockers, `Source signal ${item.source_signal_id} is missing a coverage recovery check.`);
    }
  }

  const pairs = Array.isArray(doc.respondent_task_pairs) ? doc.respondent_task_pairs : [];
  for (const [index, pair] of pairs.entries()) {
    const label = `respondent_task_pairs[${index}]`;
    if (typeof pair.shared_response_concept_ratio !== "number" || pair.shared_response_concept_ratio < 0 || pair.shared_response_concept_ratio > 1) {
      addUnique(result.blockers, `${label}.shared_response_concept_ratio must be between 0 and 1.`);
      continue;
    }

    if (pair.shared_response_concept_ratio >= 0.5) {
      for (const field of ["respondent_task_delta", "incremental_decision_value", "lower_burden_alternative"]) {
        if (!nonEmpty(pair[field])) addUnique(result.blockers, `${label}.${field} is required when concept overlap is >= 0.5.`);
      }
      if (pair.lower_burden_equivalent_available === true && pair.selected_lower_burden_design !== true) {
        addUnique(result.blockers, `${label} rejected an equivalent lower-burden design.`);
      }
      if (
        pair.same_respondent_task === true &&
        pair.second_question_required === true &&
        pair.shown_only_to_eligible !== true &&
        pair.owner_confirmation !== "confirmed"
      ) {
        addUnique(result.blockers, `${label} repeats the same respondent task outside the eligible cohort without owner confirmation.`);
      }
    }
    if (!STATUS.has(pair.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(pair.status);
    }
  }

  const workarounds = Array.isArray(doc.platform_workarounds) ? doc.platform_workarounds : [];
  for (const [index, item] of workarounds.entries()) {
    const label = `platform_workarounds[${index}]`;
    if (!nonEmpty(item.requirement)) addUnique(result.blockers, `${label}.requirement is required.`);
    if (item.adds_respondent_burden === true && item.owner_confirmation !== "confirmed") {
      addUnique(result.blockers, `${label} adds respondent burden without owner confirmation.`);
    }
    if (!STATUS.has(item.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(item.status);
    }
  }

  const delivery = doc.stakeholder_delivery;
  if (!delivery || typeof delivery !== "object" || Array.isArray(delivery)) {
    addUnique(result.blockers, "stakeholder_delivery object is required.");
  } else {
    for (const field of ["coverage_summary_first", "plain_language_main", "respondent_copy_separated", "methods_in_appendix"]) {
      if (delivery[field] !== true) addUnique(result.needs_fix, `stakeholder_delivery.${field} must be true before stakeholder-ready delivery.`);
    }
    if (!STATUS.has(delivery.status)) {
      addUnique(result.blockers, "stakeholder_delivery.status must be pass | needs_fix | blocked.");
    } else {
      statuses.push(delivery.status);
    }
  }

  const agentPretest = doc.agent_pretest;
  if (!agentPretest || typeof agentPretest !== "object" || Array.isArray(agentPretest)) {
    addUnique(result.blockers, "agent_pretest object is required.");
  } else {
    if (agentPretest.evidence_class !== "synthetic_cognitive_walkthrough") {
      addUnique(result.blockers, "agent_pretest.evidence_class must be synthetic_cognitive_walkthrough.");
    }
    if (!Number.isInteger(agentPretest.profile_count) || agentPretest.profile_count < 0) {
      addUnique(result.blockers, "agent_pretest.profile_count must be a non-negative integer.");
    }
    for (const field of ["critical_segments_covered", "boundary_profile_included", "counter_assumption_profile_included"]) {
      if (typeof agentPretest[field] !== "boolean") addUnique(result.blockers, `agent_pretest.${field} must be boolean.`);
    }
    const walkthroughStatuses = [];
    for (const field of ["cognitive_walkthrough_status", "path_walkthrough_status", "mobile_walkthrough_status"]) {
      if (!WALKTHROUGH_STATUS.has(agentPretest[field])) addUnique(result.blockers, `agent_pretest.${field} must be pass | needs_fix | blocked | not_required.`);
      else if (agentPretest[field] !== "not_required") walkthroughStatuses.push(agentPretest[field]);
    }
    if (!HUMAN_PRETEST_STATUS.has(agentPretest.human_pretest_status)) {
      addUnique(result.blockers, "agent_pretest.human_pretest_status must be not_run | planned | completed.");
    }
    if (!Array.isArray(agentPretest.high_risk_triggers)) addUnique(result.blockers, "agent_pretest.high_risk_triggers must be an array.");
    if (!Array.isArray(agentPretest.residual_risks)) addUnique(result.blockers, "agent_pretest.residual_risks must be an array.");
    const expectedPretestStatus = maxStatus(walkthroughStatuses);
    if (!STATUS.has(agentPretest.status)) {
      addUnique(result.blockers, "agent_pretest.status must be pass | needs_fix | blocked.");
    } else {
      statuses.push(agentPretest.status);
      if (walkthroughStatuses.length === 3 && agentPretest.status !== expectedPretestStatus) {
        addUnique(result.blockers, `agent_pretest.status (${agentPretest.status}) does not match walkthrough status (${expectedPretestStatus}).`);
      }
      if (agentPretest.status === "pass") {
        if (!nonEmpty(agentPretest.instrument_version)) addUnique(result.blockers, "Passing agent_pretest requires instrument_version.");
        if (!nonEmpty(agentPretest.report_path)) addUnique(result.blockers, "Passing agent_pretest requires report_path.");
        if (!Number.isInteger(agentPretest.profile_count) || agentPretest.profile_count < 3) {
          addUnique(result.blockers, "Passing agent_pretest requires at least 3 profiles.");
        }
        if (!nonEmpty(agentPretest.profile_coverage_rationale)) addUnique(result.blockers, "Passing agent_pretest requires profile_coverage_rationale.");
        for (const field of ["critical_segments_covered", "boundary_profile_included", "counter_assumption_profile_included"]) {
          if (agentPretest[field] !== true) addUnique(result.blockers, `Passing agent_pretest requires ${field}=true.`);
        }
      }
    }
    const hasResidualRisk = (Array.isArray(agentPretest.high_risk_triggers) && agentPretest.high_risk_triggers.length > 0) ||
      (Array.isArray(agentPretest.residual_risks) && agentPretest.residual_risks.length > 0);
    if (agentPretest.human_pretest_status !== "completed" && hasResidualRisk && agentPretest.owner_accepted_residual_risk !== true) {
      if (agentPretest.require_residual_risk_resolution === true) {
        addUnique(result.needs_fix, "Synthetic pretest has residual or high-risk conditions without owner acceptance or completed human pretest.");
        statuses.push("needs_fix");
      } else addUnique(result.warnings, "Human pretest was not run; residual risks are recorded for transparency and do not block launch.");
    }
  }

  const reasonSets = Array.isArray(doc.reason_option_sets) ? doc.reason_option_sets : [];
  for (const [setIndex, set] of reasonSets.entries()) {
    const label = `reason_option_sets[${setIndex}]`;
    if (!nonEmpty(set.question_id)) addUnique(result.blockers, `${label}.question_id is required.`);
    if (typeof set.substantive_option_count !== "number" || set.substantive_option_count < 1) {
      addUnique(result.blockers, `${label}.substantive_option_count must be a positive number.`);
    }
    if (!Array.isArray(set.causal_layers) || set.causal_layers.length < 1) {
      addUnique(result.blockers, `${label}.causal_layers must be a non-empty array.`);
    }
    const triggersProgressive = set.substantive_option_count > 6 || (Array.isArray(set.causal_layers) && new Set(set.causal_layers).size > 1) || set.parent_child_competition === true;
    const exceptionConfirmed = set.exception?.owner_confirmation === "confirmed" && nonEmpty(set.exception?.rationale);
    if (triggersProgressive && set.uses_progressive_diagnosis !== true && !exceptionConfirmed) {
      addUnique(result.blockers, `${label} requires progressive diagnosis or an owner-confirmed exception.`);
    }
    if (!Array.isArray(set.options) || set.options.length < 1) {
      addUnique(result.blockers, `${label}.options must be a non-empty array.`);
    } else {
      const actionBuckets = new Map();
      for (const [optionIndex, option] of set.options.entries()) {
        const optionLabel = `${label}.options[${optionIndex}]`;
        for (const field of ["option_id", "respondent_label", "causal_layer", "evidence_reference", "action_bucket", "action_if_high", "distinct_from_neighbor"]) {
          if (!nonEmpty(option[field])) addUnique(result.blockers, `${optionLabel}.${field} is required.`);
        }
        if (!PROVENANCE.has(option.provenance_type)) {
          addUnique(result.blockers, `${optionLabel}.provenance_type is invalid.`);
        }
        if (option.common_reason_claimed === true && (option.provenance_type === "expert_hypothesis" || !nonEmpty(option.commonness_evidence))) {
          addUnique(result.blockers, `${optionLabel} claims a common reason without qualifying evidence.`);
        }
        if (nonEmpty(option.action_bucket)) {
          const existing = actionBuckets.get(option.action_bucket) ?? [];
          existing.push(option.option_id ?? String(optionIndex));
          actionBuckets.set(option.action_bucket, existing);
        }
        if (!STATUS.has(option.status)) {
          addUnique(result.blockers, `${optionLabel}.status must be pass | needs_fix | blocked.`);
        } else {
          statuses.push(option.status);
        }
      }
      for (const [bucket, optionIds] of actionBuckets.entries()) {
        if (optionIds.length > 1 && !nonEmpty(set.same_action_distinction_rationale?.[bucket])) {
          addUnique(result.needs_fix, `${label} has multiple options in action bucket ${bucket} without a distinction rationale.`);
          statuses.push("needs_fix");
        }
      }
    }
    if (!STATUS.has(set.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(set.status);
    }
  }

  const orderChecks = Array.isArray(doc.option_order_checks) ? doc.option_order_checks : [];
  const closedQuestionIds = Array.isArray(doc.closed_question_ids) ? doc.closed_question_ids : [];
  const declaredQuestionIds = new Set();
  for (const [index, item] of orderChecks.entries()) {
    const label = `option_order_checks[${index}]`;
    if (!nonEmpty(item.question_id)) addUnique(result.blockers, `${label}.question_id is required.`);
    else if (declaredQuestionIds.has(item.question_id)) addUnique(result.blockers, `${label}.question_id is duplicated.`);
    else declaredQuestionIds.add(item.question_id);
    if (typeof item.has_natural_order !== "boolean") addUnique(result.blockers, `${label}.has_natural_order must be boolean.`);
    if (!OPTION_ORDER.has(item.strategy)) addUnique(result.blockers, `${label}.strategy must be natural | randomized | rotated | fixed.`);
    if (!Array.isArray(item.semantic_anchor_option_ids)) addUnique(result.blockers, `${label}.semantic_anchor_option_ids must be an array.`);
    if (!Array.isArray(item.fixed_option_ids)) addUnique(result.blockers, `${label}.fixed_option_ids must be an array.`);
    if (item.has_natural_order === true && item.strategy !== "natural") {
      addUnique(result.blockers, `${label} has natural order and must use the natural strategy.`);
    }
    if (item.has_natural_order === false && item.strategy === "fixed" && !nonEmpty(item.rationale)) {
      addUnique(result.blockers, `${label} fixes an unordered option list without a rationale.`);
    }
    if (item.strategy === "randomized" || item.strategy === "rotated") {
      const fixed = new Set(Array.isArray(item.fixed_option_ids) ? item.fixed_option_ids : []);
      for (const optionId of Array.isArray(item.semantic_anchor_option_ids) ? item.semantic_anchor_option_ids : []) {
        if (!fixed.has(optionId)) addUnique(result.blockers, `${label} must keep semantic anchor ${optionId} fixed.`);
      }
    }
    if (!STATUS.has(item.status)) {
      addUnique(result.blockers, `${label}.status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(item.status);
    }
  }
  for (const questionId of closedQuestionIds) {
    if (!nonEmpty(questionId)) addUnique(result.blockers, "closed_question_ids must contain only non-empty strings.");
    else if (!declaredQuestionIds.has(questionId)) addUnique(result.blockers, `Closed question ${questionId} is missing an option order check.`);
  }
  const closedQuestionIdSet = new Set(closedQuestionIds.filter(nonEmpty));
  for (const questionId of declaredQuestionIds) {
    if (!closedQuestionIdSet.has(questionId)) addUnique(result.blockers, `Option order check ${questionId} does not match a declared closed question.`);
  }

  const checks = Array.isArray(doc.checks) ? doc.checks : [];
  for (const [index, check] of checks.entries()) {
    if (!STATUS.has(check.status)) {
      addUnique(result.blockers, `checks[${index}].status must be pass | needs_fix | blocked.`);
    } else {
      statuses.push(check.status);
    }
  }

  if (doc.burden && typeof doc.burden === "object") {
    if (!STATUS.has(doc.burden.status)) {
      addUnique(result.blockers, "burden.status must be pass | needs_fix | blocked.");
    } else {
      statuses.push(doc.burden.status);
    }
    const longest = doc.burden.longest_path_minutes;
    const accepted = doc.burden.owner_accepted_longest_minutes;
    if (typeof longest === "number" && typeof accepted === "number" && longest > accepted && doc.burden.owner_confirmed !== true) {
      addUnique(result.blockers, "Longest path exceeds the owner-accepted burden without renewed confirmation.");
    }
    const promised = doc.burden.promised_minutes;
    const typical = doc.burden.typical_path_minutes;
    if (typeof promised === "number" && typeof typical === "number" && typical > promised) {
      addUnique(result.needs_fix, "Typical path exceeds the respondent-facing time promise.");
      statuses.push("needs_fix");
    }
  } else {
    addUnique(result.blockers, "burden object is required.");
  }

  if (doc.architecture?.owner_confirmed !== true) {
    addUnique(result.needs_fix, "Architecture approval is not confirmed.");
    statuses.push("needs_fix");
  }
  if (doc.burden?.owner_confirmed !== true) {
    addUnique(result.needs_fix, "Burden approval is not confirmed separately from architecture.");
    statuses.push("needs_fix");
  }

  if (Array.isArray(doc.blockers) && doc.blockers.length > 0) {
    addUnique(result.blockers, "Declared blockers are not empty.");
  }

  if (result.blockers.length > 0) statuses.push("blocked");
  else if (result.needs_fix.length > 0) statuses.push("needs_fix");

  result.derived_status = maxStatus(statuses);

  if (!STATUS.has(doc.overall_status)) {
    addUnique(result.blockers, "overall_status must be pass | needs_fix | blocked.");
    result.derived_status = "blocked";
  } else if (doc.overall_status !== result.derived_status) {
    addUnique(result.blockers, `Declared overall_status (${doc.overall_status}) does not match derived status (${result.derived_status}).`);
    result.derived_status = "blocked";
  }

  const approvalsReady = doc.architecture?.owner_confirmed === true && doc.burden?.owner_confirmed === true;
  result.launch_ready_allowed = result.derived_status === "pass" && approvalsReady;
  if (doc.launch_ready === true && !result.launch_ready_allowed) {
    addUnique(result.blockers, "launch_ready cannot be true while status or owner approvals are incomplete.");
    result.derived_status = "blocked";
    result.launch_ready_allowed = false;
  }

  return result;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(path.resolve(filePath)), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function selfTest() {
  const passing = {
    publication_mode: "agent_publish",
    risk_level: "C",
    source_estimand_checks: [{
      source_signal_id: "primary_channel",
      source_estimand: "primary_share",
      new_estimand: "primary_share",
      equivalence: "full",
      population_equivalent: true,
      denominator_equivalent: true,
      estimand_equivalent: true,
      evidence_strength_not_weaker: true,
      status: "pass",
    }],
    coverage_recovery_checks: [{
      source_signal_id: "primary_channel",
      decision_criticality: "core",
      regression_status: "preserved",
      required_action_output: "Choose which channel integration to prioritize.",
      action_granularity_preserved: true,
      candidate_carriers_considered: ["recent-event single choice", "all-user channel checklist"],
      selected_carrier: "recent-event single choice",
      estimated_percent_shown: 35,
      seconds_when_shown: 12,
      mobile_option_scan_lines: 6,
      owner_confirmation: "not_needed",
      status: "pass",
    }],
    respondent_task_pairs: [{
      shared_response_concept_ratio: 0.6,
      same_respondent_task: false,
      second_question_required: true,
      shown_only_to_eligible: true,
      respondent_task_delta: "Select one primary channel for the recalled event.",
      incremental_decision_value: "Primary-channel share for integration priority.",
      lower_burden_alternative: "Use routing from the event screener.",
      lower_burden_equivalent_available: true,
      selected_lower_burden_design: true,
      owner_confirmation: "not_needed",
      status: "pass",
    }],
    platform_workarounds: [{
      requirement: "page skip logic",
      adds_respondent_burden: false,
      owner_confirmation: "not_needed",
      status: "pass",
    }],
    stakeholder_delivery: {
      coverage_summary_first: true,
      plain_language_main: true,
      respondent_copy_separated: true,
      methods_in_appendix: true,
      status: "pass",
    },
    agent_pretest: {
      evidence_class: "synthetic_cognitive_walkthrough",
      instrument_version: "survey-v1",
      report_path: "research-design/agent_pretest_report.md",
      profile_count: 6,
      profile_coverage_rationale: "Covers the typical, counter-assumption, boundary, and every decision-changing routed segment.",
      critical_segments_covered: true,
      boundary_profile_included: true,
      counter_assumption_profile_included: true,
      cognitive_walkthrough_status: "pass",
      path_walkthrough_status: "pass",
      mobile_walkthrough_status: "not_required",
      human_pretest_status: "not_run",
      high_risk_triggers: [],
      residual_risks: [],
      owner_accepted_residual_risk: false,
      status: "pass",
    },
    reason_option_sets: [{
      question_id: "stop_reason",
      substantive_option_count: 4,
      causal_layers: ["proximal_mechanism"],
      parent_child_competition: false,
      uses_progressive_diagnosis: true,
      options: [{
        option_id: "enough",
        respondent_label: "I had seen enough for then",
        causal_layer: "proximal_mechanism",
        provenance_type: "source_document",
        evidence_reference: "source-1",
        common_reason_claimed: false,
        commonness_evidence: "",
        action_bucket: "occasion_fit",
        action_if_high: "Do not treat a natural stopping point as a content failure.",
        distinct_from_neighbor: "Separates satiation from content failure.",
        status: "pass",
      }],
      same_action_distinction_rationale: {},
      status: "pass",
    }],
    closed_question_ids: ["stop_reason"],
    option_order_checks: [{
      question_id: "stop_reason",
      has_natural_order: false,
      strategy: "randomized",
      semantic_anchor_option_ids: ["other"],
      fixed_option_ids: ["other"],
      rationale: "Randomize substantive reasons and keep Other last.",
      status: "pass",
    }],
    checks: [{ status: "pass" }],
    architecture: { owner_confirmed: true },
    burden: {
      promised_minutes: 5,
      typical_path_minutes: 5,
      longest_path_minutes: 6,
      owner_accepted_longest_minutes: 6,
      owner_confirmed: true,
      status: "pass",
    },
    blockers: [],
    overall_status: "pass",
    launch_ready: true,
  };

  const clone = () => JSON.parse(JSON.stringify(passing));
  const cases = [];

  const coverageToTopChoice = clone();
  Object.assign(coverageToTopChoice.source_estimand_checks[0], {
    source_estimand: "item_incidence",
    new_estimand: "top_choice",
    estimand_equivalent: false,
  });
  cases.push(["coverage-to-top-choice", coverageToTopChoice]);

  const repeatedTaxonomy = clone();
  Object.assign(repeatedTaxonomy.respondent_task_pairs[0], {
    same_respondent_task: true,
    shown_only_to_eligible: false,
    selected_lower_burden_design: false,
    owner_confirmation: "pending",
  });
  cases.push(["repeated-event-taxonomy", repeatedTaxonomy]);

  const actionGranularityLoss = clone();
  Object.assign(actionGranularityLoss.coverage_recovery_checks[0], {
    regression_status: "replaced",
    action_granularity_preserved: false,
  });
  cases.push(["action-granularity-loss", actionGranularityLoss]);

  const unconfirmedCoreLoss = clone();
  Object.assign(unconfirmedCoreLoss.coverage_recovery_checks[0], {
    regression_status: "lost",
    action_granularity_preserved: false,
    owner_confirmation: "pending",
  });
  cases.push(["unconfirmed-core-loss", unconfirmedCoreLoss]);

  const missingRecoveryLoop = clone();
  missingRecoveryLoop.coverage_recovery_checks = [];
  cases.push(["missing-coverage-recovery-loop", missingRecoveryLoop]);

  const universalWorkaround = clone();
  Object.assign(universalWorkaround.platform_workarounds[0], {
    adds_respondent_burden: true,
    owner_confirmation: "pending",
  });
  cases.push(["unknown-platform-universal-workaround", universalWorkaround]);

  const passWithBlocker = clone();
  passWithBlocker.checks[0].status = "blocked";
  cases.push(["pass-with-blocker", passWithBlocker]);

  const architectureOverreach = clone();
  architectureOverreach.burden.longest_path_minutes = 9;
  architectureOverreach.burden.owner_confirmed = false;
  cases.push(["architecture-approval-overreach", architectureOverreach]);

  const unsupportedCommonReason = clone();
  Object.assign(unsupportedCommonReason.reason_option_sets[0].options[0], {
    provenance_type: "expert_hypothesis",
    common_reason_claimed: true,
  });
  cases.push(["unsupported-common-reason", unsupportedCommonReason]);

  const flatMixedReasonList = clone();
  Object.assign(flatMixedReasonList.reason_option_sets[0], {
    substantive_option_count: 8,
    causal_layers: ["event_constraint", "content_manifestation"],
    uses_progressive_diagnosis: false,
  });
  cases.push(["flat-mixed-reason-list", flatMixedReasonList]);

  const randomizedOrderedScale = clone();
  Object.assign(randomizedOrderedScale.option_order_checks[0], {
    has_natural_order: true,
    strategy: "randomized",
  });
  cases.push(["randomized-ordered-scale", randomizedOrderedScale]);

  const randomizedSemanticAnchor = clone();
  randomizedSemanticAnchor.option_order_checks[0].fixed_option_ids = [];
  cases.push(["randomized-semantic-anchor", randomizedSemanticAnchor]);

  const unjustifiedFixedList = clone();
  Object.assign(unjustifiedFixedList.option_order_checks[0], {
    strategy: "fixed",
    rationale: "",
  });
  cases.push(["unjustified-fixed-list", unjustifiedFixedList]);

  const missingOrderCheck = clone();
  missingOrderCheck.option_order_checks = [];
  cases.push(["missing-option-order-check", missingOrderCheck]);

  const jargonFirstDelivery = clone();
  jargonFirstDelivery.stakeholder_delivery.coverage_summary_first = false;
  jargonFirstDelivery.stakeholder_delivery.status = "needs_fix";
  jargonFirstDelivery.overall_status = "needs_fix";
  jargonFirstDelivery.launch_ready = false;
  cases.push(["jargon-first-delivery", jargonFirstDelivery, "needs_fix"]);

  const incompleteSyntheticPretest = clone();
  incompleteSyntheticPretest.agent_pretest.profile_count = 2;
  cases.push(["incomplete-synthetic-pretest", incompleteSyntheticPretest]);

  const unacceptedResidualRisk = clone();
  unacceptedResidualRisk.agent_pretest.residual_risks = ["Target-language interpretation has not been observed with real users."];
  cases.push(["unaccepted-pretest-residual-risk-warning", unacceptedResidualRisk, "pass"]);

  const enforcedResidualRisk = clone();
  enforcedResidualRisk.agent_pretest.residual_risks = ["High-risk interpretation is unresolved."];
  enforcedResidualRisk.agent_pretest.require_residual_risk_resolution = true;
  enforcedResidualRisk.overall_status = "needs_fix";
  enforcedResidualRisk.launch_ready = false;
  cases.push(["enforced-pretest-residual-risk", enforcedResidualRisk, "needs_fix"]);

  for (const [name, doc, expected = "blocked"] of cases) {
    const caseResult = validate(doc);
    if (caseResult.derived_status !== expected) throw new Error(`${name} expected ${expected}, got ${caseResult.derived_status}.`);
  }

  const goodResult = validate(passing);
  if (goodResult.derived_status !== "pass" || goodResult.launch_ready_allowed !== true) {
    throw new Error("Good regression case did not pass.");
  }
  process.stdout.write("survey_quality_gate_check self-test: pass\n");
}

function main(argv) {
  const [command, filePath, ...rest] = argv;
  if (command === "--help" || command === "-h" || command === "help") {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (command === "self-test") {
    selfTest();
    return;
  }
  if (command !== "check" || !filePath) {
    process.stderr.write(`${usage()}\n`);
    process.exitCode = 2;
    return;
  }

  try {
    const result = validate(readJson(filePath));
    result.schema_version = "1.0";
    result.generated_by = "survey_quality_gate_check.js";
    result.source_file_sha256 = crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
    const outIndex = rest.indexOf("--out");
    if (outIndex >= 0) {
      const outPath = rest[outIndex + 1];
      if (!outPath || outPath.startsWith("--")) throw new Error("--out requires a file path.");
      writeJson(outPath, result);
    }
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.derived_status !== "pass") process.exitCode = 1;
  } catch (error) {
    process.stderr.write(`survey_quality_gate_check failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}

if (require.main === module) main(process.argv.slice(2));

module.exports = { validate };

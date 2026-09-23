#!/usr/bin/env node

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const SCRIPT_ID = "survey_preflight.js";
const MAX_SOLUTIONS = 128;

function usage() {
  return [
    "Usage:",
    "  node scripts/survey_preflight.js --help",
    "  node scripts/survey_preflight.js generate-typeform-paths <payload.json> --out <path-plan.json>",
    "  node scripts/survey_preflight.js verify-typeform-version <payload.json> <readback.json> --form-id <id> --out <identity.json>",
    "  node scripts/survey_preflight.js verify-typeform-responses <expectations.json> <responses.json> --identity <identity.json> --out <result.json>",
    "  node scripts/survey_preflight.js build-typeform-gate <evidence-manifest.json> --out <launch-gate.json>",
    "  node scripts/survey_preflight.js self-test",
  ].join("\n");
}

function parseArgs(argv) {
  const options = { positional: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      options.positional.push(arg);
      continue;
    }
    const key = arg.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) options[key] = true;
    else {
      options[key] = next;
      i += 1;
    }
  }
  return options;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(path.resolve(filePath)), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
  }
  return value;
}

function hashJson(value) {
  return crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

function hashFile(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function flattenFields(fields, out = []) {
  for (const field of Array.isArray(fields) ? fields : []) {
    if (!field || typeof field !== "object") continue;
    out.push(field);
    if (Array.isArray(field.properties?.fields)) flattenFields(field.properties.fields, out);
  }
  return out;
}

function fieldContext(payload) {
  return new Map(flattenFields(payload.fields).filter((field) => nonEmpty(field.ref)).map((field) => [field.ref, field]));
}

function fieldAndOperand(condition) {
  const vars = Array.isArray(condition?.vars) ? condition.vars : [];
  const fieldVar = vars.find((item) => item?.type === "field");
  const operand = vars.find((item) => item !== fieldVar);
  return { fieldRef: fieldVar?.value, operand };
}

function alternativesForAtomic(condition, desired, fields) {
  const op = condition?.op;
  if (op === "always") return desired ? [{}] : [];
  const { fieldRef, operand } = fieldAndOperand(condition);
  if (!nonEmpty(fieldRef) || !operand) return [];
  const field = fields.get(fieldRef);
  if (!field) return [];
  const choices = (field.properties?.choices || []).map((item) => item.ref).filter(nonEmpty);
  const expected = operand.value;
  const invert = new Set(["is_not", "not_contains", "not_equal"]).has(op);
  const wantPositive = invert ? !desired : desired;

  if (["is", "is_not", "contains", "not_contains"].includes(op) && operand.type === "choice") {
    if (wantPositive) return [{ [fieldRef]: { kind: "choices", values: [expected] } }];
    const alternative = choices.find((choice) => choice !== expected);
    return [{ [fieldRef]: { kind: "choices", values: alternative ? [alternative] : [] } }];
  }
  if (["equal", "not_equal"].includes(op)) {
    const alternative = typeof expected === "number" ? expected + 1 : `${expected}__different`;
    return [{ [fieldRef]: { kind: "value", value: wantPositive ? expected : alternative } }];
  }
  if (op === "lower_than" && typeof expected === "number") {
    return [{ [fieldRef]: { kind: "value", value: desired ? expected - 1 : expected } }];
  }
  if (op === "greater_than" && typeof expected === "number") {
    return [{ [fieldRef]: { kind: "value", value: desired ? expected + 1 : expected } }];
  }
  return [];
}

function mergeAssignments(left, right) {
  const merged = clone(left);
  for (const [fieldRef, value] of Object.entries(right)) {
    const current = merged[fieldRef];
    if (!current) {
      merged[fieldRef] = clone(value);
      continue;
    }
    if (current.kind !== value.kind) return null;
    if (current.kind === "value" && current.value !== value.value) return null;
    if (current.kind === "choices") {
      const union = [...new Set([...current.values, ...value.values])].sort();
      merged[fieldRef] = { kind: "choices", values: union };
    }
  }
  return merged;
}

function overlayAssignments(base, override) {
  return { ...clone(base), ...clone(override) };
}

function assignmentsValid(assignments, fields) {
  for (const [fieldRef, assignment] of Object.entries(assignments)) {
    if (assignment.kind === "choices" && assignment.values.length > 1 && fields.get(fieldRef)?.properties?.allow_multiple_selection !== true) return false;
  }
  return true;
}

function combine(solutionSets) {
  let accumulated = [{}];
  for (const solutions of solutionSets) {
    const next = [];
    for (const left of accumulated) {
      for (const right of solutions) {
        const merged = mergeAssignments(left, right);
        if (merged) next.push(merged);
        if (next.length >= MAX_SOLUTIONS) break;
      }
      if (next.length >= MAX_SOLUTIONS) break;
    }
    accumulated = next;
    if (accumulated.length === 0) break;
  }
  return accumulated;
}

function conditionAlternatives(condition, desired, fields) {
  if (!condition || typeof condition !== "object") return [];
  if (condition.op === "and") {
    if (desired) return combine((condition.vars || []).map((child) => conditionAlternatives(child, true, fields)));
    return (condition.vars || []).flatMap((child) => conditionAlternatives(child, false, fields)).slice(0, MAX_SOLUTIONS);
  }
  if (condition.op === "or") {
    if (desired) return (condition.vars || []).flatMap((child) => conditionAlternatives(child, true, fields)).slice(0, MAX_SOLUTIONS);
    return combine((condition.vars || []).map((child) => conditionAlternatives(child, false, fields)));
  }
  return alternativesForAtomic(condition, desired, fields);
}

function assignedValue(assignments, fieldRef) {
  return assignments[fieldRef];
}

function evaluateCondition(condition, assignments) {
  if (!condition || typeof condition !== "object") return false;
  if (condition.op === "always") return true;
  if (condition.op === "and") return (condition.vars || []).every((child) => evaluateCondition(child, assignments));
  if (condition.op === "or") return (condition.vars || []).some((child) => evaluateCondition(child, assignments));
  const { fieldRef, operand } = fieldAndOperand(condition);
  const assigned = assignedValue(assignments, fieldRef);
  if (!assigned || !operand) return false;
  const expected = operand.value;
  const selected = assigned.kind === "choices" ? assigned.values : [];
  const actual = assigned.kind === "value" ? assigned.value : selected[0];
  if (condition.op === "is") return selected.includes(expected);
  if (condition.op === "is_not") return !selected.includes(expected);
  if (condition.op === "contains") return selected.includes(expected);
  if (condition.op === "not_contains") return !selected.includes(expected);
  if (condition.op === "equal") return actual === expected;
  if (condition.op === "not_equal") return actual !== expected;
  if (condition.op === "lower_than") return actual < expected;
  if (condition.op === "greater_than") return actual > expected;
  return false;
}

function representativeAssignments(field) {
  const choices = (field?.properties?.choices || []).map((item) => item.ref).filter(nonEmpty);
  if (choices.length > 0) return choices.map((value) => ({ [field.ref]: { kind: "choices", values: [value] } }));
  return [{ [field.ref]: { kind: "value", value: "__representative__" } }];
}

function ruleOutcomes(rule, fields) {
  const outcomes = [];
  const field = fields.get(rule.ref);
  function withCurrentAnswer(assignment) {
    if (assignment[rule.ref] || field?.type === "statement") return assignment;
    return mergeAssignments(assignment, representativeAssignments(field)[0]) || assignment;
  }
  for (let actionIndex = 0; actionIndex < (rule.actions || []).length; actionIndex += 1) {
    const action = rule.actions[actionIndex];
    const solutions = conditionAlternatives(action.condition, true, fields)
      .filter((assignment) => assignmentsValid(assignment, fields))
      .filter((assignment) => rule.actions.slice(0, actionIndex).every((prior) => !evaluateCondition(prior.condition, assignment)));
    for (const assignment of solutions.slice(0, 8)) {
      outcomes.push({ key: `logic:${rule.ref}:action:${actionIndex}`, rule_ref: rule.ref, assignment: withCurrentAnswer(assignment), target: action.details?.to });
    }
  }
  const fallthrough = representativeAssignments(field)
    .filter((assignment) => (rule.actions || []).every((action) => !evaluateCondition(action.condition, assignment)));
  for (const assignment of fallthrough.slice(0, 1)) outcomes.push({ key: `logic:${rule.ref}:default`, rule_ref: rule.ref, assignment, target: null });
  return outcomes;
}

function enumeratePaths(payload) {
  const orderedFields = flattenFields(payload.fields);
  const fields = fieldContext(payload);
  const indexByRef = new Map(orderedFields.map((field, index) => [field.ref, index]));
  const ruleByRef = new Map((payload.logic || []).filter((rule) => rule.type === "field").map((rule) => [rule.ref, rule]));
  const blockers = [];
  for (const rule of payload.logic || []) if (rule.type !== "field") blockers.push("Hidden-field logic requires an explicit externally supplied test allocation and is not auto-solved.");
  const outcomesByRef = new Map();
  const requiredOutcomeKeys = [];
  for (const [ref, rule] of ruleByRef.entries()) {
    const outcomes = ruleOutcomes(rule, fields);
    outcomesByRef.set(ref, outcomes);
    for (let actionIndex = 0; actionIndex < (rule.actions || []).length; actionIndex += 1) {
      const key = `logic:${ref}:action:${actionIndex}`;
      if (!outcomes.some((outcome) => outcome.key === key)) blockers.push(`Logic outcome ${key} is shadowed or cannot be solved.`);
      else requiredOutcomeKeys.push(key);
    }
    if (outcomes.some((outcome) => outcome.key === `logic:${ref}:default`)) requiredOutcomeKeys.push(`logic:${ref}:default`);
  }

  function move(state, outcome) {
    const assignments = mergeAssignments(state.assignments, outcome.assignment);
    if (!assignments || !assignmentsValid(assignments, fields)) return null;
    const rule = ruleByRef.get(outcome.rule_ref);
    const actionIndex = (rule?.actions || []).findIndex((action) => evaluateCondition(action.condition, assignments));
    const actualKey = actionIndex >= 0 ? `logic:${outcome.rule_ref}:action:${actionIndex}` : `logic:${outcome.rule_ref}:default`;
    if (actualKey !== outcome.key) return null;
    const next = { assignments, visited: state.visited, coverage: [...state.coverage, outcome.key], ending: null, index: state.index + 1 };
    if (outcome.target?.type === "field" && indexByRef.has(outcome.target.value)) next.index = indexByRef.get(outcome.target.value);
    else if (["thankyou", "outcome"].includes(outcome.target?.type)) next.ending = `${outcome.target.type}:${outcome.target.value}`;
    return next;
  }

  function complete(state, selectOutcome = (outcomes) => outcomes[0]) {
    let current = clone(state);
    let guard = 0;
    while (!current.ending && current.index < orderedFields.length && guard <= orderedFields.length + 2) {
      guard += 1;
      const field = orderedFields[current.index];
      current.visited.push(field.ref);
      const outcomes = outcomesByRef.get(field.ref);
      if (!outcomes) current.index += 1;
      else {
        const next = move(current, selectOutcome(outcomes, field.ref));
        if (!next) return null;
        current = next;
      }
    }
    if (!current.ending) current.ending = guard > orderedFields.length + 2 ? "loop_guard" : "natural_end";
    return { assignments: current.assignments, visited_question_refs: current.visited, coverage: current.coverage, ending: current.ending };
  }

  function findTarget(targetKey) {
    const queue = [{ index: 0, assignments: {}, visited: [], coverage: [] }];
    const seen = new Set();
    let processed = 0;
    while (queue.length > 0 && processed < 4096) {
      processed += 1;
      const state = queue.shift();
      if (state.index >= orderedFields.length) continue;
      const field = orderedFields[state.index];
      const visited = [...state.visited, field.ref];
      const outcomes = outcomesByRef.get(field.ref);
      if (!outcomes) {
        const key = `${state.index + 1}:${hashJson(state.assignments)}`;
        if (!seen.has(key)) {
          seen.add(key);
          queue.push({ ...state, index: state.index + 1, visited });
        }
        continue;
      }
      const target = outcomes.find((outcome) => outcome.key === targetKey);
      if (target) {
        const moved = move({ ...state, visited }, target);
        const seed = parseInt(hashJson(targetKey).slice(0, 8), 16);
        return moved ? complete(moved, (items, ref) => items[(seed + parseInt(hashJson(ref).slice(0, 4), 16)) % items.length]) : null;
      }
      for (const outcome of outcomes) {
        const moved = move({ ...state, visited }, outcome);
        if (!moved || moved.ending) continue;
        const key = `${moved.index}:${hashJson(moved.assignments)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        queue.push(moved);
      }
    }
    return null;
  }

  const leaves = [];
  for (const key of requiredOutcomeKeys) {
    const leaf = findTarget(key);
    if (leaf) leaves.push(leaf);
    else blockers.push(`Logic outcome ${key} is unreachable from the form start.`);
  }
  const maxOutcomeCount = Math.max(1, ...[...outcomesByRef.values()].map((items) => items.length));
  const ruleOrder = new Map([...outcomesByRef.keys()].map((ref, index) => [ref, index]));
  for (let stride = 1; stride <= Math.min(5, maxOutcomeCount); stride += 1) {
    for (let cycle = 0; cycle < maxOutcomeCount; cycle += 1) {
      const leaf = complete({ index: 0, assignments: {}, visited: [], coverage: [] }, (items, ref) => items[(cycle + stride * ruleOrder.get(ref)) % items.length]);
      if (leaf) leaves.push(leaf);
    }
  }
  if (requiredOutcomeKeys.length === 0 && orderedFields.length > 0) {
    const leaf = complete({ index: 0, assignments: {}, visited: [], coverage: [] });
    if (leaf) leaves.push(leaf);
  }
  return { leaves, blockers };
}

function generateTypeformPaths(payload) {
  const blockers = [];
  const candidates = [];
  const enumerated = enumeratePaths(payload);
  blockers.push(...enumerated.blockers);
  for (const leaf of enumerated.leaves) {
    const signature = hashJson({ assignments: leaf.assignments, visited: leaf.visited_question_refs, ending: leaf.ending, coverage: leaf.coverage });
    if (!candidates.some((item) => item.signature === signature)) candidates.push({ ...leaf, signature });
  }
  const requirements = [...new Set(candidates.flatMap((candidate) => candidate.coverage))].sort();
  for (const rule of payload.logic || []) {
    if (rule.type !== "field") continue;
    for (let actionIndex = 0; actionIndex < (rule.actions || []).length; actionIndex += 1) {
      const key = `logic:${rule.ref}:action:${actionIndex}`;
      if (!requirements.includes(key)) blockers.push(`Logic outcome ${key} is shadowed, unreachable, or unsupported.`);
    }
  }

  if (requirements.length === 0 && candidates.length === 0 && flattenFields(payload.fields).length > 0) {
    candidates.push({ assignments: {}, visited_question_refs: flattenFields(payload.fields).map((field) => field.ref), coverage: [], ending: "natural_end" });
  }
  const uncovered = new Set(requirements);
  const selected = [];
  const remaining = candidates.map((candidate, index) => ({ ...candidate, candidate_index: index }));
  while (uncovered.size > 0) {
    remaining.sort((a, b) => {
      const aGain = a.coverage.filter((item) => uncovered.has(item)).length;
      const bGain = b.coverage.filter((item) => uncovered.has(item)).length;
      return bGain - aGain || a.visited_question_refs.length - b.visited_question_refs.length || a.candidate_index - b.candidate_index;
    });
    const best = remaining.shift();
    if (!best || best.coverage.every((item) => !uncovered.has(item))) break;
    selected.push(best);
    for (const item of best.coverage) uncovered.delete(item);
  }
  for (const requirement of uncovered) blockers.push(`No generated path covers ${requirement}.`);
  if (selected.length === 0 && candidates.length > 0) selected.push(candidates[0]);

  const cases = selected.map((item, index) => ({
    case_id: `path_${String(index + 1).padStart(2, "0")}`,
    assignments: item.assignments,
    expected_question_refs: item.visited_question_refs,
    expected_ending: item.ending,
    covers: item.coverage,
  }));
  const coverage = Object.fromEntries(requirements.map((requirement) => [requirement, cases.filter((item) => item.covers.includes(requirement)).map((item) => item.case_id)]));
  const behaviorGroups = new Map();
  function addBehavior(kind, signature, fieldRef, contentSpecific = false) {
    const key = contentSpecific ? `${kind}:${fieldRef}` : `${kind}:${signature}`;
    const existing = behaviorGroups.get(key);
    if (existing) existing.applies_to_field_refs.push(fieldRef);
    else behaviorGroups.set(key, { behavior_id: key.replace(/[^a-zA-Z0-9:_-]/g, "_"), representative_field_ref: fieldRef, applies_to_field_refs: [fieldRef], kind });
  }
  for (const field of flattenFields(payload.fields)) {
    if (field.validations?.required === true) addBehavior("required_block", field.type, field.ref);
    if (field.properties?.allow_other_choice === true) addBehavior("other_text_and_encoding", field.type, field.ref);
    if (field.properties?.allow_multiple_selection === true) addBehavior("multiple_selection_and_limits", `${field.type}:${field.validations?.min_selection ?? "none"}:${field.validations?.max_selection ?? "none"}`, field.ref);
    if (field.type === "inline_group") addBehavior("same_page_rendering", field.type, field.ref, true);
    if (field.attachment?.type === "image" || (field.properties?.choices || []).some((choice) => choice.attachment?.type === "image")) addBehavior("image_rendering_and_mapping", field.type, field.ref, true);
  }
  const behaviorChecks = [...behaviorGroups.values()];
  return {
    schema_version: "1.0",
    platform: "typeform",
    generated_by: SCRIPT_ID,
    instrument_sha256: contentFingerprint(payload),
    status: blockers.length > 0 ? "block" : "pass",
    blockers,
    required_logic_behaviors: requirements,
    coverage,
    renderer_cases: cases,
    behavior_checks: behaviorChecks,
  };
}

function projectLike(expected, actual) {
  if (Array.isArray(expected)) {
    if (expected.length === 0 && actual === undefined) return [];
    if (!Array.isArray(actual)) return actual;
    const byRef = expected.every((item) => item && typeof item === "object" && nonEmpty(item.ref));
    if (byRef) {
      const actualByRef = new Map(actual.filter((item) => item && typeof item === "object").map((item) => [item.ref, item]));
      return expected.map((item) => projectLike(item, actualByRef.get(item.ref)));
    }
    return expected.map((item, index) => projectLike(item, actual[index]));
  }
  if (expected && typeof expected === "object") {
    return Object.fromEntries(Object.keys(expected).map((key) => [key, projectLike(expected[key], actual?.[key])]));
  }
  return actual;
}

function contentProjection(value) {
  const copy = clone(value);
  if (copy.settings) delete copy.settings.is_public;
  return copy;
}

function contentFingerprint(value) {
  return hashJson(contentProjection(value));
}

function collectRefs(payload) {
  const fields = flattenFields(payload.fields).map((field) => ({
    ref: field.ref,
    choices: (field.properties?.choices || []).map((choice) => choice.ref).filter(nonEmpty).sort(),
  }));
  return { fields, thankyou_screens: (payload.thankyou_screens || []).map((item) => item.ref).filter(nonEmpty).sort() };
}

function verifyTypeformVersion(payload, readback, formId) {
  const projectedReadback = projectLike(payload, readback);
  const expectedHash = contentFingerprint(payload);
  const readbackHash = contentFingerprint(projectedReadback);
  const blockers = [];
  if (!nonEmpty(formId)) blockers.push("form_id is required.");
  if (readback.id !== formId) blockers.push(`Read-back form id ${readback.id ?? "<missing>"} does not match ${formId}.`);
  if (expectedHash !== readbackHash) blockers.push("Payload and read-back semantic fingerprints differ.");
  const refs = collectRefs(payload);
  return {
    schema_version: "1.0",
    platform: "typeform",
    generated_by: SCRIPT_ID,
    form_id: formId,
    status: blockers.length > 0 ? "block" : "pass",
    blockers,
    instrument_sha256: expectedHash,
    readback_instrument_sha256: readbackHash,
    payload_file_sha256: null,
    readback_file_sha256: null,
    refs_sha256: hashJson(refs),
    refs,
    readback_public_state: readback.settings?.is_public === true,
  };
}

function answerMap(response) {
  return new Map((response.answers || []).filter((answer) => nonEmpty(answer.field?.ref)).map((answer) => [answer.field.ref, answer]));
}

function normalizedAnswer(answer) {
  if (!answer) return undefined;
  if (answer.type === "choice") return answer.choice?.other ?? answer.choice?.ref ?? answer.choice?.label;
  if (answer.type === "choices") return [...(answer.choices?.refs ?? answer.choices?.labels ?? []), ...(answer.choices?.other ? [answer.choices.other] : [])];
  if (answer.type === "boolean") return answer.boolean;
  if (answer.type === "number") return answer.number;
  if (["text", "email", "date", "url", "file_url", "phone_number"].includes(answer.type)) return answer[answer.type];
  return answer[answer.type];
}

function matchesCase(response, selector) {
  if (nonEmpty(selector?.response_id) && response.response_id !== selector.response_id) return false;
  if (nonEmpty(selector?.token) && response.token !== selector.token) return false;
  for (const [key, value] of Object.entries(selector?.hidden || {})) if (response.hidden?.[key] !== value) return false;
  if (selector?.answer) {
    const actual = normalizedAnswer(answerMap(response).get(selector.answer.field_ref));
    if (selector.answer.operator === "equals" && actual !== selector.answer.value) return false;
    if (selector.answer.operator === "contains" && !(typeof actual === "string" ? actual.includes(selector.answer.value) : Array.isArray(actual) && actual.includes(selector.answer.value))) return false;
  }
  return Boolean(nonEmpty(selector?.response_id) || nonEmpty(selector?.token) || Object.keys(selector?.hidden || {}).length > 0 || selector?.answer);
}

function verifyExpectation(answer, expectation) {
  const actual = normalizedAnswer(answer);
  if (expectation.operator === "present") return answer !== undefined;
  if (expectation.operator === "absent") return answer === undefined;
  if (expectation.operator === "equals") return JSON.stringify(actual) === JSON.stringify(expectation.value);
  if (expectation.operator === "contains_all") return Array.isArray(actual) && (expectation.values || []).every((item) => actual.includes(item));
  return false;
}

function verifyTypeformResponses(expectations, responsesDoc, identity) {
  const blockers = [];
  if (identity.status !== "pass") blockers.push("Instrument identity is not passing.");
  if (expectations.form_id !== identity.form_id) blockers.push("Expectation form_id does not match instrument identity.");
  if (expectations.instrument_sha256 !== identity.instrument_sha256) blockers.push("Expectation instrument_sha256 does not match instrument identity.");
  if (responsesDoc.form_id && responsesDoc.form_id !== identity.form_id) blockers.push("Responses file form_id does not match instrument identity.");
  const validFields = new Map((identity.refs?.fields || []).map((item) => [item.ref, new Set(item.choices || [])]));
  const responses = responsesDoc.items || responsesDoc.responses || [];
  const caseResults = [];
  for (const testCase of expectations.test_cases || []) {
    const hasSelector = nonEmpty(testCase.selector?.response_id) || nonEmpty(testCase.selector?.token) || Object.keys(testCase.selector?.hidden || {}).length > 0 || Boolean(testCase.selector?.answer);
    const matched = responses.filter((response) => matchesCase(response, testCase.selector));
    const failures = [];
    if (!nonEmpty(testCase.test_case_id)) failures.push("test_case_id is required.");
    if (!hasSelector) failures.push("A response_id, token, hidden-field, or unique answer-marker selector is required.");
    if (matched.length > 1) failures.push(`Expected exactly one matching response; found ${matched.length}.`);
    const response = matched[0];
    if (response) {
      const answers = answerMap(response);
      for (const expectation of testCase.answers || []) {
        const observedAnswer = answers.get(expectation.field_ref);
        const isNativeOtherText = observedAnswer?.type === "choice" && nonEmpty(observedAnswer.choice?.other) && expectation.value === observedAnswer.choice.other;
        if (!validFields.has(expectation.field_ref)) failures.push(`Unknown field_ref ${expectation.field_ref}.`);
        if (!["present", "absent", "equals", "contains_all"].includes(expectation.operator)) failures.push(`Unsupported operator ${expectation.operator} for ${expectation.field_ref}.`);
        if (expectation.operator === "equals" && validFields.get(expectation.field_ref)?.size > 0 && typeof expectation.value === "string" && !validFields.get(expectation.field_ref).has(expectation.value) && !isNativeOtherText) failures.push(`Unknown choice ref ${expectation.value} for ${expectation.field_ref}.`);
        for (const ref of expectation.values || []) {
          if (!validFields.get(expectation.field_ref)?.has(ref)) failures.push(`Unknown choice ref ${ref} for ${expectation.field_ref}.`);
        }
        if (!verifyExpectation(observedAnswer, expectation)) failures.push(`Answer expectation failed for ${expectation.field_ref} (${expectation.operator}).`);
      }
    }
    const status = failures.length > 0 ? "block" : matched.length === 0 ? "pending_api_visibility" : "pass";
    caseResults.push({ test_case_id: testCase.test_case_id, response_id: response?.response_id || null, status, failures: matched.length === 0 && failures.length === 0 ? ["No matching response is visible yet; retry the same bounded window after the Typeform API visibility delay."] : failures });
  }
  if (!Array.isArray(expectations.test_cases) || expectations.test_cases.length === 0) blockers.push("At least one response test case is required.");
  for (const result of caseResults.filter((item) => item.status === "block")) blockers.push(...result.failures.map((message) => `${result.test_case_id}: ${message}`));
  const pending = caseResults.some((item) => item.status === "pending_api_visibility");
  return {
    schema_version: "1.0",
    platform: "typeform",
    generated_by: SCRIPT_ID,
    form_id: identity.form_id,
    instrument_sha256: identity.instrument_sha256,
    status: blockers.length > 0 ? "block" : pending ? "pending_api_visibility" : "pass",
    blockers,
    cases: caseResults,
  };
}

function resolveEvidencePath(manifestPath, value) {
  const base = path.dirname(path.resolve(manifestPath));
  const resolved = path.resolve(base, value);
  if (resolved !== base && !resolved.startsWith(`${base}${path.sep}`)) throw new Error("Evidence paths must stay inside the manifest directory.");
  return resolved;
}

function readEvidence(manifestPath, value) {
  const resolved = resolveEvidencePath(manifestPath, value);
  return { path: resolved, sha256: hashFile(resolved), data: readJson(resolved) };
}

function buildTypeformGate(manifest, manifestPath) {
  const blockers = [];
  const evidence = {};
  if (manifest.publication_mode !== "agent_publish") blockers.push("Launch gate generation requires publication_mode=agent_publish.");
  if (!new Set(["C", "D"]).has(manifest.risk_level)) blockers.push("Agent publish requires risk_level C or D.");
  const required = ["instrument_identity", "survey_quality_result", "readback_diff", "path_plan", "renderer_results"];
  for (const key of required) {
    if (!nonEmpty(manifest.evidence?.[key])) {
      blockers.push(`evidence.${key} is required.`);
      continue;
    }
    try { evidence[key] = readEvidence(manifestPath, manifest.evidence[key]); } catch (error) { blockers.push(`Could not read ${key}: ${error.message}`); }
  }
  if (manifest.require_response_verification === true) {
    if (!nonEmpty(manifest.evidence?.response_verification)) blockers.push("response_verification evidence is required.");
    else {
      try { evidence.response_verification = readEvidence(manifestPath, manifest.evidence.response_verification); } catch (error) { blockers.push(`Could not read response_verification: ${error.message}`); }
    }
    const lifecycle = manifest.test_data_lifecycle || {};
    if (lifecycle.required !== true) blockers.push("Response verification requires test_data_lifecycle.required=true.");
    if (!nonEmpty(lifecycle.test_case_marker)) blockers.push("Test data lifecycle requires a non-personal test_case_marker.");
    if (!nonEmpty(lifecycle.retrieval_window?.since) || !nonEmpty(lifecycle.retrieval_window?.until)) blockers.push("Test data lifecycle requires a narrow retrieval_window.");
    if (!nonEmpty(lifecycle.analysis_exclusion_rule)) blockers.push("Test data lifecycle requires an analysis_exclusion_rule.");
    if (!new Set(["planned", "retained_excluded", "deleted"]).has(lifecycle.cleanup_status)) blockers.push("Test data lifecycle cleanup_status must be planned, retained_excluded, or deleted.");
  }
  const identity = evidence.instrument_identity?.data;
  const instrumentHash = identity?.instrument_sha256;
  if (identity?.status !== "pass") blockers.push("Instrument identity evidence is not pass.");
  if (identity?.form_id !== manifest.form_id) blockers.push("Instrument identity form_id does not match manifest form_id.");
  if (evidence.survey_quality_result?.data?.generated_by !== "survey_quality_gate_check.js") blockers.push("Survey quality evidence was not generated by survey_quality_gate_check.js.");
  if (evidence.survey_quality_result?.data?.derived_status !== "pass") blockers.push("Derived survey quality status is not pass.");
  if (evidence.readback_diff?.data?.generated_by !== "typeform_api.js") blockers.push("Read-back diff evidence was not generated by typeform_api.js.");
  if (evidence.readback_diff?.data?.status !== "pass") blockers.push("Read-back diff status is not pass.");
  if (evidence.path_plan?.data?.status !== "pass") blockers.push("Generated path plan status is not pass.");
  if (evidence.path_plan?.data?.instrument_sha256 !== instrumentHash) blockers.push("Path plan instrument fingerprint is stale.");

  const renderer = evidence.renderer_results?.data;
  if (renderer?.form_id !== manifest.form_id || renderer?.instrument_sha256 !== instrumentHash) blockers.push("Renderer evidence belongs to a different form or instrument version.");
  const passingRuns = new Map((renderer?.runs || []).filter((run) => run.status === "pass").map((run) => [run.case_id, run]));
  const allRendererCases = evidence.path_plan?.data?.renderer_cases || [];
  let requiredRendererCases = allRendererCases;
  if (Array.isArray(manifest.required_renderer_case_ids)) {
    const requested = new Set(manifest.required_renderer_case_ids);
    if (requested.size === 0) blockers.push("required_renderer_case_ids cannot be empty when supplied.");
    const known = new Set(allRendererCases.map((item) => item.case_id));
    for (const caseId of requested) if (!known.has(caseId)) blockers.push(`Required renderer case ${caseId} does not exist in the generated path plan.`);
    requiredRendererCases = allRendererCases.filter((item) => requested.has(item.case_id));
    if (requiredRendererCases.length < allRendererCases.length && !nonEmpty(manifest.renderer_sampling_rationale)) blockers.push("A renderer_sampling_rationale is required when runtime coverage uses a subset of generated cases.");
  }
  for (const testCase of requiredRendererCases) {
    const run = passingRuns.get(testCase.case_id);
    if (!run) {
      blockers.push(`Renderer case ${testCase.case_id} is not passing.`);
      continue;
    }
    if (JSON.stringify(run.observed_question_refs) !== JSON.stringify(testCase.expected_question_refs)) blockers.push(`Renderer case ${testCase.case_id} question sequence differs from the plan.`);
    if (run.observed_ending !== testCase.expected_ending) blockers.push(`Renderer case ${testCase.case_id} ending differs from the plan.`);
    if (!Array.isArray(run.evidence_refs) || run.evidence_refs.length === 0) blockers.push(`Renderer case ${testCase.case_id} has no evidence reference.`);
  }
  for (const check of evidence.path_plan?.data?.behavior_checks || []) {
    const observed = (renderer?.behavior_checks || []).find((item) => item.behavior_id === check.behavior_id && item.status === "pass");
    if (!observed) blockers.push(`Renderer behavior ${check.behavior_id} is not passing.`);
    else if (!Array.isArray(observed.evidence_refs) || observed.evidence_refs.length === 0) blockers.push(`Renderer behavior ${check.behavior_id} has no evidence reference.`);
  }
  const mobilePass = (renderer?.runs || []).some((run) => run.status === "pass" && run.device === "mobile");
  if (manifest.require_mobile === true && !mobilePass) blockers.push("At least one passing mobile renderer run is required.");
  if (manifest.require_response_verification === true) {
    const responseEvidence = evidence.response_verification?.data;
    if (responseEvidence?.status !== "pass") blockers.push("Response verification is not pass.");
    if (responseEvidence?.form_id !== manifest.form_id || responseEvidence?.instrument_sha256 !== instrumentHash) blockers.push("Response evidence belongs to a different form or instrument version.");
  }
  const owner = manifest.owner_publish_confirmation || { status: "pending", confirmed_by: "", confirmed_at: "" };
  if (owner.status !== "confirmed") blockers.push("Owner publish confirmation is pending.");
  if (owner.status === "confirmed" && (!nonEmpty(owner.confirmed_by) || !nonEmpty(owner.confirmed_at))) blockers.push("Confirmed owner publish authorization requires confirmed_by and confirmed_at.");

  const evidenceIndex = Object.fromEntries(Object.entries(evidence).map(([key, item]) => [key, { path: path.relative(path.dirname(path.resolve(manifestPath)), item.path), sha256: item.sha256 }]));
  evidenceIndex.evidence_manifest = { path: path.relative(path.dirname(path.resolve(manifestPath)), path.resolve(manifestPath)), sha256: hashFile(manifestPath) };
  const logicPass = !blockers.some((item) => item.startsWith("Renderer case") || item.startsWith("Renderer behavior") || item.includes("path plan") || item.includes("Path plan"));
  return {
    schema_version: "2.0",
    platform: "typeform",
    generated_by: SCRIPT_ID,
    publication_mode: manifest.publication_mode,
    risk_level: manifest.risk_level,
    form_id: manifest.form_id,
    instrument_sha256: instrumentHash || null,
    evidence_manifest_sha256: hashFile(manifestPath),
    evidence: evidenceIndex,
    survey_quality_gate_status: evidence.survey_quality_result?.data?.derived_status === "pass" ? "pass" : "block",
    readback_diff_status: evidence.readback_diff?.data?.status === "pass" ? "pass" : "block",
    logic_test_status: logicPass ? "pass" : "block",
    mobile_test_required: manifest.require_mobile === true,
    mobile_test_status: manifest.require_mobile === true ? (mobilePass ? "pass" : "block") : "not_required",
    response_test_status: manifest.require_response_verification === true ? (evidence.response_verification?.data?.status === "pass" ? "pass" : "block") : "not_required",
    test_data_lifecycle: manifest.test_data_lifecycle || { required: false, cleanup_status: "not_needed" },
    renderer_case_scope: {
      generated_case_count: allRendererCases.length,
      required_case_ids: requiredRendererCases.map((item) => item.case_id),
      sampling_rationale: manifest.renderer_sampling_rationale || null,
    },
    owner_publish_confirmation: owner,
    blockers,
    status: blockers.length > 0 ? "block" : "pass",
  };
}

function validateGeneratedGate(gate, gatePath) {
  const blockers = [];
  if (gate?.schema_version !== "2.0" || gate?.generated_by !== SCRIPT_ID) blockers.push("Launch gate must be generated by survey_preflight.js schema 2.0.");
  if (gate?.publication_mode !== "agent_publish") blockers.push("Launch gate publication_mode must be agent_publish.");
  if (!new Set(["C", "D"]).has(gate?.risk_level)) blockers.push("Launch gate risk_level must be C or D.");
  const base = path.dirname(path.resolve(gatePath));
  for (const [key, item] of Object.entries(gate?.evidence || {})) {
    try {
      const resolved = path.resolve(base, item.path);
      if (resolved !== base && !resolved.startsWith(`${base}${path.sep}`)) throw new Error("path escapes the gate directory");
      if (hashFile(resolved) !== item.sha256) blockers.push(`Evidence ${key} has changed since gate generation.`);
    } catch (error) {
      blockers.push(`Evidence ${key} cannot be verified: ${error.message}`);
    }
  }
  if (gate?.status !== "pass" || (gate?.blockers || []).length > 0) blockers.push("Generated launch gate is not pass.");
  return { status: blockers.length > 0 ? "block" : "pass", blockers };
}

function selfTest(options = {}) {
  const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "survey-preflight-"));
  const checks = [];
  function assertCheck(name, condition, message) {
    checks.push({ check: name, status: condition ? "pass" : "fail" });
    if (!condition) throw new Error(message);
  }
  const payload = {
    title: "Fixture",
    settings: { is_public: false },
    fields: [
      { ref: "q1", title: "Continue?", type: "multiple_choice", properties: { choices: [{ ref: "yes", label: "Yes" }, { ref: "no", label: "No" }] }, validations: { required: true } },
      { ref: "q2", title: "Why?", type: "multiple_choice", properties: { allow_multiple_selection: true, allow_other_choice: true, choices: [{ ref: "a", label: "A" }, { ref: "b", label: "B" }] } },
    ],
    thankyou_screens: [{ ref: "early", title: "Bye" }],
    logic: [{ type: "field", ref: "q1", actions: [{ action: "jump", details: { to: { type: "thankyou", value: "early" } }, condition: { op: "is", vars: [{ type: "field", value: "q1" }, { type: "choice", value: "no" }] } }] }],
  };
  const plan = generateTypeformPaths(payload);
  assertCheck("path_generation", plan.status === "pass" && !Object.values(plan.coverage).some((items) => items.length === 0), "Path generation fixture failed.");
  const readback = clone(payload);
  readback.id = "form1";
  readback.settings.is_public = true;
  const identity = verifyTypeformVersion(payload, readback, "form1");
  assertCheck("current_version_passes", identity.status === "pass", "Version identity fixture failed.");
  const staleReadback = clone(readback);
  staleReadback.fields[0].title = "Changed title";
  assertCheck("stale_instrument_blocked", verifyTypeformVersion(payload, staleReadback, "form1").status === "block", "Stale instrument version was not blocked.");
  const expectations = {
    form_id: "form1",
    instrument_sha256: identity.instrument_sha256,
    test_cases: [{ test_case_id: "tc1", selector: { hidden: { test_case: "tc1" } }, answers: [{ field_ref: "q1", operator: "equals", value: "yes" }] }],
  };
  const responses = { form_id: "form1", items: [{ response_id: "r1", hidden: { test_case: "tc1" }, answers: [{ type: "choice", field: { ref: "q1" }, choice: { ref: "yes", label: "Yes" } }] }] };
  const responseResult = verifyTypeformResponses(expectations, responses, identity);
  assertCheck("response_encoding_passes", responseResult.status === "pass", "Response verification fixture failed.");
  const wrongResponses = clone(responses);
  wrongResponses.items[0].answers[0].choice.ref = "no";
  assertCheck("wrong_response_encoding_blocked", verifyTypeformResponses(expectations, wrongResponses, identity).status === "block", "Wrong response encoding was not blocked.");

  const files = { identity, quality: { generated_by: "survey_quality_gate_check.js", derived_status: "pass" }, diff: { generated_by: "typeform_api.js", status: "pass" }, plan, responses: responseResult };
  for (const [name, value] of Object.entries(files)) writeJson(path.join(tmp, `${name}.json`), value);
  writeJson(path.join(tmp, "renderer.json"), {
    form_id: "form1",
    instrument_sha256: identity.instrument_sha256,
    runs: plan.renderer_cases.map((item, index) => ({ case_id: item.case_id, status: "pass", device: index === 0 ? "mobile" : "desktop", observed_question_refs: item.expected_question_refs, observed_ending: item.expected_ending, evidence_refs: [`fixture-${item.case_id}`] })),
    behavior_checks: plan.behavior_checks.map((item) => ({ behavior_id: item.behavior_id, status: "pass", evidence_refs: [`fixture-${item.behavior_id}`] })),
  });
  const manifestPath = path.join(tmp, "manifest.json");
  writeJson(manifestPath, {
    form_id: "form1",
    publication_mode: "agent_publish",
    risk_level: "C",
    require_mobile: true,
    require_response_verification: true,
    test_data_lifecycle: {
      required: true,
      test_case_marker: "tc1",
      retrieval_window: { since: "2026-09-09T00:00:00Z", until: "2026-09-09T00:10:00Z" },
      analysis_exclusion_rule: "Exclude hidden.test_case=tc1.",
      retention_until: "2026-09-16T00:00:00Z",
      cleanup_status: "planned",
    },
    evidence: { instrument_identity: "identity.json", survey_quality_result: "quality.json", readback_diff: "diff.json", path_plan: "plan.json", renderer_results: "renderer.json", response_verification: "responses.json" },
    owner_publish_confirmation: { status: "confirmed", confirmed_by: "owner", confirmed_at: "2026-09-09T00:00:00Z" },
  });
  const gate = buildTypeformGate(readJson(manifestPath), manifestPath);
  assertCheck("complete_gate_passes", gate.status === "pass", `Gate fixture failed: ${JSON.stringify(gate.blockers)}`);
  const gatePath = path.join(tmp, "gate.json");
  writeJson(gatePath, gate);
  assertCheck("untampered_gate_passes", validateGeneratedGate(gate, gatePath).status === "pass", "Generated gate evidence verification failed.");
  fs.appendFileSync(path.join(tmp, "quality.json"), " ");
  assertCheck("tampered_evidence_blocked", validateGeneratedGate(gate, gatePath).status === "block", "Changed evidence was not detected.");
  writeJson(path.join(tmp, "quality.json"), files.quality);
  fs.unlinkSync(path.join(tmp, "renderer.json"));
  assertCheck("missing_evidence_blocked", validateGeneratedGate(gate, gatePath).status === "block", "Missing evidence was not detected.");
  writeJson(path.join(tmp, "renderer.json"), {
    form_id: "form1",
    instrument_sha256: identity.instrument_sha256,
    runs: plan.renderer_cases.map((item, index) => ({ case_id: item.case_id, status: "pass", device: index === 0 ? "mobile" : "desktop", observed_question_refs: item.expected_question_refs, observed_ending: item.expected_ending, evidence_refs: [`fixture-${item.case_id}`] })),
    behavior_checks: plan.behavior_checks.map((item) => ({ behavior_id: item.behavior_id, status: "pass", evidence_refs: [`fixture-${item.behavior_id}`] })),
  });
  const stalePlan = clone(plan);
  stalePlan.instrument_sha256 = "stale-instrument-fingerprint";
  writeJson(path.join(tmp, "plan.json"), stalePlan);
  const staleGate = buildTypeformGate(readJson(manifestPath), manifestPath);
  assertCheck("stale_evidence_blocked", staleGate.status === "block" && staleGate.blockers.some((item) => item.includes("stale")), "Stale evidence was not detected.");
  const report = { schema_version: "1.0", generated_by: SCRIPT_ID, status: "pass", checks };
  if (nonEmpty(options.out)) writeJson(options.out, report);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  return report;
}

function requireOut(options) {
  if (!nonEmpty(options.out)) throw new Error("--out is required.");
}

function main(argv) {
  const options = parseArgs(argv);
  const [command, ...args] = options.positional;
  if (options.help || command === "help") {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (command === "self-test") return selfTest(options);
  requireOut(options);
  let result;
  if (command === "generate-typeform-paths" && args[0]) {
    result = generateTypeformPaths(readJson(args[0]));
  } else if (command === "verify-typeform-version" && args[0] && args[1]) {
    result = verifyTypeformVersion(readJson(args[0]), readJson(args[1]), options.formId);
    result.payload_file_sha256 = hashFile(args[0]);
    result.readback_file_sha256 = hashFile(args[1]);
  } else if (command === "verify-typeform-responses" && args[0] && args[1] && nonEmpty(options.identity)) {
    result = verifyTypeformResponses(readJson(args[0]), readJson(args[1]), readJson(options.identity));
  } else if (command === "build-typeform-gate" && args[0]) {
    result = buildTypeformGate(readJson(args[0]), args[0]);
  } else {
    process.stderr.write(`${usage()}\n`);
    process.exitCode = 2;
    return;
  }
  writeJson(options.out, result);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (result.status !== "pass") process.exitCode = 1;
}

if (require.main === module) {
  try { main(process.argv.slice(2)); } catch (error) {
    process.stderr.write(`survey_preflight failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = {
  buildTypeformGate,
  generateTypeformPaths,
  validateGeneratedGate,
  verifyTypeformResponses,
  verifyTypeformVersion,
};

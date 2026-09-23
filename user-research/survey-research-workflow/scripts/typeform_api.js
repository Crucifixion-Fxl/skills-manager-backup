#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { validateGeneratedGate, verifyTypeformVersion } = require("./survey_preflight.js");

const OFFICIAL_BASE_URLS = new Set([
  "https://api.typeform.com",
  "https://api.eu.typeform.com",
  "https://api.typeform.eu",
]);
const FIELD_TYPES = new Set([
  "calendly", "checkbox", "contact_info", "date", "dropdown", "email", "file_upload",
  "google_calendar", "group", "inline_group", "legal", "long_text", "matrix", "multi_format",
  "multiple_choice", "nps", "number", "opinion_scale", "payment", "phone_number",
  "picture_choice", "ranking", "rating", "short_text", "signature", "statement",
  "website", "yes_no",
]);
const INLINE_GROUP_CHILD_TYPES = new Set([
  "checkbox", "date", "dropdown", "email", "legal", "long_text", "multiple_choice",
  "nps", "number", "opinion_scale", "phone_number", "ranking", "rating", "short_text",
  "statement", "website", "yes_no",
]);
const REF_RE = /^[a-zA-Z0-9_-]{1,254}$/;
const TYPEFORM_IMAGE_HREF_RE = /^https:\/\/images\.typeform\.com\/images\/[a-zA-Z0-9]+(?:\/.*)?$/;

function usage() {
  return [
    "Usage:",
    "  node scripts/typeform_api.js --help",
    "  node scripts/typeform_api.js check <payload.json>",
    "  node scripts/typeform_api.js diff <payload.json> <readback.json> [--out <file>]",
    "  node scripts/typeform_api.js get <form_id> [--out <file>]",
    "  node scripts/typeform_api.js upload-image <image-file> --out <file> [--upload-source user_upload]",
    "  node scripts/typeform_api.js create-private <payload.json> --out-dir <dir>",
    "  node scripts/typeform_api.js replace-private <form_id> <payload.json> --confirm-form-id <form_id> --out-dir <dir>",
    "  node scripts/typeform_api.js begin-response-test <form_id> --confirm-form-id <form_id> --payload <payload.json> --identity <identity.json> --out-dir <dir>",
    "  node scripts/typeform_api.js end-response-test <transaction.json> --confirm-form-id <form_id> --out-dir <dir>",
    "  node scripts/typeform_api.js responses <form_id> --confirm-form-id <form_id> --out <file> --since <ISO-8601> --until <ISO-8601> [--response-type completed|partial|started]",
    "  node scripts/typeform_api.js publish <form_id> --confirm-form-id <form_id> --launch-gate <file> [--out <file>]",
    "  node scripts/typeform_api.js self-test",
    "",
    "Environment:",
    "  TYPEFORM_ACCESS_TOKEN   Required for network commands; never printed.",
    "  TYPEFORM_API_BASE       Optional official regional API base URL.",
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

function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function flattenFields(fields, out = []) {
  for (const field of Array.isArray(fields) ? fields : []) {
    if (!field || typeof field !== "object") continue;
    out.push(field);
    const nested = field.properties?.fields;
    if (Array.isArray(nested)) {
      for (const item of nested) {
        if (Array.isArray(item)) flattenFields(item, out);
        else flattenFields([item], out);
      }
    }
  }
  return out;
}

function validateImageAttachment(attachment, label, blockers) {
  if (!attachment || typeof attachment !== "object") return;
  if (attachment.type !== "image") return;
  if (!TYPEFORM_IMAGE_HREF_RE.test(attachment.href || "")) {
    blockers.push(`${label}.href must reference an image already stored at images.typeform.com.`);
  }
  const decorative = attachment.properties?.decorative === true;
  if (!decorative && !nonEmpty(attachment.properties?.description)) {
    blockers.push(`${label}.properties.description is required as alt text unless the image is explicitly decorative.`);
  }
}

function validatePayload(payload) {
  const blockers = [];
  const warnings = [];
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return { status: "block", blockers: ["Payload root must be an object."], warnings };
  }
  if (!nonEmpty(payload.title)) blockers.push("title is required.");
  if (payload.settings?.is_public !== false) blockers.push("settings.is_public must be explicitly false for a build payload.");
  if (!Array.isArray(payload.fields) || payload.fields.length === 0) blockers.push("fields must be a non-empty array.");

  const fields = flattenFields(payload.fields);
  const fieldRefs = new Map();
  const choiceRefs = new Set();
  const positions = new Map();
  fields.forEach((field, index) => {
    const label = `fields[${index}]`;
    if (!REF_RE.test(field.ref || "")) blockers.push(`${label}.ref must match ${REF_RE}.`);
    else if (fieldRefs.has(field.ref)) blockers.push(`${label}.ref ${field.ref} is duplicated.`);
    else {
      fieldRefs.set(field.ref, field);
      positions.set(field.ref, index);
    }
    if (!nonEmpty(field.title)) blockers.push(`${label}.title is required.`);
    if (!FIELD_TYPES.has(field.type)) blockers.push(`${label}.type ${field.type ?? "<missing>"} is unsupported.`);
    validateImageAttachment(field.attachment, `${label}.attachment`, blockers);
    if (field.type === "inline_group") {
      const nested = field.properties?.fields;
      if (!Array.isArray(nested) || nested.length < 2) blockers.push(`${label}.properties.fields must contain at least two questions for an inline_group.`);
      for (const [childIndex, child] of (nested || []).entries()) {
        if (!INLINE_GROUP_CHILD_TYPES.has(child?.type)) {
          blockers.push(`${label}.properties.fields[${childIndex}].type ${child?.type ?? "<missing>"} is not supported inside an inline_group.`);
        }
      }
    }
    const choices = field.properties?.choices;
    if (Array.isArray(choices)) {
      for (const [choiceIndex, choice] of choices.entries()) {
        const choiceLabel = `${label}.properties.choices[${choiceIndex}]`;
        if (!nonEmpty(choice.label)) blockers.push(`${choiceLabel}.label is required.`);
        validateImageAttachment(choice.attachment, `${choiceLabel}.attachment`, blockers);
        if (field.type !== "dropdown") {
          if (!REF_RE.test(choice.ref || "")) blockers.push(`${choiceLabel}.ref must match ${REF_RE}.`);
          else if (choiceRefs.has(choice.ref)) blockers.push(`${choiceLabel}.ref ${choice.ref} is duplicated.`);
          else choiceRefs.add(choice.ref);
        }
      }
    }
    if (field.properties?.randomize === true && !["ranking", "multiple_choice", "picture_choice", "dropdown"].includes(field.type)) {
      blockers.push(`${label} enables randomize for unsupported field type ${field.type}.`);
    }
  });

  const hiddenRefs = new Set(Array.isArray(payload.hidden) ? payload.hidden : []);
  const thankyouRefs = new Set((payload.thankyou_screens || []).map((screen) => screen?.ref).filter(nonEmpty));
  const logicTriggers = new Set();

  function validateCondition(condition, label) {
    if (!condition || typeof condition !== "object") {
      blockers.push(`${label} must be an object.`);
      return;
    }
    if (!nonEmpty(condition.op)) blockers.push(`${label}.op is required.`);
    if (!Array.isArray(condition.vars)) blockers.push(`${label}.vars must be an array.`);
    if (["and", "or"].includes(condition.op)) {
      for (const [index, child] of (condition.vars || []).entries()) validateCondition(child, `${label}.vars[${index}]`);
      return;
    }
    for (const [index, variable] of (condition.vars || []).entries()) {
      const varLabel = `${label}.vars[${index}]`;
      if (!variable || typeof variable !== "object" || !nonEmpty(variable.type)) {
        blockers.push(`${varLabel}.type is required.`);
        continue;
      }
      if (variable.type === "field" && !fieldRefs.has(variable.value)) blockers.push(`${varLabel} references missing field ${variable.value}.`);
      if (variable.type === "choice" && !choiceRefs.has(variable.value)) blockers.push(`${varLabel} references missing choice ${variable.value}.`);
      if (variable.type === "hidden" && !hiddenRefs.has(variable.value)) blockers.push(`${varLabel} references missing hidden field ${variable.value}.`);
    }
  }

  for (const [logicIndex, rule] of (payload.logic || []).entries()) {
    const label = `logic[${logicIndex}]`;
    if (rule.type === "field") {
      if (!fieldRefs.has(rule.ref)) blockers.push(`${label}.ref references missing field ${rule.ref}.`);
      if (logicTriggers.has(`field:${rule.ref}`)) blockers.push(`${label} duplicates the Logic Jump definition for field ${rule.ref}.`);
      logicTriggers.add(`field:${rule.ref}`);
    } else if (rule.type === "hidden") {
      if (logicTriggers.has("hidden")) blockers.push(`${label} duplicates the hidden Logic Jump definition.`);
      logicTriggers.add("hidden");
    } else blockers.push(`${label}.type must be field or hidden.`);

    if (!Array.isArray(rule.actions) || rule.actions.length === 0) blockers.push(`${label}.actions must be non-empty.`);
    for (const [actionIndex, action] of (rule.actions || []).entries()) {
      const actionLabel = `${label}.actions[${actionIndex}]`;
      validateCondition(action.condition, `${actionLabel}.condition`);
      if (action.action === "jump") {
        const target = action.details?.to;
        if (!target || !["field", "thankyou", "outcome"].includes(target.type)) blockers.push(`${actionLabel}.details.to is invalid.`);
        else if (target.type === "field") {
          if (!fieldRefs.has(target.value)) blockers.push(`${actionLabel} jumps to missing field ${target.value}.`);
          else if (rule.type === "field" && positions.get(target.value) <= positions.get(rule.ref)) blockers.push(`${actionLabel} must jump forward in this workflow.`);
        } else if (target.type === "thankyou" && target.value !== "default" && !thankyouRefs.has(target.value)) {
          blockers.push(`${actionLabel} jumps to missing thank-you screen ${target.value}.`);
        }
      }
    }
  }

  if (!Array.isArray(payload.logic)) warnings.push("logic is omitted; use an explicit empty array when no branching is needed.");
  return {
    status: blockers.length > 0 ? "block" : warnings.length > 0 ? "warn" : "pass",
    blockers,
    warnings,
    summary: { field_count: fields.length, choice_ref_count: choiceRefs.size, logic_definition_count: Array.isArray(payload.logic) ? payload.logic.length : 0 },
  };
}

function projectLike(expected, actual) {
  if (Array.isArray(expected)) {
    if (expected.length === 0 && actual === undefined) return [];
    if (!Array.isArray(actual)) return actual;
    const canMatchByRef = expected.every((item) => item && typeof item === "object" && nonEmpty(item.ref));
    if (canMatchByRef) {
      const actualByRef = new Map(actual.filter((item) => item && typeof item === "object").map((item) => [item.ref, item]));
      return expected.map((item) => projectLike(item, actualByRef.get(item.ref)));
    }
    return expected.map((item, index) => projectLike(item, actual[index]));
  }
  if (expected && typeof expected === "object") {
    const output = {};
    for (const key of Object.keys(expected)) output[key] = projectLike(expected[key], actual?.[key]);
    return output;
  }
  return actual;
}

function collectDiff(expected, actual, currentPath = "$", out = [], equivalences = []) {
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) {
      out.push({ path: currentPath, expected, actual });
      return out;
    }
    if (expected.length !== actual.length) out.push({ path: `${currentPath}.length`, expected: expected.length, actual: actual.length });
    for (let i = 0; i < expected.length; i += 1) collectDiff(expected[i], actual[i], `${currentPath}[${i}]`, out, equivalences);
    return out;
  }
  if (expected && typeof expected === "object") {
    if (!actual || typeof actual !== "object") {
      out.push({ path: currentPath, expected, actual });
      return out;
    }
    for (const key of Object.keys(expected)) collectDiff(expected[key], actual[key], `${currentPath}.${key}`, out, equivalences);
    return out;
  }
  if (currentPath.endsWith(".attachment.href") && TYPEFORM_IMAGE_HREF_RE.test(expected || "") && TYPEFORM_IMAGE_HREF_RE.test(actual || "")) {
    if (expected !== actual) equivalences.push({ path: currentPath, kind: "typeform_image_copy", expected, actual });
    return out;
  }
  if (expected !== actual) out.push({ path: currentPath, expected, actual });
  return out;
}

function semanticDiff(expected, readback) {
  const projected = projectLike(expected, readback);
  const equivalences = [];
  const differences = collectDiff(expected, projected, "$", [], equivalences);
  return { status: differences.length === 0 ? "pass" : "block", differences, equivalences };
}

function semanticDiffEvidence(expected, readback) {
  return {
    schema_version: "1.0",
    generated_by: "typeform_api.js",
    expected_payload_sha256: crypto.createHash("sha256").update(JSON.stringify(expected)).digest("hex"),
    readback_sha256: crypto.createHash("sha256").update(JSON.stringify(readback)).digest("hex"),
    ...semanticDiff(expected, readback),
  };
}

function idMapping(form) {
  return {
    form_id: form.id || null,
    display_url: form._links?.display || null,
    fields: flattenFields(form.fields).map((field) => ({
      ref: field.ref,
      id: field.id || null,
      choices: (field.properties?.choices || []).map((choice) => ({ ref: choice.ref || null, id: choice.id || null, label: choice.label })),
    })),
  };
}

function hydrateIds(expectedFields, currentFields) {
  const currentFlat = new Map(flattenFields(currentFields).filter((field) => nonEmpty(field.ref)).map((field) => [field.ref, field]));
  for (const field of flattenFields(expectedFields)) {
    const current = currentFlat.get(field.ref);
    if (!current) continue;
    if (current.id) field.id = current.id;
    const currentChoices = new Map((current.properties?.choices || []).filter((choice) => nonEmpty(choice.ref)).map((choice) => [choice.ref, choice]));
    for (const choice of field.properties?.choices || []) {
      const existing = currentChoices.get(choice.ref);
      if (existing?.id) choice.id = existing.id;
    }
  }
}

function apiBase() {
  const value = (process.env.TYPEFORM_API_BASE || "https://api.typeform.com").replace(/\/$/, "");
  if (!OFFICIAL_BASE_URLS.has(value)) throw new Error(`TYPEFORM_API_BASE must be an official Typeform API URL; got ${value}.`);
  return value;
}

function token() {
  const value = process.env.TYPEFORM_ACCESS_TOKEN;
  if (!nonEmpty(value)) throw new Error("TYPEFORM_ACCESS_TOKEN is required.");
  return value;
}

async function wait(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function apiRequest(method, endpoint, body = undefined) {
  const url = `${apiBase()}${endpoint}`;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const response = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${token()}`, Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let parsed = null;
    if (text) {
      try { parsed = JSON.parse(text); } catch { parsed = { raw: text }; }
    }
    if (response.ok) return { status: response.status, headers: Object.fromEntries(response.headers.entries()), body: parsed };
    if (![429, 503].includes(response.status) || attempt === 3) {
      throw new Error(`Typeform API ${method} ${endpoint} failed with HTTP ${response.status}; response body omitted to avoid exposing survey data.`);
    }
    const retryAfter = Number(response.headers.get("retry-after"));
    await wait(Number.isFinite(retryAfter) ? Math.min(retryAfter * 1000, 30000) : 500 * (2 ** attempt));
  }
  throw new Error("Unreachable retry state.");
}

function formIdFromCreate(result) {
  if (nonEmpty(result.body?.id)) return result.body.id;
  const location = result.headers?.location;
  if (nonEmpty(location)) return location.split("/").filter(Boolean).at(-1);
  throw new Error("Create succeeded but form ID was not present in body or Location header.");
}

function confirmFormId(options, formId) {
  if (options.confirmFormId !== formId) throw new Error("--confirm-form-id must exactly match the target form_id.");
}

function validateLaunchGate(gate, formId) {
  const blockers = [];
  if (!gate || typeof gate !== "object" || Array.isArray(gate)) blockers.push("Launch gate must be an object.");
  else {
    if (gate.platform !== "typeform") blockers.push("launch gate platform must be typeform.");
    if (gate.form_id !== formId) blockers.push("launch gate form_id must match the target form_id.");
    for (const field of ["survey_quality_gate_status", "readback_diff_status", "logic_test_status"]) {
      if (gate[field] !== "pass") blockers.push(`${field} must be pass.`);
    }
    if (gate.mobile_test_required === true && gate.mobile_test_status !== "pass") blockers.push("mobile_test_status must be pass when mobile testing is required.");
    if (gate.mobile_test_required !== true && !["pass", "not_required"].includes(gate.mobile_test_status)) blockers.push("mobile_test_status must be pass or not_required when mobile testing is optional.");
    if (gate.owner_publish_confirmation?.status !== "confirmed") blockers.push("owner publish confirmation must be confirmed.");
    if (!nonEmpty(gate.owner_publish_confirmation?.confirmed_by)) blockers.push("owner_publish_confirmation.confirmed_by is required.");
    if (!nonEmpty(gate.owner_publish_confirmation?.confirmed_at)) blockers.push("owner_publish_confirmation.confirmed_at is required.");
    if (!Array.isArray(gate.blockers) || gate.blockers.length > 0) blockers.push("launch gate blockers must be an empty array.");
  }
  return { status: blockers.length === 0 ? "pass" : "block", blockers };
}

async function commandCreate(payloadFile, options) {
  if (!nonEmpty(options.outDir)) throw new Error("create-private requires --out-dir.");
  const payload = readJson(payloadFile);
  const check = validatePayload(payload);
  if (check.status === "block") throw new Error(`Payload check blocked: ${JSON.stringify(check.blockers)}`);
  fs.mkdirSync(options.outDir, { recursive: true });
  writeJson(path.join(options.outDir, "typeform_form_payload.snapshot.json"), payload);
  const create = await apiRequest("POST", "/forms", payload);
  const formId = formIdFromCreate(create);
  writeJson(path.join(options.outDir, "typeform_create_result.json"), { form_id: formId, status: create.status, location: create.headers.location || null });
  const readback = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  writeJson(path.join(options.outDir, "typeform_readback.json"), readback);
  const diff = semanticDiffEvidence(payload, readback);
  writeJson(path.join(options.outDir, "typeform_readback_diff.json"), diff);
  writeJson(path.join(options.outDir, "typeform_id_mapping.json"), idMapping(readback));
  return { form_id: formId, readback_status: diff.status, output_dir: path.resolve(options.outDir) };
}

async function commandUploadImage(imageFile, options) {
  if (!nonEmpty(options.out)) throw new Error("upload-image requires --out.");
  const resolved = path.resolve(imageFile);
  const extension = path.extname(resolved).toLowerCase();
  if (![".gif", ".jpeg", ".jpg", ".png"].includes(extension)) throw new Error("upload-image supports GIF, JPEG, and PNG files only.");
  const uploadSource = options.uploadSource || "user_upload";
  if (!["user_upload", "stock_image", "stock_icon", "unknown"].includes(uploadSource)) throw new Error("--upload-source is invalid.");
  const sourceBytes = fs.readFileSync(resolved);
  const image = sourceBytes.toString("base64");
  const result = await apiRequest("POST", "/images", { file_name: path.basename(resolved), image, upload_source: uploadSource });
  const location = result.headers.location || result.body?.href || result.body?._links?.self || null;
  if (!nonEmpty(location) || !TYPEFORM_IMAGE_HREF_RE.test(location)) throw new Error("Image upload succeeded but no valid Typeform image URL was returned.");
  const output = {
    status: result.status,
    image_url: location,
    image_id: location.split("/").filter(Boolean).at(-1),
    file_name: path.basename(resolved),
    source_sha256: crypto.createHash("sha256").update(sourceBytes).digest("hex"),
  };
  writeJson(options.out, output);
  return output;
}

async function commandReplace(formId, payloadFile, options) {
  confirmFormId(options, formId);
  if (!nonEmpty(options.outDir)) throw new Error("replace-private requires --out-dir.");
  const payload = readJson(payloadFile);
  const check = validatePayload(payload);
  if (check.status === "block") throw new Error(`Payload check blocked: ${JSON.stringify(check.blockers)}`);
  const current = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  if (current?.settings?.is_public !== false) throw new Error("Refusing whole-form replacement because the target form is public.");
  fs.mkdirSync(options.outDir, { recursive: true });
  writeJson(path.join(options.outDir, `typeform_backup_${formId}.json`), current);
  const hydrated = JSON.parse(JSON.stringify(payload));
  hydrateIds(hydrated.fields, current.fields);
  writeJson(path.join(options.outDir, "typeform_replace_payload.snapshot.json"), hydrated);
  await apiRequest("PUT", `/forms/${encodeURIComponent(formId)}`, hydrated);
  const readback = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  const diff = semanticDiffEvidence(hydrated, readback);
  writeJson(path.join(options.outDir, "typeform_readback.json"), readback);
  writeJson(path.join(options.outDir, "typeform_readback_diff.json"), diff);
  writeJson(path.join(options.outDir, "typeform_id_mapping.json"), idMapping(readback));
  return { form_id: formId, readback_status: diff.status, output_dir: path.resolve(options.outDir) };
}

async function commandPublish(formId, options) {
  confirmFormId(options, formId);
  if (!nonEmpty(options.launchGate)) throw new Error("publish requires --launch-gate.");
  const gate = readJson(options.launchGate);
  const gateCheck = validateLaunchGate(gate, formId);
  if (gateCheck.status !== "pass") throw new Error(`Launch gate blocked: ${JSON.stringify(gateCheck.blockers)}`);
  const evidenceCheck = validateGeneratedGate(gate, options.launchGate);
  if (evidenceCheck.status !== "pass") throw new Error(`Launch gate evidence blocked: ${JSON.stringify(evidenceCheck.blockers)}`);
  const before = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  await apiRequest("PATCH", `/forms/${encodeURIComponent(formId)}`, [{ op: "replace", path: "/settings/is_public", value: true }]);
  const after = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  const result = { form_id: formId, was_public: before?.settings?.is_public === true, is_public: after?.settings?.is_public === true, display_url: after?._links?.display || null };
  if (options.out) writeJson(options.out, result);
  return result;
}

async function commandResponses(formId, options) {
  confirmFormId(options, formId);
  if (!nonEmpty(options.out)) throw new Error("responses requires --out.");
  if (!nonEmpty(options.since) || !nonEmpty(options.until)) throw new Error("responses requires both --since and --until to constrain test-data retrieval.");
  const responseType = options.responseType || "completed";
  if (!["completed", "partial", "started"].includes(responseType)) throw new Error("--response-type must be completed, partial, or started.");
  const params = new URLSearchParams({ page_size: "1000", since: options.since, until: options.until, response_type: responseType });
  const response = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}/responses?${params.toString()}`)).body;
  if ((response?.total_items ?? 0) > (response?.items || []).length) throw new Error("The selected response window exceeds one 1000-item page; narrow --since/--until before saving evidence.");
  const result = {
    schema_version: "1.0",
    platform: "typeform",
    form_id: formId,
    fetched_at: new Date().toISOString(),
    query: Object.fromEntries(params.entries()),
    total_items: response?.total_items ?? (response?.items || []).length,
    page_count: response?.page_count ?? null,
    items: response?.items || [],
  };
  writeJson(options.out, result);
  return { form_id: formId, total_items: result.total_items, saved_to: path.resolve(options.out) };
}

async function commandBeginResponseTest(formId, options) {
  confirmFormId(options, formId);
  if (!nonEmpty(options.payload) || !nonEmpty(options.identity) || !nonEmpty(options.outDir)) {
    throw new Error("begin-response-test requires --payload, --identity, and --out-dir.");
  }
  const payload = readJson(options.payload);
  const identity = readJson(options.identity);
  const before = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  const currentIdentity = verifyTypeformVersion(payload, before, formId);
  if (identity.status !== "pass" || identity.form_id !== formId || identity.instrument_sha256 !== currentIdentity.instrument_sha256 || currentIdentity.status !== "pass") {
    throw new Error("Current Typeform content does not match the approved instrument identity.");
  }
  fs.mkdirSync(options.outDir, { recursive: true });
  const transactionPath = path.join(options.outDir, "typeform_response_test_transaction.json");
  writeJson(path.join(options.outDir, "typeform_response_test_before.json"), before);
  const transaction = {
    schema_version: "1.0",
    operation: "typeform_response_test",
    form_id: formId,
    instrument_sha256: identity.instrument_sha256,
    original_is_public: before.settings?.is_public === true,
    started_at: new Date().toISOString(),
    status: "prepared",
  };
  writeJson(transactionPath, transaction);
  if (transaction.original_is_public !== true) {
    await apiRequest("PATCH", `/forms/${encodeURIComponent(formId)}`, [{ op: "replace", path: "/settings/is_public", value: true }]);
  }
  const after = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  if (after.settings?.is_public !== true) throw new Error(`Failed to make ${formId} public for the authorized response test; restore using ${transactionPath}.`);
  transaction.status = "active";
  transaction.public_started_at = new Date().toISOString();
  transaction.display_url = after._links?.display || null;
  writeJson(transactionPath, transaction);
  writeJson(path.join(options.outDir, "typeform_response_test_public_readback.json"), after);
  return { form_id: formId, status: transaction.status, display_url: transaction.display_url, transaction: path.resolve(transactionPath) };
}

async function commandEndResponseTest(transactionFile, options) {
  const transaction = readJson(transactionFile);
  const formId = transaction.form_id;
  confirmFormId(options, formId);
  if (transaction.operation !== "typeform_response_test" || !nonEmpty(options.outDir)) throw new Error("end-response-test requires a valid transaction file and --out-dir.");
  const before = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  const originalState = transaction.original_is_public === true;
  if ((before.settings?.is_public === true) !== originalState) {
    await apiRequest("PATCH", `/forms/${encodeURIComponent(formId)}`, [{ op: "replace", path: "/settings/is_public", value: originalState }]);
  }
  const after = (await apiRequest("GET", `/forms/${encodeURIComponent(formId)}`)).body;
  const restored = (after.settings?.is_public === true) === originalState;
  const result = {
    ...transaction,
    status: restored ? "restored" : "restore_failed",
    restored_at: new Date().toISOString(),
    public_state_before_restore: before.settings?.is_public === true,
    restored_is_public: after.settings?.is_public === true,
  };
  writeJson(path.join(options.outDir, "typeform_response_test_restore_readback.json"), after);
  writeJson(transactionFile, result);
  writeJson(path.join(options.outDir, "typeform_response_test_restore_result.json"), result);
  if (!restored) throw new Error(`Failed to restore public state for ${formId}.`);
  return { form_id: formId, status: result.status, restored_is_public: result.restored_is_public };
}

function selfTest() {
  const good = {
    title: "Test",
    type: "branching",
    settings: { is_public: false, language: "en" },
    fields: [
      { ref: "q1", title: "Continue?", type: "multiple_choice", properties: { allow_multiple_selection: false, randomize: false, choices: [{ ref: "q1_yes", label: "Yes" }, { ref: "q1_no", label: "No" }] }, validations: { required: true } },
      { ref: "q2", title: "Why?", type: "long_text", validations: { required: false } },
    ],
    thankyou_screens: [{ ref: "thanks", title: "Thanks" }],
    logic: [{ type: "field", ref: "q1", actions: [{ action: "jump", details: { to: { type: "thankyou", value: "thanks" } }, condition: { op: "is", vars: [{ type: "field", value: "q1" }, { type: "choice", value: "q1_no" }] } }] }],
  };
  const result = validatePayload(good);
  if (result.status !== "pass") throw new Error(`Valid fixture failed: ${JSON.stringify(result)}`);
  const badBackward = JSON.parse(JSON.stringify(good));
  badBackward.logic = [{ type: "field", ref: "q2", actions: [{ action: "jump", details: { to: { type: "field", value: "q1" } }, condition: { op: "always", vars: [] } }] }];
  if (validatePayload(badBackward).status !== "block") throw new Error("Backward jump fixture did not block.");
  const badPublic = JSON.parse(JSON.stringify(good));
  badPublic.settings.is_public = true;
  if (validatePayload(badPublic).status !== "block") throw new Error("Public build fixture did not block.");
  const readback = JSON.parse(JSON.stringify(good));
  readback.id = "abc";
  readback.fields[0].id = "field-1";
  if (semanticDiff(good, readback).status !== "pass") throw new Error("Read-back projection fixture failed.");
  const inline = JSON.parse(JSON.stringify(good));
  inline.fields = [{ ref: "page", title: "One page", type: "inline_group", properties: { fields: [
    { ref: "page_a", title: "A", type: "short_text", validations: { required: false } },
    { ref: "page_b", title: "B", type: "yes_no", validations: { required: true } },
  ] } }];
  inline.logic = [];
  if (validatePayload(inline).status !== "pass") throw new Error("Valid inline_group fixture failed.");
  const badInline = JSON.parse(JSON.stringify(inline));
  badInline.fields[0].properties.fields[1].type = "payment";
  if (validatePayload(badInline).status !== "block") throw new Error("Invalid inline_group child fixture did not block.");
  const imageExpected = { title: "Image", settings: { is_public: false }, fields: [{ ref: "q", title: "Q", type: "statement", attachment: { type: "image", href: "https://images.typeform.com/images/source1", properties: { description: "Abstract test image", decorative: false } } }], logic: [] };
  const imageReadback = JSON.parse(JSON.stringify(imageExpected));
  imageReadback.fields[0].attachment.href = "https://images.typeform.com/images/copied2";
  delete imageReadback.logic;
  const imageDiff = semanticDiff(imageExpected, imageReadback);
  if (imageDiff.status !== "pass" || imageDiff.equivalences.length !== 1) throw new Error("Image-copy or omitted-empty-array normalization fixture failed.");
  const goodLaunchGate = {
    platform: "typeform",
    form_id: "abc",
    survey_quality_gate_status: "pass",
    readback_diff_status: "pass",
    logic_test_status: "pass",
    mobile_test_required: true,
    mobile_test_status: "pass",
    owner_publish_confirmation: { status: "confirmed", confirmed_by: "owner", confirmed_at: "2026-09-07T00:00:00Z" },
    blockers: [],
  };
  if (validateLaunchGate(goodLaunchGate, "abc").status !== "pass") throw new Error("Valid launch gate fixture failed.");
  goodLaunchGate.mobile_test_status = "not_run";
  if (validateLaunchGate(goodLaunchGate, "abc").status !== "block") throw new Error("Incomplete launch gate fixture did not block.");
  goodLaunchGate.mobile_test_required = false;
  goodLaunchGate.mobile_test_status = "not_required";
  if (validateLaunchGate(goodLaunchGate, "abc").status !== "pass") throw new Error("Optional mobile launch gate fixture failed.");
  process.stdout.write("typeform_api self-test: pass\n");
}

async function main(argv) {
  const options = parseArgs(argv);
  const [command, ...positional] = options.positional;
  if (options.help || command === "help") {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (command === "self-test") return selfTest();
  if (command === "check" && positional[0]) {
    const result = validatePayload(readJson(positional[0]));
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.status === "block") process.exitCode = 1;
    return;
  }
  if (command === "diff" && positional[0] && positional[1]) {
    const result = semanticDiffEvidence(readJson(positional[0]), readJson(positional[1]));
    if (options.out) writeJson(options.out, result);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.status === "block") process.exitCode = 1;
    return;
  }
  if (command === "get" && positional[0]) {
    const result = (await apiRequest("GET", `/forms/${encodeURIComponent(positional[0])}`)).body;
    if (options.out) writeJson(options.out, result);
    else process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    return;
  }
  if (command === "upload-image" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandUploadImage(positional[0], options), null, 2)}\n`);
    return;
  }
  if (command === "create-private" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandCreate(positional[0], options), null, 2)}\n`);
    return;
  }
  if (command === "replace-private" && positional[0] && positional[1]) {
    process.stdout.write(`${JSON.stringify(await commandReplace(positional[0], positional[1], options), null, 2)}\n`);
    return;
  }
  if (command === "begin-response-test" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandBeginResponseTest(positional[0], options), null, 2)}\n`);
    return;
  }
  if (command === "end-response-test" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandEndResponseTest(positional[0], options), null, 2)}\n`);
    return;
  }
  if (command === "responses" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandResponses(positional[0], options), null, 2)}\n`);
    return;
  }
  if (command === "publish" && positional[0]) {
    process.stdout.write(`${JSON.stringify(await commandPublish(positional[0], options), null, 2)}\n`);
    return;
  }
  process.stderr.write(`${usage()}\n`);
  process.exitCode = 2;
}

if (require.main === module) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`typeform_api failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { validatePayload, semanticDiff, hydrateIds, validateLaunchGate };

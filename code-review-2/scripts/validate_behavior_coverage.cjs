// Record-integrity check only: this cannot prove that a reviewer read the code.
const fs = require('node:fs');

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasText = (value) => typeof value === 'string' && value.trim().length > 0;
const isSha = (value) => typeof value === 'string' && /^[a-f0-9]{40}$/.test(value);

function validateCoverage(report, expected = {}) {
  const errors = [];
  const requireThat = (condition, label) => { if (!condition) errors.push(label); };
  const result = (status) => ({
    status, errors, checked_entries: Array.isArray(report?.entries) ? report.entries.length : 0,
    audit_mode: report?.audit?.mode ?? 'none',
  });
  if (!isObject(report) || !isObject(expected)) {
    errors.push('report and expected revisions must be objects');
    return result('incomplete');
  }
  requireThat(report.schema_version === 1, 'schema_version must be 1');
  for (const key of ['base_sha', 'head_sha']) {
    requireThat(isSha(expected[key]) && report[key] === expected[key], `${key} must match the fixed review revision`);
  }
  requireThat(hasText(report.reviewer_id), 'reviewer_id is required');

  function checkEvidence(refs, label, requiredRevision) {
    if (!Array.isArray(refs) || refs.length === 0) {
      errors.push(`${label} is required`);
      return;
    }
    for (const ref of refs) {
      const valid = isObject(ref) && hasText(ref.locator) && isSha(ref.revision);
      requireThat(valid, `${label} requires pinned revisions and locators`);
      if (!valid) continue;
      if (ref.repository !== undefined) {
        requireThat(hasText(ref.repository), `${label} external repository must be identified`);
      } else {
        requireThat([expected.base_sha, expected.head_sha].includes(ref.revision), `${label} has stale local evidence`);
      }
    }
    requireThat(refs.some((ref) => isObject(ref) && ref.repository === undefined &&
      ref.revision === requiredRevision && hasText(ref.locator)), `${label} needs evidence at the required local revision`);
  }

  requireThat(report.scope?.status === 'complete', 'source scope is not complete');
  checkEvidence(report.scope?.evidence, 'scope.evidence', expected.head_sha);
  requireThat(Array.isArray(report.entries), 'entries must be an array');

  if (report.applicability === 'not_applicable') {
    requireThat(hasText(report.reason), 'not_applicable scope requires a reason');
    requireThat(Array.isArray(report.entries) && report.entries.length === 0, 'not_applicable scope must not hide entries');
    return result(errors.length ? 'incomplete' : 'not_applicable');
  }
  requireThat(report.applicability === 'required', 'applicability must be required or not_applicable');
  const entries = Array.isArray(report.entries) ? report.entries : [];
  requireThat(entries.length > 0, 'changed behavior requires an entry inventory');
  const ids = new Set();
  entries.forEach((entry, index) => {
    const label = `entries[${index}]`;
    if (!isObject(entry)) {
      errors.push(`${label} must be an object`);
      return;
    }
    for (const key of ['id', 'entry', 'intent', 'condition', 'before', 'after', 'expected']) {
      requireThat(hasText(entry[key]), `${label}.${key} is required`);
    }
    requireThat(!ids.has(entry.id), `${label} has a duplicate id`);
    ids.add(entry.id);
    checkEvidence(entry.before_evidence, `${label}.before_evidence`, expected.base_sha);
    checkEvidence(entry.after_evidence, `${label}.after_evidence`, expected.head_sha);
    if (entry.status === 'verified') {
      requireThat(['preserved', 'intended_change', 'regression'].includes(entry.outcome), `${label}.outcome is required`);
      if (entry.outcome === 'regression') requireThat(hasText(entry.finding), `${label} regression requires a finding reference`);
    } else if (entry.status === 'not_applicable') {
      requireThat(hasText(entry.reason), `${label} exclusion requires a reason`);
    } else {
      errors.push(`${label} remains unverified or has an invalid status`);
    }
  });
  requireThat(entries.some((entry) => entry?.status === 'verified'), 'changed behavior cannot exclude every entry');

  const audit = report.audit;
  if (!isObject(audit)) {
    errors.push('omission audit is required even when there are no findings');
    return result('incomplete');
  }
  requireThat(audit.status === 'complete', 'omission audit is not complete');
  for (const key of ['base_sha', 'head_sha']) {
    requireThat(audit[key] === expected[key], `audit.${key} must match the fixed review revision`);
  }
  requireThat(hasText(audit.reviewer_id), 'audit.reviewer_id is required');
  requireThat(hasText(audit.notes), 'audit.notes must describe the independent inventory or self-check');
  requireThat(['independent', 'self_check'].includes(audit.mode), 'audit.mode is invalid');
  if (audit.mode === 'independent') {
    requireThat(audit.reviewer_id !== report.reviewer_id, 'independent audit requires a different reviewer');
  } else if (audit.mode === 'self_check') {
    requireThat(audit.reviewer_id === report.reviewer_id, 'self_check must identify the original reviewer');
  }
  checkEvidence(audit.evidence, 'audit.evidence', expected.head_sha);
  const auditIds = audit.required_entry_ids;
  requireThat(Array.isArray(auditIds) && auditIds.every(hasText), 'audit.required_entry_ids must be an array of nonempty ids');
  if (Array.isArray(auditIds)) {
    requireThat(new Set(auditIds).size === auditIds.length, 'audit contains duplicate entry ids');
    requireThat(ids.size === auditIds.length && auditIds.every((id) => ids.has(id)), 'inventory and omission audit do not cover the same entries');
  }
  return result(errors.length ? 'incomplete' : 'complete');
}

if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length !== 2 || !args.every(isSha)) {
    process.stderr.write('Usage: node validate_behavior_coverage.cjs <base-sha> <head-sha> < coverage.json\n');
    process.exitCode = 2;
  } else {
    try {
      const report = JSON.parse(fs.readFileSync(0, 'utf8'));
      const result = validateCoverage(report, { base_sha: args[0], head_sha: args[1] });
      process.stdout.write(`${JSON.stringify(result)}\n`);
      process.exitCode = result.status === 'incomplete' ? 1 : 0;
    } catch {
      process.stderr.write('Unable to read valid coverage JSON from stdin.\n');
      process.exitCode = 2;
    }
  }
}

module.exports = { validateCoverage };

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { test } = require('node:test');
const { validateCoverage } = require('../validate_behavior_coverage.cjs');

const BASE = 'a'.repeat(40);
const HEAD = 'b'.repeat(40);
const expected = { base_sha: BASE, head_sha: HEAD };
const evidence = (revision) => [{ revision, locator: 'router.js:12' }];

function fixture() {
  return {
    schema_version: 1, ...expected, reviewer_id: 'review-run-1',
    scope: { status: 'complete', evidence: evidence(HEAD) },
    applicability: 'required',
    entries: ['first-entry/on', 'explicit-action/on', 'explicit-action/off'].map((id) => ({
      id, entry: 'caller.js:8 -> router.js:12', intent: id,
      condition: id.split('/')[1], status: 'verified',
      before: 'Destination A', after: 'Destination A', expected: 'Preserve destination A',
      outcome: 'preserved', before_evidence: evidence(BASE), after_evidence: evidence(HEAD),
    })),
    audit: {
      ...expected, reviewer_id: 'audit-run-2', mode: 'independent', status: 'complete',
      required_entry_ids: ['first-entry/on', 'explicit-action/on', 'explicit-action/off'],
      evidence: evidence(HEAD), notes: 'Enumerated callers and dispatch branches from source.',
    },
  };
}

test('complete evidence records coverage, not a product approval', () => {
  const result = validateCoverage(fixture(), expected);
  assert.equal(result.status, 'complete');
  assert.equal(result.audit_mode, 'independent');
  assert.equal(result.checked_entries, 3);
  assert.equal(result.approved, undefined);
});

test('verified intended changes and verified regressions both count as reviewed', () => {
  const report = fixture();
  report.entries[0].after = 'Destination B';
  report.entries[0].expected = 'Move first entry to destination B';
  report.entries[0].outcome = 'intended_change';
  report.entries[1].after = 'Destination B';
  report.entries[1].outcome = 'regression';
  report.entries[1].finding = 'F1';
  assert.equal(validateCoverage(report, expected).status, 'complete');
});

const rejectedCases = {
  'missing caller with no findings': (r) => { r.entries.splice(1, 1); },
  'missing flag branch': (r) => { r.entries.pop(); },
  'audit did not cover a recorded entry': (r) => { r.audit.required_entry_ids.pop(); },
  'unverified entry': (r) => { r.entries[0].status = 'unverified'; r.entries[0].reason = 'Source unavailable'; },
  'stale head': (r) => { r.head_sha = 'c'.repeat(40); },
  'stale base': (r) => { r.base_sha = 'c'.repeat(40); },
  'stale omission audit': (r) => { r.audit.head_sha = 'c'.repeat(40); },
  'wrong before revision': (r) => { r.entries[0].before_evidence = evidence(HEAD); },
  'wrong after revision': (r) => { r.entries[0].after_evidence = evidence(BASE); },
  'missing evidence': (r) => { r.entries[0].after_evidence = []; },
  'blank evidence locator': (r) => { r.entries[0].after_evidence[0].locator = ' '; },
  'missing intent': (r) => { delete r.entries[0].intent; },
  'missing expected behavior': (r) => { delete r.entries[0].expected; },
  'unknown comparison result': (r) => { r.entries[0].outcome = 'looks good'; },
  'regression without finding reference': (r) => { r.entries[0].outcome = 'regression'; },
  'duplicate entry': (r) => { r.entries.push({ ...r.entries[0] }); },
  'duplicate audit entry': (r) => { r.audit.required_entry_ids.push(r.audit.required_entry_ids[0]); },
  'missing omission audit even with zero findings': (r) => { delete r.audit; },
  'unfinished omission audit': (r) => { r.audit.status = 'incomplete'; },
  'self review labeled independent': (r) => { r.audit.reviewer_id = r.reviewer_id; },
  'different reviewer labeled self-check': (r) => { r.audit.mode = 'self_check'; },
  'partial source scope': (r) => { r.scope.status = 'partial'; },
  'unknown source scope': (r) => { r.scope.status = 'unknown'; },
  'missing scope evidence': (r) => { r.scope.evidence = []; },
  'all changed behavior excluded': (r) => {
    r.entries.forEach((e) => { e.status = 'not_applicable'; e.reason = 'Excluded'; });
  },
  'invalid entries type': (r) => { r.entries = {}; },
  'invalid audit entry type': (r) => { r.audit.required_entry_ids = [null]; },
  'unsupported schema': (r) => { r.schema_version = 2; },
};

for (const [name, mutate] of Object.entries(rejectedCases)) {
  test(`incomplete: ${name}`, () => {
    const report = fixture();
    mutate(report);
    const result = validateCoverage(report, expected);
    assert.equal(result.status, 'incomplete');
    assert.ok(result.errors.length > 0);
  });
}

test('bounded exclusions require reasons and remain in the audit inventory', () => {
  const report = fixture();
  report.entries[2].status = 'not_applicable';
  report.entries[2].reason = 'Unchanged provider branch is not reachable from changed dispatch.';
  assert.equal(validateCoverage(report, expected).status, 'complete');
  delete report.entries[2].reason;
  assert.equal(validateCoverage(report, expected).status, 'incomplete');
});

test('single-agent fallback is explicit and cannot claim independent verification', () => {
  const report = fixture();
  report.audit.mode = 'self_check';
  report.audit.reviewer_id = report.reviewer_id;
  const result = validateCoverage(report, expected);
  assert.equal(result.status, 'complete');
  assert.equal(result.audit_mode, 'self_check');
});

test('mechanical-only scope can be excluded with evidence and reason', () => {
  const report = fixture();
  report.applicability = 'not_applicable';
  report.reason = 'Whitespace-only changes; no dispatch, state or contract change.';
  report.entries = [];
  delete report.audit;
  assert.equal(validateCoverage(report, expected).status, 'not_applicable');
  delete report.reason;
  assert.equal(validateCoverage(report, expected).status, 'incomplete');
});

test('extra cross-repository evidence needs its own pinned revision', () => {
  const report = fixture();
  report.entries[0].after_evidence.push({ repository: 'app/bridge', revision: 'd'.repeat(40), locator: 'bridge.js:3' });
  assert.equal(validateCoverage(report, expected).status, 'complete');
  report.entries[0].after_evidence[1].revision = 'main';
  assert.equal(validateCoverage(report, expected).status, 'incomplete');
});

test('new entry can use fixed base tree absence as before evidence', () => {
  const report = fixture();
  Object.assign(report.entries[0], {
    entry: 'src/new-route.js:16',
    before: 'No route exists in the base tree',
    after: 'The new route returns the intended destination',
    before_evidence: [{ revision: BASE, locator: 'base-tree-index: src/new-route.js absent' }],
    after_evidence: [{ revision: HEAD, locator: 'src/new-route.js:16' }],
  });
  // The validator checks the fixed revision and locator shape; the reviewer must verify the claim.
  assert.equal(validateCoverage(report, expected).status, 'complete');
});

test('pinned external evidence cannot replace required local base and head evidence', () => {
  const report = fixture();
  const externalOnly = [{ repository: 'app/bridge', revision: 'd'.repeat(40), locator: 'bridge.js:3' }];
  report.entries[0].before_evidence = externalOnly;
  report.entries[0].after_evidence = externalOnly;
  assert.equal(validateCoverage(report, expected).status, 'incomplete');
});

test('invalid top-level and expected revisions fail closed without throwing', () => {
  for (const report of [null, [], 'pass', {}]) {
    assert.equal(validateCoverage(report, expected).status, 'incomplete');
  }
  assert.equal(validateCoverage(fixture(), { ...expected, head_sha: 'main' }).status, 'incomplete');
});

test('CLI validates stdin and communicates success, gaps and bad input by exit code', () => {
  const script = path.resolve(__dirname, '../validate_behavior_coverage.cjs');
  const run = (input, args = [BASE, HEAD]) => spawnSync(process.execPath, [script, ...args], { input, encoding: 'utf8' });
  assert.equal(run(JSON.stringify(fixture())).status, 0);
  const report = fixture();
  report.entries.pop();
  const incomplete = run(JSON.stringify(report));
  assert.equal(incomplete.status, 1);
  assert.equal(JSON.parse(incomplete.stdout).status, 'incomplete');
  assert.equal(run('{invalid json').status, 2);
  assert.equal(run('{}', [BASE]).status, 2);
});

// Diff-scoped review scenarios: the checker validates evidence records, not LLM relevance.
test('unavailable necessary CI context stays incomplete without inventing a regression', () => {
  const report = fixture();
  report.entries = [report.entries[0]];
  report.audit.required_entry_ids = [report.entries[0].id];
  Object.assign(report.entries[0], {
    entry: '.gitlab-ci.yml image consumer', intent: 'use the upgraded build image',
    status: 'unverified', reason: 'Relevant override semantics unavailable from safe projection',
  });
  delete report.entries[0].outcome;
  const result = validateCoverage(report, expected);
  assert.equal(result.status, 'incomplete');
  assert.equal(report.entries.some((entry) => entry.finding), false);
});

test('verified affected entry needs no unrelated CI job in its coverage set', () => {
  const report = fixture();
  report.entries = [report.entries[0]];
  report.audit.required_entry_ids = [report.entries[0].id];
  const result = validateCoverage(report, expected);
  assert.equal(result.status, 'complete');
  assert.equal(result.checked_entries, 1);
  // Completeness is relative to the justified set; relevance requires scenario evaluation.
  assert.equal(result.approved, undefined);
});

test('tiny authorization change with unavailable caller cannot use not-applicable escape', () => {
  const report = fixture();
  report.entries[0].status = 'unverified';
  report.entries[0].reason = 'Tenant authorization caller unavailable';
  assert.equal(validateCoverage(report, expected).status, 'incomplete');
});

test('diff-scoped capability is a separate versioned contract from source access', () => {
  const fs = require('node:fs');
  const refs = path.resolve(__dirname, '../../references');
  const extra = JSON.parse(fs.readFileSync(path.join(refs, 'diff-context-contract.json')));
  const original = JSON.parse(fs.readFileSync(path.join(refs, 'source-context-contract.json')));
  assert.deepEqual(extra, {
    schema_version: 1, capability: 'diff-scoped-context/v1', tool: 'read_review_source',
  });
  assert.deepEqual(original, {
    schema_version: 1, capability: 'fixed-source-context/v1', tool: 'read_review_source',
  });
});

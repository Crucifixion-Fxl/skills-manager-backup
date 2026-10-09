const test = require('node:test');
const assert = require('node:assert/strict');
const { verifyLocal, verifySandbox, verifyStaging, verifyUsage, bindReport } = require('../cli/src/verification');
const { ContractError } = require('../cli/src/contracts');
const { validateStagingTarget, verifyStagingSource } = require('../cli/src/staging-target');
const { schemaPath } = require('../cli/src/release');

const event = { type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'] };
const scenario = { schemaVersion: 1, kind: 'scenario', runId: 'run-101',
  host: 'vicohome', applicationId: 14, maxLagSeconds: 300,
  expectations: [{ event, count: 1, fields: { source: 'feed' } }] };
const captured = { events: [{ ...event, eventId: 'evt-101', fields: { source: 'feed' } }] };
const contract = { host: 'vicohome', applicationId: 14, application: 'smart_camera', events: [{ ...event, point: 'submit', parameters: [
  { name: 'source', valueType: 'string', isRequired: true },
] }] };
const usTarget = { schemaVersion: 1, kind: 'stagingTarget', region: 'us', environment: 'staging',
  collectorEndpoint: 'https://us-test-log.theunismart.com', warehouseId: 'athena-us-stage',
  allowedSourceTables: ['analytics_staging.dwd_event_feed_card_submit_clk_hi'] };

test('local assertions detect missing, duplicate and wrong payload', () => {
  assert.equal(verifyLocal(scenario, captured, contract).status, 'PASS');
  assert.throws(() => verifyLocal(scenario, { events: [] }, contract), e => e.code === 'EVENT_COUNT');
  assert.throws(() => verifyLocal(scenario, { events: [captured.events[0], captured.events[0]] }, contract), e => e.code === 'EVENT_COUNT');
  assert.throws(() => verifyLocal(scenario, { events: [{ ...captured.events[0], fields: { source: 'other' } }] }, contract), e => e.code === 'FIELD_MISMATCH');
  assert.throws(() => verifyLocal({ ...scenario, expectations: [] }, { events: [] }, contract),
    e => e.code === 'INVALID_SCENARIO');
  assert.throws(() => verifyLocal({ ...scenario, expectations: [{ event, count: 0 }] }, { events: [] }, contract),
    e => e.code === 'INVALID_SCENARIO');
});

test('local contract checks required fields, types and undeclared payload fields', () => {
  assert.throws(() => verifyLocal({ ...scenario, expectations: [{ event, count: 1 }] },
    { events: [{ ...event, fields: {} }] }, contract), e => e.code === 'REQUIRED_FIELD');
  assert.throws(() => verifyLocal({ ...scenario, expectations: [{ event, count: 1 }] },
    { events: [{ ...event, fields: { source: 42 } }] }, contract), e => e.code === 'FIELD_TYPE');
  assert.throws(() => verifyLocal({ ...scenario, expectations: [{ event, count: 1 }] },
    { events: [{ ...event, fields: { source: 'feed', extra: 'x' } }] }, contract), e => e.code === 'UNDECLARED_FIELD');
});

test('integer and null retain their JSON Schema value semantics', () => {
  const typed = { ...contract, events: [{ ...contract.events[0], parameters: [
    { name: 'count', valueType: 'integer', isRequired: true },
    { name: 'optional_value', valueType: 'null', isRequired: false },
  ] }] };
  const expected = { ...scenario, expectations: [{ event, count: 1 }] };
  assert.equal(verifyLocal(expected, { events: [{ ...event, fields: { count: 2, optional_value: null } }] }, typed).status, 'PASS');
  assert.throws(() => verifyLocal(expected, { events: [{ ...event, fields: { count: 2.5 } }] }, typed),
    e => e.code === 'FIELD_TYPE');
  assert.throws(() => verifyLocal(expected, { events: [{ ...event, fields: { count: 2, optional_value: 'x' } }] }, typed),
    e => e.code === 'FIELD_TYPE');
});

test('all TDD layers reject a scenario for another host or application', () => {
  assert.throws(() => verifyLocal({ ...scenario, applicationId: 999 }, captured, contract),
    e => e.code === 'SCENARIO_SCOPE');
  assert.throws(() => verifyLocal({ ...scenario, host: 'other' }, captured, contract),
    e => e.code === 'SCENARIO_SCOPE');
  const evidence = { namespace: 'ci-101-1', queryLimit: 50, truncated: false,
    good: [{ ...captured.events[0], schemaVersion: '1-0-7',
      schemaUri: `iglu:${schemaPath('smart_camera', contract.events[0], '1-0-7')}` }], bad: [] };
  assert.throws(() => verifySandbox({ ...scenario, applicationId: 999 }, evidence,
    { version: '1-0-7', expectedEventIds: ['evt-101'], contract, namespace: 'ci-101-1' }), e => e.code === 'SCENARIO_SCOPE');
  assert.throws(() => verifyStaging({ ...scenario, applicationId: 999 }, {
    runId: 'run-101', queryId: 'q', sentAt: '2026-10-02T04:04:00Z',
    now: '2026-10-02T04:05:00Z', watermark: '2026-10-02T04:05:00Z',
    queryWindow: { from: '2026-10-02T04:04:00Z', to: '2026-10-02T04:05:00Z' },
    region: 'us', warehouseId: 'athena-us-stage',
    sourceTables: ['analytics_staging.dwd_event_feed_card_submit_clk_hi'],
    collectorEndpoint: 'https://us-test-log.theunismart.com', rows: [], badRows: [],
  }, { version: '1-0-7', contract, target: usTarget }), e => e.code === 'SCENARIO_SCOPE');
});

test('sandbox requires exact run IDs, no bad and a complete query', () => {
  const evidence = { namespace: 'ci-101-1', queryLimit: 50, truncated: false,
    good: [{ ...captured.events[0], schemaVersion: '1-0-7',
      schemaUri: `iglu:${schemaPath('smart_camera', contract.events[0], '1-0-7')}` }], bad: [] };
  const input = { version: '1-0-7', expectedEventIds: ['evt-101'], contract, namespace: 'ci-101-1' };
  assert.throws(() => verifySandbox(scenario, evidence,
    { ...input, namespace: 'ci-101-other' }),
  e => e.code === 'NAMESPACE_MISMATCH');
  assert.equal(verifySandbox(scenario, evidence, input).status, 'PASS');
  assert.throws(() => verifySandbox(scenario, { ...evidence, good: [{ ...evidence.good[0], schemaUri: 'iglu:wrong' }] },
    input), e => e.code === 'SCHEMA_URI');
  assert.throws(() => verifySandbox(scenario, { ...evidence, bad: [{ eventId: 'evt-101' }] },
    input), e => e.code === 'BAD_EVENT');
  assert.throws(() => verifySandbox(scenario, { ...evidence, truncated: true },
    input), e => e.code === 'QUERY_TRUNCATED');
  assert.throws(() => verifySandbox(scenario, { ...evidence, good: [{ ...captured.events[0], eventId: 'old' }] },
    input), e => e.code === 'EVENT_ID_MISMATCH');
});

test('staging does not treat an incomplete warehouse watermark as zero events', () => {
  const evidence = { runId: 'run-101', collectorEndpoint: 'https://us-test-log.theunismart.com',
    region: 'us', warehouseId: 'athena-us-stage',
    queryId: 'athena-1', watermark: '2026-10-02T04:05:00Z',
    sentAt: '2026-10-02T04:04:00Z', now: '2026-10-02T04:05:00Z',
    sourceTables: ['analytics_staging.dwd_event_feed_card_submit_clk_hi'],
    queryWindow: { from: '2026-10-02T04:04:00Z', to: '2026-10-02T04:05:00Z' },
    rows: [{ ...captured.events[0], runId: 'run-101', schemaVersion: '1-0-7',
      schemaUri: `iglu:${schemaPath('smart_camera', contract.events[0], '1-0-7')}` }], badRows: [] };
  assert.equal(verifyStaging(scenario, evidence, { version: '1-0-7', contract, target: usTarget }).status, 'PASS');
  assert.throws(() => verifyStaging(scenario, { ...evidence, sourceTables: ['analytics.dwd_event_feed_card_submit_clk_hi'] },
    { version: '1-0-7', contract, target: usTarget }), e => e.code === 'INVALID_ENV');
  assert.throws(() => verifyStaging(scenario, { ...evidence, rows: [], watermark: '2026-10-02T04:03:00Z' },
    { version: '1-0-7', contract, target: usTarget }), e => e.code === 'WAREHOUSE_DELAY');
  assert.throws(() => verifyStaging(scenario, { ...evidence, rows: [], now: '2026-10-02T04:10:00Z' },
    { version: '1-0-7', contract, target: usTarget }), e => e.code === 'EVENT_COUNT');
  assert.throws(() => verifyStaging(scenario, { ...evidence, badRows: [{ runId: 'run-101' }] },
    { version: '1-0-7', contract, target: usTarget }), e => e.code === 'BAD_EVENT');
});

test('staging target accepts a reviewed regional collector and exact warehouse tables', () => {
  const target = { schemaVersion: 1, kind: 'stagingTarget', region: 'cn', environment: 'staging',
    collectorEndpoint: 'https://collector-cn-stage.example.test', warehouseId: 'athena-cn-stage',
    allowedSourceTables: ['tracking_stage.enriched', 'tracking_stage.bad_events'] };
  const evidence = { runId: 'run-101', region: 'cn', warehouseId: 'athena-cn-stage',
    collectorEndpoint: target.collectorEndpoint, queryId: 'athena-cn-1',
    watermark: '2026-10-02T04:05:00Z', sentAt: '2026-10-02T04:04:00Z', now: '2026-10-02T04:05:00Z',
    queryWindow: { from: '2026-10-02T04:04:00Z', to: '2026-10-02T04:05:00Z' },
    sourceTables: ['tracking_stage.enriched', 'tracking_stage.bad_events'],
    rows: [{ ...captured.events[0], runId: 'run-101', schemaVersion: '1-0-7',
      schemaUri: `iglu:${schemaPath('smart_camera', contract.events[0], '1-0-7')}` }], badRows: [] };
  const options = { version: '1-0-7', contract, target };
  assert.equal(verifyStaging(scenario, evidence, options).status, 'PASS');
  assert.throws(() => verifyStaging(scenario, { ...evidence, region: 'us' }, options), e => e.code === 'WRONG_REGION');
  assert.throws(() => verifyStaging(scenario, { ...evidence, sourceTables: ['tracking.enriched'] }, options),
    e => e.code === 'INVALID_ENV');
  assert.throws(() => verifyStaging(scenario, { ...evidence, collectorEndpoint: 'https://other.example.test' }, options),
    e => e.code === 'WRONG_ENDPOINT');
});

test('staging target rejects missing, insecure and ambiguous destinations', () => {
  assert.throws(() => validateStagingTarget(), e => e.code === 'INVALID_TARGET');
  assert.throws(() => validateStagingTarget({ ...usTarget, collectorEndpoint: 'http://collector.example.test' }),
    e => e.code === 'INVALID_TARGET');
  assert.throws(() => validateStagingTarget({ ...usTarget, collectorEndpoint: 'https://collector.example.test/custom' }),
    e => e.code === 'INVALID_TARGET');
  assert.throws(() => validateStagingTarget({ ...usTarget, allowedSourceTables: ['tracking.enriched', 'tracking.enriched'] }),
    e => e.code === 'INVALID_TARGET');
  assert.throws(() => validateStagingTarget({ ...usTarget, allowedSourceTables: ['tracking.*'] }),
    e => e.code === 'INVALID_TARGET');
  assert.throws(() => validateStagingTarget({ ...usTarget, region: 'CN staging' }),
    e => e.code === 'INVALID_TARGET');
});

test('staging source requires an explicit nonempty list even when called directly', () => {
  const evidence = { region: usTarget.region, collectorEndpoint: usTarget.collectorEndpoint,
    warehouseId: usTarget.warehouseId, sourceTables: [] };
  assert.throws(() => verifyStagingSource(usTarget, evidence), e => e.code === 'INVALID_EVIDENCE');
  assert.throws(() => verifyStagingSource(usTarget, { ...evidence, sourceTables: null }),
    e => e.code === 'INVALID_EVIDENCE');
});

test('usage is bound to selected issues and a defined event identity', () => {
  const target = { application: 'smart_camera', selectedIssues: ['101'], events: [{ ...event, point: 'submit', parameters: [] }] };
  assert.equal(verifyUsage(target, { events: [{ ...event, issue: '101' }] }).status, 'PASS');
  assert.throws(() => verifyUsage(target, { events: [] }), e => e.code === 'INVALID_USAGE');
  assert.throws(() => verifyUsage(target, { events: [{ ...event, issue: '102' }] }), e => e.code === 'UNSELECTED_USAGE');
  assert.throws(() => verifyUsage(target, { events: [{ ...event, spm: ['other', 'card', 'submit'], issue: '101' }] }), e => e.code === 'UNDECLARED_USAGE');
});

test('report binding rejects missing provenance', () => {
  const binding = { host: 'vicohome', applicationId: 14, commit: 'abc', artifactDigest: 'a1',
    contractDigest: 'b2', lockDigest: 'c3', releaseId: 77, version: '1-0-7' };
  assert.equal(bindReport('local', { status: 'PASS' }, binding).binding.commit, 'abc');
  assert.throws(() => bindReport('staging', { status: 'PASS' }, { ...binding, artifactDigest: '' }),
    e => e instanceof ContractError && e.code === 'MISSING_BINDING');
});

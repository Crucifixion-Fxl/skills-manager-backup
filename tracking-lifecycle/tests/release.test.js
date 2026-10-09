const test = require('node:test');
const assert = require('node:assert/strict');
const { schemaPath, verifyBaseline, publishCandidate, validateReports, assertPublishedBoundary } = require('../cli/src/release');
const { digest, project, ContractError, assertBaseline } = require('../cli/src/contracts');

const event = { type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'],
  point: 'submit', name: 'Submit', parameters: [{ name: 'source', valueType: 'string', isRequired: true }] };
const baseline = { schemaVersion: 1, kind: 'baseline', host: 'vicohome', application: 'smart_camera',
  applicationId: 14, release: { id: 10, version: '1-0-6', status: 3 }, events: [event] };
const change = { schemaVersion: 1, kind: 'change', host: 'vicohome', issue: '101', applicationId: 14,
  baseDigest: digest(baseline.events), operations: [{ event: { type: 'COMPONENT', trackerType: 'CLK', spm: event.spm },
    field: 'parameters.origin', before: { exists: false }, after: { valueType: 'string', isRequired: false } }] };
const candidate = { ...project(baseline, [change]), commit: 'abc', artifactDigest: 'sha256:artifact',
  releaseId: 11, version: '1-0-7' };
const binding = { host: 'vicohome', applicationId: 14, commit: 'abc', artifactDigest: 'sha256:artifact',
  contractDigest: digest(candidate.events), lockDigest: digest(candidate.events), releaseId: 11, version: '1-0-7' };
const reports = ['local', 'sandbox', 'staging'].map(phase => ({ phase, status: 'PASS',
  ...(phase === 'sandbox' ? { namespace: 'ci-101-202-303' } : {}),
  binding: phase === 'sandbox' ? { ...binding, namespace: 'ci-101-202-303' } : binding }));
const validUsage = { events: [{ type: event.type, trackerType: event.trackerType, spm: event.spm, issue: '101' }] };

test('release rejects a sandbox report from another namespace or CI pipeline', () => {
  const namespace = 'ci-101-202-303';
  const scoped = reports.map(report => report.phase === 'sandbox'
    ? { ...report, namespace, binding: { ...report.binding, namespace } } : report);
  assert.equal(validateReports(candidate, scoped, 'ci-101-202-'), undefined);
  const wrongBinding = scoped.map(report => report.phase === 'sandbox'
    ? { ...report, binding: { ...report.binding, namespace: 'ci-101-999-303' } } : report);
  assert.throws(() => validateReports(candidate, wrongBinding, 'ci-101-202-'),
    e => e.code === 'STALE_REPORT');
  assert.throws(() => validateReports(candidate, scoped, 'ci-101-999-'),
    e => e.code === 'STALE_REPORT');
});

test('schema path uses full SPM and workorder version', () => {
  assert.equal(schemaPath('smart_camera', event, '1-0-7'), 'com.smart_camera/feed_card_submit_clk/jsonschema/1-0-7');
  assert.equal(schemaPath('smart_camera', { type: 'PAGE', trackerType: 'BASE', spm: ['feed'], point: 'feed', parameters: [] }, '1-0-7'),
    'com.smart_camera/feed_pv/jsonschema/1-0-7');
});

test('baseline import requires exact released ID and production schema readback', async () => {
  const client = { listReleases: async () => [{ id: 10, releaseStatus: 3, version: '1-0-6' }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14, events: baseline.events }) };
  const schemaReader = { verify: async () => ({ verifiedPaths: ['path'], digest: 'schemas' }) };
  assert.equal((await verifyBaseline(client, schemaReader, baseline)).status, 'PASS');
  await assert.rejects(() => verifyBaseline(client, null, baseline), e => e.code === 'BLOCKED_DEPENDENCY');
  await assert.rejects(() => verifyBaseline({ ...client, listReleases: async () => [{ id: 10, releaseStatus: 4 }] }, schemaReader, baseline),
    e => e.code === 'BASELINE_NOT_RELEASED');
});

test('first release uses an explicit empty bootstrap boundary tied to the sole active workorder', async () => {
  const bootstrap = { schemaVersion: 1, kind: 'bootstrapBaseline', host: 'devium',
    application: 'device_cloud', applicationId: 3396,
    release: { id: 356, version: '1-0-4', status: 0 }, events: [] };
  const releases = [{ id: 356, version: '1-0-4', releaseStatus: 0 },
    { id: 355, releaseStatus: 4 }];
  const client = { listReleases: async () => releases,
    readTree: async () => ({ application: 'device_cloud', applicationId: 3396, events: [event] }) };
  assert.equal((await verifyBaseline(client, null, bootstrap)).status, 'PASS');
  assert.equal(assertPublishedBoundary(releases, bootstrap).id, 356);
  assert.deepEqual(project(bootstrap, []).events, []);
  assert.throws(() => assertBaseline({ ...bootstrap, events: [event] }), e => e.code === 'INVALID_BASELINE');
  await assert.rejects(() => verifyBaseline({ ...client, listReleases: async () => [
    ...releases, { id: 300, releaseStatus: 3, version: '1-0-0' },
  ] }, null, bootstrap), e => e.code === 'BASELINE_DRIFT');
  await assert.rejects(() => verifyBaseline({ ...client, listReleases: async () => [
    ...releases, { id: 357, releaseStatus: 0, version: '1-0-5' },
  ] }, null, bootstrap), e => e.code === 'WORKORDER_MISMATCH');
});

test('first release can publish only the selected live projection after all three reports', async () => {
  const bootstrap = { schemaVersion: 1, kind: 'bootstrapBaseline', host: 'vicohome',
    application: 'smart_camera', applicationId: 14,
    release: { id: 11, version: '1-0-7', status: 0 }, events: [] };
  const added = { type: 'SELF_DEFINE', trackerType: 'BASE', spm: ['action_completed'],
    point: 'action_completed', eventName: 'action_completed', name: 'Action completed', parameters: [] };
  const selected = { schemaVersion: 1, kind: 'change', host: 'vicohome', issue: '101',
    applicationId: 14, baseDigest: digest([]), operations: [{
      event: { type: 'SELF_DEFINE', trackerType: 'BASE', spm: ['action_completed'], eventName: 'action_completed' },
      field: '$event', before: { exists: false }, after: added,
    }] };
  const firstCandidate = { ...project(bootstrap, [selected]), commit: 'abc',
    artifactDigest: 'sha256:artifact', releaseId: 11, version: '1-0-7' };
  const firstBinding = { host: 'vicohome', applicationId: 14, commit: 'abc',
    contractDigest: digest(firstCandidate.events), lockDigest: digest(firstCandidate.events),
    releaseId: 11, version: '1-0-7', artifactDigest: 'sha256:artifact' };
  const firstReports = ['local', 'sandbox', 'staging'].map(phase => ({ phase, status: 'PASS',
    ...(phase === 'sandbox' ? { namespace: 'ci-101-202-303' } : {}),
    binding: { ...firstBinding, ...(phase === 'sandbox' ? { namespace: 'ci-101-202-303' } : {}) } }));
  const calls = [];
  const client = { listReleases: async () => [{ id: 11, version: '1-0-7',
    releaseStatus: calls.includes('online') ? 3 : 0 }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14, events: [added] }),
    getUnpassed: async () => ({ notUploaded: [], unpassed: [] }),
    validate: async () => { calls.push('validate'); return { passed: true }; },
    onlineRelease: async () => { calls.push('online'); } };
  const input = { client, schemaReader: { verify: async () => ({ digest: 'prod' }) },
    baseline: bootstrap, changes: [selected], candidate: firstCandidate, reports: firstReports,
    usage: { events: [{ type: added.type, trackerType: added.trackerType, spm: added.spm,
      eventName: added.eventName, issue: '101' }] } };
  assert.equal((await publishCandidate(input)).status, 'PASS');
  assert.deepEqual(calls, ['validate', 'online']);
  calls.length = 0;
  client.readTree = async () => ({ application: 'smart_camera', applicationId: 14,
    events: [{ ...added, name: 'Unselected live edit' }] });
  await assert.rejects(() => publishCandidate(input), e => e.code === 'UNOWNED_PLATFORM_CHANGE');
  assert.deepEqual(calls, []);
});

function fakeClient(overrides = {}) {
  const calls = [];
  const client = {
    calls,
    listReleases: async () => [{ id: 11, releaseStatus: calls.includes('online') ? 3 : 0,
      applicationId: 14, version: '1-0-7' }, { id: 10, releaseStatus: 3,
      applicationId: 14, version: '1-0-6' }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14, events: candidate.events }),
    getUnpassed: async () => ({ notUploaded: [], unpassed: [] }),
    validate: async () => { calls.push('validate'); return { passed: true }; },
    onlineRelease: async () => { calls.push('online'); },
    ...overrides,
  };
  return client;
}

test('publish calls existing API only after full preflight and validates exact released ID', async () => {
  const client = fakeClient();
  const schemaReader = { verify: async () => ({ digest: 'schema-digest' }) };
  const result = await publishCandidate({ client, schemaReader, baseline, changes: [change], candidate,
    reports, usage: validUsage });
  assert.equal(result.status, 'PASS');
  assert.deepEqual(client.calls, ['validate', 'online']);
});

test('unpassed old point or stale report prevents publish', async () => {
  const client = fakeClient({ getUnpassed: async () => ({ notUploaded: [{ eventPoint: 'legacy' }], unpassed: [] }) });
  const input = { client, schemaReader: { verify: async () => ({ digest: 'x' }) }, baseline, changes: [change], candidate,
    reports, usage: validUsage };
  await assert.rejects(() => publishCandidate(input), e => e.code === 'PLATFORM_VALIDATION');
  assert.deepEqual(client.calls, []);
  await assert.rejects(() => publishCandidate({ ...input, reports: [{ ...reports[0], binding: { ...binding, commit: 'old' } }, ...reports.slice(1)] }),
    e => e.code === 'STALE_REPORT');
});

test('publish rejects a stale released baseline before validation or API call', async () => {
  const client = fakeClient({ listReleases: async () => [
    { id: 11, releaseStatus: 0, applicationId: 14, version: '1-0-7' },
    { id: 12, releaseStatus: 3, applicationId: 14, version: '1-0-6' },
  ] });
  await assert.rejects(() => publishCandidate({ client, schemaReader: { verify: async () => ({ digest: 'x' }) },
    baseline, changes: [change], candidate, reports, usage: validUsage }),
  e => e.code === 'BASELINE_DRIFT');
  assert.deepEqual(client.calls, []);
});

test('unknown API outcome is read back and never blindly retried', async () => {
  let calls = 0;
  const client = fakeClient({ onlineRelease: async () => { calls++; throw new Error('timeout'); } });
  await assert.rejects(() => publishCandidate({ client, schemaReader: { verify: async () => ({ digest: 'x' }) },
    baseline, changes: [change], candidate, reports, usage: validUsage }),
  e => e instanceof ContractError && e.code === 'RELEASE_UNKNOWN');
  assert.equal(calls, 1);
});

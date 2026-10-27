'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { normalizeEvents, digest, project, planAgainstCurrent } = require('../cli/src/contracts');
const { schemaPath, IgluReader, verifyBaseline } = require('../cli/src/release');
const { PlatformClient, postChanges } = require('../cli/src/platform');
const { ProductionSchemaReader, verifyPublishedSnapshot } = require('../cli/src/published-release');

const custom = point => ({ type: 'SELF_DEFINE', trackerType: 'BASE', spm: [point],
  point, eventName: point, name: point, parameters: [], baseSchemas: [] });
const page = point => ({ type: 'PAGE', trackerType: 'BASE', spm: [point],
  point, name: point, parameters: [], baseSchemas: [] });
const released = events => ({ schemaVersion: 1, kind: 'baseline', host: 'nature',
  application: 'smart_camera', applicationId: 14,
  release: { id: 10, version: '1-0-6', status: 3 }, events });
const addition = (baseline, added) => ({ schemaVersion: 1, kind: 'change', host: baseline.host,
  applicationId: baseline.applicationId, issue: '337', baseDigest: digest(baseline.events),
  operations: [{ event: added, field: '$event', before: { exists: false }, after: added }] });
const renamedAddition = (baseline, added, renamed) => {
  const change = addition(baseline, added);
  for (const field of ['spm', 'point', ...(added.eventName ? ['eventName'] : [])]) {
    change.operations.push({ event: added, field, before: { exists: false }, after: renamed[field] });
  }
  return change;
};

test('released digit-leading names survive unrelated new-event projection unchanged', () => {
  const legacyEvents = [custom('4g_non_vip_alert'), page('4g_settings')];
  const baseline = released(legacyEvents);
  const change = addition(baseline, custom('paywall_loading_finished'));
  const candidate = project(baseline, [change]);
  for (const old of legacyEvents) assert.deepEqual(candidate.events.find(e => e.point === old.point), old);
  assert.equal(planAgainstCurrent(baseline, baseline, [change]).complete, false);
  assert.equal(planAgainstCurrent(baseline, candidate, [change]).complete, true);
  assert.deepEqual(baseline.events, legacyEvents);
});

test('new names still require a letter at every SPM level, including bootstrap', () => {
  const baseline = released([]);
  const bootstrap = { ...baseline, kind: 'bootstrapBaseline', release: { id: 11, version: '1-0-7', status: 0 } };
  for (const added of [custom('4g_new_event'), page('4g_new_page'),
    { type: 'MODULE', trackerType: 'CLK', spm: ['home', '4g_card'], point: '4g_card', parameters: [] },
    { type: 'COMPONENT', trackerType: 'EXP', spm: ['home', 'card', '4g_button'], point: '4g_button', parameters: [] }]) {
    for (const boundary of [baseline, bootstrap]) {
      assert.throws(() => project(boundary, [addition(boundary, added)]),
        error => error.code === 'INVALID_EVENT_IDENTITY');
    }
  }
});

test('a pre-existing live name absent from the released baseline is not grandfathered', () => {
  const baseline = released([custom('existing')]);
  const change = addition(baseline, custom('paywall_loading_finished'));
  const live = { ...baseline, events: [...baseline.events, custom('4g_unreleased')] };
  assert.throws(() => planAgainstCurrent(baseline, live, [change]),
    error => error.code === 'UNOWNED_PLATFORM_CHANGE');
});

test('new identities cannot gain legacy compatibility through subsequent field edits', () => {
  const baseline = released([]);
  const bootstrap = { ...baseline, kind: 'bootstrapBaseline', release: { id: 11, version: '1-0-7', status: 0 } };
  const pairs = [
    [custom('new_event'), custom('4g_new_event')],
    [page('new_page'), page('4g_new_page')],
    [{ type: 'MODULE', trackerType: 'CLK', spm: ['home', 'card'], point: 'card', parameters: [] },
      { spm: ['4g_home', 'card'], point: 'card' }],
    [{ type: 'COMPONENT', trackerType: 'EXP', spm: ['home', 'card', 'button'], point: 'button', parameters: [] },
      { spm: ['home', '4g_card', 'button'], point: 'button' }],
  ];
  for (const boundary of [baseline, bootstrap]) {
    for (const [added, renamed] of pairs) {
      assert.throws(() => project(boundary, [renamedAddition(boundary, added, renamed)]),
        error => error.code === 'INVALID_EVENT_IDENTITY');
    }
  }
});

test('post rejects a new identity renamed to a legacy name before any platform write', async () => {
  const baseline = released([custom('4g_existing')]);
  const change = renamedAddition(baseline, custom('new_event'), custom('4g_new_event'));
  const writes = [];
  let events = baseline.events;
  const client = { listReleases: async () => [
    { id: 10, version: '1-0-6', releaseStatus: 3 }, { id: 11, version: '1-0-7', releaseStatus: 0 },
  ], readTree: async () => ({ ...baseline, events }),
  createEvent: async payload => {
    writes.push(payload);
    events = [...events, { ...custom(payload.events[0].point), name: payload.events[0].name }];
  } };
  await assert.rejects(() => postChanges(client, baseline, [change], 11),
    error => error.code === 'INVALID_EVENT_IDENTITY');
  assert.equal(writes.length, 0);
});

test('legacy compatibility does not permit unselected field edits, renames or invalid types', () => {
  const old = custom('4g_non_vip_alert');
  const baseline = released([old]);
  const change = addition(baseline, custom('paywall_loading_finished'));
  assert.throws(() => planAgainstCurrent(baseline, { ...baseline, events: [{ ...old, name: 'Foreign edit' }] }, [change]),
    error => error.code === 'UNOWNED_PLATFORM_CHANGE');
  assert.throws(() => project(baseline, [{ ...change, operations: [{ event: old,
    field: 'point', before: old.point, after: 'renamed' }] }]), error => error.code === 'IMMUTABLE_FIELD');
  assert.throws(() => normalizeEvents([{ ...old, parameters: [
    { name: 'duration', valueType: 'float', isRequired: true },
  ] }]), error => error.code === 'INVALID_PARAMETER');
});

test('compatibility never accepts traversal, separators, uppercase or empty names', () => {
  for (const point of ['../old', '4g/old', '4g%2Fold', '4g\\old', '4g.old', '4g#old', '4g?old', '4G_old', '']) {
    assert.throws(() => normalizeEvents([custom(point)]), error => error.code === 'INVALID_EVENT_IDENTITY');
  }
});

test('identical duplicate records still block rather than being silently deduplicated', () => {
  const old = custom('4g_non_vip_alert');
  assert.throws(() => normalizeEvents([old, structuredClone(old)]), error => error.code === 'DUPLICATE_EVENT');
});

test('distinct identities that generate the same schema name block even with identical fields', () => {
  const pairs = [
    [page('feed'), custom('feed_pv')],
    [{ type: 'MODULE', trackerType: 'CLK', spm: ['home_card', 'submit'], point: 'submit', parameters: [] },
      { type: 'MODULE', trackerType: 'CLK', spm: ['home', 'card_submit'], point: 'card_submit', parameters: [] }],
    [page('4g_settings'), custom('4g_settings_pv')],
  ];
  for (const events of pairs) {
    assert.throws(() => normalizeEvents(events), error => error.code === 'DUPLICATE_SCHEMA');
    assert.throws(() => project(released(events), []), error => error.code === 'DUPLICATE_SCHEMA');
  }
  const baseline = released([page('feed')]);
  assert.throws(() => project(baseline, [addition(baseline, custom('feed_pv'))]),
    error => error.code === 'DUPLICATE_SCHEMA');
});

test('platform full-tree reads preserve old names but reject colliding physical records', async () => {
  const rows = [{ id: 1, parentApplicationId: 14, type: 4, point: '4g_non_vip_alert',
    name: 'Old alert', parameters: [], baseSchemas: [] }];
  const client = new PlatformClient({ request: async (method, path) => {
    assert.equal(method, 'GET');
    if (path.includes('/search')) return rows;
    return rows.find(row => String(row.id) === new URL(`https://fixture${path}`).searchParams.get('eventId'));
  } });
  assert.equal((await client.readTree(released([]))).events[0].point, '4g_non_vip_alert');
  rows.push({ ...rows[0], id: 2, type: 1, point: 'feed' },
    { ...rows[0], id: 3, point: 'feed_pv' });
  await assert.rejects(() => client.readTree(released([])), error => error.code === 'DUPLICATE_SCHEMA');
});

test('production schema collisions are rejected before any resolver request', async () => {
  const reader = new IgluReader({ baseUrls: ['https://schemas.example'], fetchImpl: async () => {
    assert.fail('conflicting schemas must not be fetched');
  } });
  await assert.rejects(() => reader.verify('smart_camera', '1-0-6', [page('feed'), custom('feed_pv')]),
    error => error.code === 'DUPLICATE_SCHEMA');
});

test('production readback is still required to establish the old released baseline', async () => {
  const baseline = released([custom('4g_non_vip_alert')]);
  const path = schemaPath('smart_camera', baseline.events[0], '1-0-6');
  assert.equal(path, 'com.smart_camera/4g_non_vip_alert/jsonschema/1-0-6');
  const client = { listReleases: async () => [{ id: 10, version: '1-0-6', releaseStatus: 3 }],
    readTree: async () => baseline };
  const reader = new IgluReader({ baseUrls: ['https://schemas.example'], fetchImpl: async url => {
    assert.equal(url, `https://schemas.example/${path}`);
    return { ok: true, json: async () => ({ self: { vendor: 'com.smart_camera',
      name: '4g_non_vip_alert', version: '1-0-6' }, type: 'object', properties: {} }) };
  } });
  assert.equal((await verifyBaseline(client, reader, baseline)).status, 'PASS');
  reader.fetch = async () => ({ ok: false, status: 404 });
  await assert.rejects(() => verifyBaseline(client, reader, baseline), error => error.code === 'PROD_SCHEMA_MISSING');
  await assert.rejects(() => verifyBaseline({ ...client, listReleases: async () => [
    { id: 10, version: '1-0-6', releaseStatus: 3 }, { id: 11, releaseStatus: 0 },
  ] }, reader, baseline), error => error.code === 'BASELINE_NOT_RELEASED');
});

test('frozen historical schemas with old names can be checked without weakening full-schema comparison', async () => {
  const events = [custom('4g_non_vip_alert')];
  const expected = { application: 'smart_camera', applicationId: 14, releaseId: 10,
    version: '1-0-6', eventDigest: digest(events) };
  const path = 'com.smart_camera/4g_non_vip_alert/jsonschema/1-0-6';
  const schema = { self: { vendor: 'com.smart_camera', name: '4g_non_vip_alert',
    format: 'jsonschema', version: '1-0-6' }, type: 'object', properties: {}, additionalProperties: false };
  const snapshot = { schemaVersion: 1, ...expected, releaseStatus: 3, events, schemas: [{ path, schema }] };
  const schemaReader = new ProductionSchemaReader({ baseUrls: ['https://schemas.example'], fetchImpl: async url => {
    assert.equal(url, `https://schemas.example/${path}`);
    return { ok: true, json: async () => structuredClone(schema) };
  } });
  assert.equal((await verifyPublishedSnapshot({ expected, snapshot, schemaReader })).status, 'PASS');
  schemaReader.fetch = async () => ({ ok: true, json: async () => ({ ...schema, additionalProperties: true }) });
  await assert.rejects(() => verifyPublishedSnapshot({ expected, snapshot, schemaReader }),
    error => error.code === 'PROD_SCHEMA_DRIFT');
});

test('adding an unrelated event never writes the released legacy point', async () => {
  const baseline = released([custom('4g_non_vip_alert')]);
  const added = custom('paywall_loading_finished');
  const change = addition(baseline, added);
  const writes = [];
  let events = baseline.events;
  const client = { listReleases: async () => [
    { id: 10, version: '1-0-6', releaseStatus: 3 }, { id: 11, version: '1-0-7', releaseStatus: 0 },
  ], readTree: async () => ({ ...baseline, events }),
  createEvent: async payload => { writes.push(payload); events = [...events, added]; } };
  const lock = await postChanges(client, baseline, [change], 11);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].events[0].point, added.point);
  assert.equal(lock.events.length, 2);
});

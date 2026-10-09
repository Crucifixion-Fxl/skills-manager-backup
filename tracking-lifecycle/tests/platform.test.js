const test = require('node:test');
const assert = require('node:assert/strict');
const { PlatformClient, detailToEvents, prepareUpdate, postChanges, createWorkorder, fetchTransport } = require('../cli/src/platform');
const { digest, ContractError } = require('../cli/src/contracts');

test('live platform client requires a reviewed HTTPS API endpoint', () => {
  assert.throws(() => new PlatformClient({ token: 'pat' }), e => e.code === 'INVALID_ENDPOINT');
  assert.throws(() => new PlatformClient({ baseUrl: 'http://manager.example.test', token: 'pat' }),
    e => e.code === 'INVALID_ENDPOINT');
  assert.doesNotThrow(() => new PlatformClient({ baseUrl: 'https://manager-cn.example.test', token: 'pat' }));
});

test('platform transport reports network cause without exposing credentials', async () => {
  const original = global.fetch;
  global.fetch = async () => { const error = new TypeError('fetch failed');
    error.cause = { code: 'ENOTFOUND' }; throw error; };
  try {
    await assert.rejects(() => fetchTransport('https://manager.example.test', 'secret-pat', 'GET', '/api/info/search'),
      error => error.code === 'PLATFORM_NETWORK' && error.message.includes('ENOTFOUND') &&
        !error.message.includes('secret-pat'));
  } finally { global.fetch = original; }
});

const rawPage = { id: 10, type: 1, point: 'feed', name: 'Feed', parentApplicationId: 14,
  parameters: [{ id: 31, eventId: 10, trackerType: 0, name: 'source', valueType: 'string', isRequired: 1 }],
  baseSchemas: [{ id: 99 }] };
const rawModule = { id: 11, type: 2, point: 'card', name: 'Card', parentApplicationId: 14,
  parentPageId: 10, parameters: [{ id: 32, eventId: 11, trackerType: 1,
    name: 'position', valueType: 'integer', isRequired: 0 }], baseSchemas: [] };
const rawComponent = { id: 12, type: 3, point: 'submit', name: 'Submit', parentApplicationId: 14,
  parentPageId: 10, parentModuleId: 11, parameters: [
    { id: 33, eventId: 12, trackerType: 1, name: 'source', valueType: 'string', isRequired: 1 },
  ], baseSchemas: [] };
const tree = [{ ...rawPage, children: [{ ...rawModule, children: [rawComponent] }] }];

test('platform reader recurses tree, uses full detail and expands CLK/EXP schemas', async () => {
  const calls = [];
  const details = new Map([[10, rawPage], [11, rawModule], [12, rawComponent]]);
  const client = new PlatformClient({ request: async (method, path) => {
    calls.push([method, path]);
    if (path.includes('/search')) return tree;
    if (path.includes('/getEventDetail')) return details.get(Number(new URL(`https://x${path}`).searchParams.get('eventId')));
    throw new Error(path);
  } });
  const result = await client.readTree({ application: 'smart_camera', applicationId: 14 });
  assert.equal(result.events.length, 5);
  assert.deepEqual(result.events.find(e => e.type === 'COMPONENT' && e.trackerType === 'CLK').spm,
    ['feed', 'card', 'submit']);
  assert.equal(result.events.find(e => e.type === 'COMPONENT' && e.trackerType === 'CLK').parameters[0].valueType, 'string');
  assert.equal(result.events.find(e => e.type === 'COMPONENT' && e.trackerType === 'EXP').parameters.length, 0);
  assert.equal(calls.filter(([_, path]) => path.includes('/getEventDetail')).length, 3);
});

test('platform reader preserves canonical integer and rejects ambiguous numeric type codes', () => {
  assert.equal(detailToEvents(rawModule, ['feed', 'card'])[0].parameters[0].valueType, 'integer');
  assert.throws(() => detailToEvents({ ...rawPage, parameters: [{ ...rawPage.parameters[0], valueType: '5' }] }, ['feed']),
    e => e.code === 'PLATFORM_SHAPE');
});

test('update payload retains every raw parameter and base schema while adding one parameter', () => {
  const desired = { type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'],
    point: 'submit', name: 'Submit', parameters: [
      { name: 'source', valueType: 'string', isRequired: true },
      { name: 'naming_type', valueType: 'string', isRequired: false },
    ] };
  const payload = prepareUpdate(rawComponent, [desired]);
  assert.equal(payload.id, 12);
  assert.equal(payload.parentPageId, 10);
  assert.deepEqual(payload.baseSchemas, []);
  assert.deepEqual(payload.parameters.map(p => p.name).sort(), ['naming_type', 'source']);
  assert.equal(payload.parameters.find(p => p.name === 'source').id, 33);
  assert.equal(payload.parameters.find(p => p.name === 'naming_type').eventId, 12);
  assert.equal(payload.parameters.find(p => p.name === 'naming_type').valueType, 'string');
  assert.equal(payload.parameters.find(p => p.name === 'source').valueType, 'string');
});

test('single-event update refuses an ambiguous numeric type in the existing full payload', () => {
  assert.throws(() => prepareUpdate({ ...rawComponent, parameters: [
    { ...rawComponent.parameters[0], valueType: '5' },
  ] }, [{ type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'],
    point: 'submit', parameters: [] }]), e => e.code === 'PLATFORM_SHAPE');
});

test('platform transport rejects a contradictory success envelope', async () => {
  const previous = global.fetch;
  global.fetch = async () => ({ ok: true, status: 200,
    json: async () => ({ code: 200, success: false, data: { id: 11 } }) });
  try {
    await assert.rejects(() => fetchTransport('https://example.invalid', 'test', 'POST', '/x', {}),
      e => e.code === 'PLATFORM_HTTP');
  } finally { global.fetch = previous; }
});

test('update payload applies event metadata and rejects divergent tracker metadata', () => {
  const clk = { type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'],
    point: 'submit', name: 'New submit', description: 'New description', alias: 'submit_action',
    category: 'Commerce', baseSchemas: [99], parameters: [] };
  const exp = { ...clk, trackerType: 'EXP' };
  const payload = prepareUpdate(rawComponent, [clk, exp]);
  assert.equal(payload.name, 'New submit');
  assert.equal(payload.description, 'New description');
  assert.equal(payload.alias, 'submit_action');
  assert.equal(payload.category, 'Commerce');
  assert.deepEqual(payload.baseSchemas, [99]);
  assert.throws(() => prepareUpdate(rawComponent, [clk, { ...exp, alias: 'different' }]),
    e => e.code === 'INVALID_UPDATE');
});

test('writer refuses undeclared platform change before any API write', async () => {
  const baseEvent = { type: 'PAGE', trackerType: 'BASE', spm: ['feed'], point: 'feed', name: 'Feed',
    parameters: [{ name: 'source', valueType: 'string', isRequired: true }] };
  const baseline = { schemaVersion: 1, kind: 'baseline', host: 'vicohome', application: 'smart_camera',
    applicationId: 14, release: { id: 1, version: '1-0-6', status: 3 }, events: [baseEvent] };
  const change = { schemaVersion: 1, kind: 'change', host: 'vicohome', issue: '101', applicationId: 14,
    baseDigest: digest(baseline.events), operations: [{
      event: { type: 'PAGE', trackerType: 'BASE', spm: ['feed'] },
      field: 'parameters.origin', before: { exists: false }, after: { valueType: 'string', isRequired: false },
    }] };
  let writes = 0;
  const client = {
    listReleases: async () => [{ id: 2, applicationId: 14, version: '1-0-7', releaseStatus: 0 },
      { id: 1, applicationId: 14, version: '1-0-6', releaseStatus: 3 }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14, events: [{
      ...baseEvent, parameters: [...baseEvent.parameters, { name: 'other_issue', valueType: 'string', isRequired: false }],
    }] }),
    saveEvent: async () => { writes++; },
  };
  await assert.rejects(() => postChanges(client, baseline, [change], 2), e => e instanceof ContractError && e.code === 'UNOWNED_PLATFORM_CHANGE');
  assert.equal(writes, 0);
});

test('writer checks fresh event detail before a full-overwrite update', async () => {
  const baseEvent = { type: 'PAGE', trackerType: 'BASE', spm: ['feed'], point: 'feed', name: 'Feed',
    parameters: [{ name: 'source', valueType: 'string', isRequired: true }] };
  const baseline = { schemaVersion: 1, kind: 'baseline', host: 'vicohome', application: 'smart_camera',
    applicationId: 14, release: { id: 1, version: '1-0-6', status: 3 }, events: [baseEvent] };
  const change = { schemaVersion: 1, kind: 'change', host: 'vicohome', issue: '101', applicationId: 14,
    baseDigest: digest(baseline.events), operations: [{ event: { type: 'PAGE', trackerType: 'BASE', spm: ['feed'] },
      field: 'parameters.origin', before: { exists: false }, after: { valueType: 'string', isRequired: false } }] };
  let writes = 0;
  const client = {
    listReleases: async () => [{ id: 2, applicationId: 14, version: '1-0-7', releaseStatus: 0 },
      { id: 1, applicationId: 14, version: '1-0-6', releaseStatus: 3 }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14,
      events: [{ ...baseEvent, platformId: 10 }] }),
    getDetail: async () => ({ ...rawPage, parameters: [...rawPage.parameters,
      { id: 32, eventId: 10, trackerType: 0, name: 'foreign', valueType: 'string', isRequired: 0 }] }),
    saveEvent: async () => { writes++; },
  };
  await assert.rejects(() => postChanges(client, baseline, [change], 2),
    e => e.code === 'PLATFORM_DRIFT_BEFORE_WRITE');
  assert.equal(writes, 0);
});

test('workorder creation only starts from a verified clean published baseline', async () => {
  const base = { schemaVersion: 1, kind: 'baseline', host: 'vicohome', application: 'smart_camera',
    applicationId: 14, release: { id: 10, version: '1-0-6', status: 3 }, events: [{
      type: 'PAGE', trackerType: 'BASE', spm: ['feed'], point: 'feed', parameters: [],
    }] };
  let created = 0;
  const client = {
    listReleases: async () => created ? [
      { id: 11, applicationId: 14, version: '1-0-7', releaseStatus: 0 },
      { id: 10, applicationId: 14, version: '1-0-6', releaseStatus: 3 },
    ] : [{ id: 10, applicationId: 14, version: '1-0-6', releaseStatus: 3 }],
    readTree: async () => ({ application: 'smart_camera', applicationId: 14, events: base.events }),
    createRelease: async () => { created++; },
  };
  const schemaReader = { verify: async () => ({ digest: 'prod' }) };
  const result = await createWorkorder(client, schemaReader, base, { name: '101', version: '1-0-7' });
  assert.equal(result.id, 11);
  assert.equal(created, 1);
  await assert.rejects(() => createWorkorder(client, schemaReader, base, { name: '102', version: '1-0-8' }),
    e => e.code === 'BASELINE_NOT_RELEASED');
});

test('writer creates parent page and module before a new component', async () => {
  const baseline = { schemaVersion: 1, kind: 'baseline', host: 'demo', application: 'demo_app',
    applicationId: 1, release: { id: 1, version: '1-0-0', status: 3 }, events: [] };
  const desired = [
    { type: 'PAGE', trackerType: 'BASE', spm: ['home'], point: 'home', name: 'Home',
      description: 'Home page', parameters: [] },
    ...['CLK', 'EXP'].map(trackerType => ({ type: 'MODULE', trackerType, spm: ['home', 'card'],
      point: 'card', name: 'Card', parameters: [] })),
    ...['CLK', 'EXP'].map(trackerType => ({ type: 'COMPONENT', trackerType, spm: ['home', 'card', 'submit'],
      point: 'submit', name: 'Submit', parameters: [] })),
  ];
  const change = { schemaVersion: 1, kind: 'change', host: 'demo', applicationId: 1, issue: '101',
    baseDigest: digest([]), operations: desired.map(event => ({
      event: { type: event.type, trackerType: event.trackerType, spm: event.spm },
      field: '$event', before: { exists: false }, after: event,
    })) };
  let live = [];
  const order = [];
  const client = {
    listReleases: async () => [{ id: 2, releaseStatus: 0, version: '1-0-1', applicationId: 1 },
      { id: 1, releaseStatus: 3, version: '1-0-0', applicationId: 1 }],
    readTree: async () => ({ application: 'demo_app', applicationId: 1, events: live }),
    createEvent: async payload => {
      const type = payload.events[0].type;
      order.push(type);
      if (type === 'PAGE') assert.equal(payload.events[0].description, 'Home page');
      live = [...live, ...desired.filter(e => e.type === type)];
    },
  };
  const result = await postChanges(client, baseline, [change], 2);
  assert.deepEqual(order, ['PAGE', 'MODULE', 'COMPONENT']);
  assert.equal(result.writes, 3);
});

test('writer refuses a workorder based on a newer released boundary', async () => {
  const baseline = { schemaVersion: 1, kind: 'baseline', host: 'demo', application: 'demo_app',
    applicationId: 1, release: { id: 1, version: '1-0-0', status: 3 }, events: [] };
  const client = { listReleases: async () => [
    { id: 3, releaseStatus: 0, version: '1-0-2', applicationId: 1 },
    { id: 2, releaseStatus: 3, version: '1-0-1', applicationId: 1 },
    { id: 1, releaseStatus: 3, version: '1-0-0', applicationId: 1 },
  ] };
  await assert.rejects(() => postChanges(client, baseline, [], 3), e => e.code === 'BASELINE_DRIFT');
});

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const cli = path.resolve(__dirname, '../cli/bin/events-tdd.js');

test('CLI exposes the full source-bound platform operation catalog', () => {
  const result = spawnSync(process.execPath, [cli, 'api-catalog'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.operations.length, 76);
  assert.equal(output.sourceCommit, 'd61e1692c3d28224e4d642f7bdf765a6a982ab0d');
  const routes = new Set(output.operations.map(x => `${x.method} ${x.path}`));
  for (const route of ['POST /api/baseSchema/add', 'POST /api/context/save',
    'POST /api/approval/updateStatus', 'POST /api/project-tokens/{id}/status',
    'POST /api/release/onlineRelease', 'POST /api/role/setAppOwner']) assert.ok(routes.has(route), route);
  assert.ok(!routes.has('GET /api/release/getChangedInfo'));
});

function api() { return require('../cli/src/platform-admin'); }
const pat = { baseUrl: 'https://manager.example.test', auth: 'pat', token: 'tmt_test-private' };
const project = { ...pat, auth: 'project', token: 'tmp_test-private' };
const envelope = data => ({ ok: true, status: 200, json: async () => ({ code: 200, success: true, data }) });

test('Base add uses JSON while detail and delete use bound query parameters', () => {
  const { planOperation } = api();
  const body = { applicationId: 14, name: 'shared', parameters: [] };
  assert.deepEqual(planOperation('baseSchema.saveBaseSchemas', { body }).body, body);
  assert.equal(planOperation('baseSchema.getDetailByBaseSchemaId', { query: { id: 99 } }).requestPath, '/api/baseSchema/detail?id=99');
  const deletion = planOperation('baseSchema.deleteBaseSchemasById', { query: { id: 99, parentApplicationId: 14 } });
  assert.equal(deletion.method, 'DELETE');
  assert.equal(deletion.body, undefined);
  assert.equal(deletion.requestPath, '/api/baseSchema/delete?id=99&parentApplicationId=14');
});

test('Context schema and form-bound approval routes are encoded without JSON bodies', () => {
  const { planOperation } = api();
  assert.equal(planOperation('context.contextSchema', { query: { id: 1 } }).requestPath, '/api/context/schema?id=1');
  const plan = planOperation('approval.updateStatus', { query: { id: 1, status: 'PENDING & test' } });
  assert.equal(plan.requestPath, '/api/approval/updateStatus?id=1&status=PENDING+%26+test');
  assert.equal(plan.body, undefined);
});

test('Token path ID uses canonical positive numeric strings and distinct JSON body', () => {
  const { planOperation } = api();
  assert.equal(planOperation('projectAccessToken.updateStatus', { path: { id: '3' }, body: { status: 0 } }).requestPath,
    '/api/project-tokens/3/status');
  for (const id of ['../3', '3/status', '03', '0', '-1', '3?x=1', '3\n']) {
    assert.throws(() => planOperation('projectAccessToken.updateStatus', { path: { id }, body: { status: 0 } }),
      error => error.code === 'INVALID_API_INPUT');
  }
});

test('unknown operations, unbound inputs and GET bodies fail before I/O', () => {
  const { planOperation } = api();
  assert.throws(() => planOperation('/api/arbitrary', {}), e => e.code === 'UNKNOWN_OPERATION');
  for (const input of [{ url: 'https://other.test' }, { query: { secret: 'private' } }, { path: { id: '1' } },
    { body: {} }, { query: { id: { nested: 'value' } } }]) {
    assert.throws(() => planOperation('context.contextSchema', input), e => e.code === 'INVALID_API_INPUT');
  }
});

test('Project Token blocks POST query operations and GET auth state changes locally', async () => {
  const { AdministrativeClient } = api();
  let calls = 0;
  const client = new AdministrativeClient({ ...project, fetch: async () => { calls++; return envelope([]); } });
  await assert.rejects(client.call('release.getUnpassedEvents', { body: { id: 1, applicationId: 14 } }),
    e => e.code === 'READ_ONLY_TOKEN');
  await assert.rejects(client.call('login.logout', {}), e => e.code === 'READ_ONLY_TOKEN');
  assert.equal(calls, 0);
});

test('JWT and contradictory/mixed auth are rejected without transmitting', () => {
  const { AdministrativeClient } = api();
  for (const input of [{ ...pat, token: 'eyJhbGci.test.signature' },
    { ...project, token: 'tmt_test' }, { ...pat, cookie: 'JSESSIONID=test' },
    { ...pat, baseUrl: 'http://manager.example.test' }, { ...pat, auth: 'jwt' }]) {
    assert.throws(() => new AdministrativeClient(input));
  }
});

test('Token creation requires Session and private response handling, PAT cannot mint it', async () => {
  const { AdministrativeClient } = api();
  let calls = 0;
  const client = new AdministrativeClient({ ...pat, fetch: async () => { calls++; return envelope({}); } });
  await assert.rejects(client.call('apiAccessToken.create', { body: { name: 'test', expiryDays: 7 } }),
    e => e.code === 'SESSION_REQUIRED');
  assert.equal(calls, 0);
});

test('generic requests cannot bypass event/workorder projection or production publish', async () => {
  const { AdministrativeClient } = api();
  let calls = 0;
  const client = new AdministrativeClient({ ...pat, fetch: async () => { calls++; return envelope({}); } });
  for (const operation of ['trackerInfo.saveOrUpdateEventInfo', 'trackerInfo.batchCreateEvents',
    'trackerInfo.startImport',
    'release.addRelease', 'release.onlineRelease']) {
    await assert.rejects(client.call(operation, { body: {} }), e => e.code === 'TRACKING_WORKFLOW_REQUIRED');
  }
  assert.equal(calls, 0);
});

test('private output failures hide filesystem paths before sending any request', async () => {
  const fs = require('node:fs'), os = require('node:os');
  const { runAdministrative } = require('../cli/src/platform-admin-cli');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-private-'));
  const request = path.join(directory, 'request.json');
  fs.writeFileSync(request, JSON.stringify({ body: { name: 'test', expiryDays: 7 } }));
  const priorFetch = global.fetch;
  let calls = 0;
  global.fetch = async () => { calls++; return envelope({ token: 'canary-secret' }); };
  try {
    await assert.rejects(runAdministrative('api-call', { request, auth: 'session',
      operation: 'apiAccessToken.create', 'private-output': path.join(directory, 'private-path-canary', 'token.json') },
    { TRACKING_PLATFORM_BASE_URL: pat.baseUrl, TMT_SESSION_COOKIE: 'JSESSIONID=canary' },
    () => assert.fail('must not output success')),
    e => e.code === 'INVALID_PRIVATE_OUTPUT' && !e.message.includes(directory));
    assert.equal(calls, 0);
  } finally { global.fetch = priorFetch; fs.rmSync(directory, { recursive: true, force: true }); }
});

for (const failure of ['network', 'save', 'cleanup']) {
  test(`private output cleanup cannot leak paths or replace ${failure} outcome`, async () => {
    const fs = require('node:fs'), os = require('node:os');
    const { runAdministrative } = require('../cli/src/platform-admin-cli');
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-private-'));
    const request = path.join(directory, 'request.json'), output = path.join(directory, 'token.json');
    fs.writeFileSync(request, JSON.stringify({ body: { name: 'test', expiryDays: 7 } }));
    const priorFetch = global.fetch, priorClose = fs.closeSync, priorSync = fs.fsyncSync;
    let outputs = 0, calls = 0;
    global.fetch = async () => {
      calls++;
      if (failure === 'network') throw new Error(directory);
      return envelope({ token: 'canary-secret' });
    };
    fs.closeSync = descriptor => { priorClose(descriptor); throw new Error(directory); };
    if (failure === 'save') fs.fsyncSync = () => { throw new Error(directory); };
    const code = { network: 'API_RESULT_UNKNOWN', save: 'PRIVATE_OUTPUT_FAILED',
      cleanup: 'PRIVATE_OUTPUT_CLEANUP_FAILED' }[failure];
    try {
      await assert.rejects(runAdministrative('api-call', { request, auth: 'session',
        operation: 'apiAccessToken.create', 'private-output': output },
      { TRACKING_PLATFORM_BASE_URL: pat.baseUrl, TMT_SESSION_COOKIE: 'JSESSIONID=canary' },
      () => { outputs++; }), e => e.code === code && !e.message.includes(directory));
      assert.equal(calls, 1);
      assert.equal(outputs, 0);
    } finally {
      global.fetch = priorFetch; fs.closeSync = priorClose; fs.fsyncSync = priorSync;
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

test('MCP release listing remains a read operation while creation keeps its workflow gate', async () => {
  const { AdministrativeClient } = api();
  let calls = 0;
  const client = new AdministrativeClient({ baseUrl: pat.baseUrl, auth: 'mcp', apiKey: 'test-mcp-key',
    fetch: async (url, config) => {
      calls++;
      assert.equal(url, 'https://manager.example.test/api/mcp/releases?applicationId=3396');
      assert.equal(config.method, 'GET');
      return envelope([{ id: 357, version: '2-0-0' }]);
    } });
  const result = await client.call('mcpProxy.listReleases', { query: { applicationId: 3396 } });
  assert.deepEqual(result.data, [{ id: 357, version: '2-0-0' }]);
  await assert.rejects(client.call('mcpProxy.createRelease', { body: {} }),
    e => e.code === 'TRACKING_WORKFLOW_REQUIRED');
  assert.equal(calls, 1);
});

test('actual transport sends Project auth to the bound origin and disables redirects', async () => {
  const { AdministrativeClient } = api();
  let invocation;
  const client = new AdministrativeClient({ ...project, fetch: async (url, config) => { invocation = { url, config }; return envelope([]); } });
  const result = await client.call('baseSchema.getAllBaseSchemas', { query: { applicationId: 14 } });
  assert.equal(result.status, 'API_ACKNOWLEDGED');
  assert.equal(invocation.url, 'https://manager.example.test/api/baseSchema/list?applicationId=14');
  assert.equal(invocation.config.headers.Authorization, 'Bearer tmp_test-private');
  assert.equal(invocation.config.redirect, 'manual');
  assert.equal(invocation.config.body, undefined);
});

test('ordinary API response redacts nested secrets and reflected auth, not benign data', async () => {
  const { AdministrativeClient } = api();
  const client = new AdministrativeClient({ ...pat, fetch: async () => envelope({ id: 1, token: 'server-secret',
    nested: { cookie: 'secret', label: 'tmt_test-private', count: 3 } }) });
  const result = await client.call('context.list', { query: { applicationId: 14 } });
  assert.equal(result.data.id, 1);
  assert.equal(result.data.token, '[REDACTED]');
  assert.equal(result.data.nested.count, 3);
  assert.doesNotMatch(JSON.stringify(result), /server-secret|test-private/);
});

for (const kind of ['network', 'redirect', 'non-json', 'business', 'contradictory']) {
  test(`${kind} failure contains no raw server/network message, request fields or credentials`, async () => {
    const { AdministrativeClient } = api();
    let calls = 0;
    const client = new AdministrativeClient({ ...pat, fetch: async () => {
      calls++;
      if (kind === 'network') throw new Error('tmt_test-private raw-private-path');
      if (kind === 'redirect') return { ok: false, status: 302 };
      if (kind === 'non-json') return { ok: true, status: 200, json: async () => { throw new Error('raw-private-path'); } };
      return { ok: true, status: 200, json: async () => ({ code: kind === 'business' ? 401 : 200,
        success: false, errorMessage: 'tmt_test-private raw-private-path' }) };
    } });
    await assert.rejects(client.call('context.save', { body: { applicationId: 14, name: 'test', parameters: [] } }),
      e => !/test-private|raw-private-path/.test(e.message) && e.code === 'API_RESULT_UNKNOWN');
    assert.equal(calls, 1);
  });
}

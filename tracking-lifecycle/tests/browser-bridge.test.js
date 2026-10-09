'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { startBridge } = require('../cli/src/remote-bridge');
const { createBrowserTransport } = require('../cli/src/browser-transport');
const { ContractError } = require('../cli/src/contracts');

const origin = 'https://manager.example.test';
function fixture() {
  const calls = [];
  const state = { subject: 7, authenticated: true, permission: true };
  const transport = { calls, state, close: async () => {}, async request(method, url, body) {
    calls.push({ method, url, body });
    if (!state.authenticated) throw new ContractError('AUTH_REQUIRED', 'fixture');
    if (url === '/api/user/getCurrentUser') return { userId: state.subject, name: 'fixture' };
    if (url === '/api/info/getAllApplication') return state.permission ? [{ id: 14, name: 'fixture_app' }] : [];
    return { parentApplicationId: 14, items: [] };
  }};
  return transport;
}
async function setup(t, options = {}) {
  const transport = fixture();
  const bridge = await startBridge({ origin, port: 0, transport, expectedSubject: '7', applicationId: '14', ...options });
  t.after(() => bridge.close());
  const call = async (route, data = {}, headers = {}) => {
    const response = await fetch(bridge.url + route, { method: 'POST', headers: {
      Authorization: `Bearer ${bridge.key}`, 'Content-Type': 'application/json', ...headers,
    }, body: JSON.stringify({ origin, ...data }) });
    return { status: response.status, ...await response.json() };
  };
  return { transport, bridge, call };
}
test('tracking bridge verifies subject and App before becoming READY', async t => {
  const { call, transport, bridge } = await setup(t);
  const result = await call('/v1/ensure');
  assert.equal(result.data.state, 'READY');
  assert.equal(result.data.subject, '7');
  assert.equal(result.data.applicationId, '14');
  assert.equal(JSON.stringify(result).includes(bridge.key), false);
  assert.deepEqual(transport.calls.map(c => c.url), ['/api/user/getCurrentUser', '/api/info/getAllApplication']);
});
test('tracking bridge distinguishes expired login and insufficient App permission', async t => {
  const { call, transport } = await setup(t);
  transport.state.authenticated = false;
  assert.equal((await call('/v1/ensure')).data.state, 'NEEDS_USER');
  transport.state.authenticated = true; transport.state.permission = false;
  assert.equal((await call('/v1/ensure')).data.state, 'BLOCKED_PERMISSION');
});
test('tracking bridge refuses a changed identity before sending a business request', async t => {
  const { call, transport } = await setup(t);
  assert.equal((await call('/v1/ensure')).data.state, 'READY');
  transport.state.subject = 8;
  const result = await call('/v1/request', { operation: 'context.list', input: { query: { applicationId: 14 } } });
  assert.equal(result.code, 'AUTH_IDENTITY_MISMATCH');
  assert.equal(transport.calls.some(c => c.url.startsWith('/api/context')), false);
});
test('tracking bridge limits requests to the bound App', async t => {
  const { call, transport } = await setup(t);
  const denied = await call('/v1/request', { operation: 'context.list', input: { query: { applicationId: 15 } } });
  assert.equal(denied.code, 'RESOURCE_MISMATCH');
  assert.equal(transport.calls.some(c => c.url.startsWith('/api/context')), false);
  const allowed = await call('/v1/request', { operation: 'context.list', input: { query: { applicationId: 14 } } });
  assert.equal(allowed.ok, true);
});
test('tracking bridge refuses write, credential, OAuth and production workflow routes', async t => {
  const { call, transport } = await setup(t, { allowWrites: true });
  for (const operation of ['login.logout', 'login.feishuLogin']) {
    const result = await call('/v1/request', { operation, input: {} });
    assert.equal(result.code, 'LOGIN_WORKFLOW_REQUIRED');
  }
  const secret = await call('/v1/request', { operation: 'apiAccessToken.create', input: { body: {} } });
  assert.equal(secret.code, 'PRIVATE_RESPONSE_REQUIRED');
  const publish = await call('/v1/request', { operation: 'release.onlineRelease', input: { body: { applicationId: 14, id: 1 } } });
  assert.equal(publish.code, 'PROTECTED_CI_REQUIRED');
  assert.equal(transport.calls.some(c => c.url.includes('logout') || c.url.includes('onlineRelease')), false);
});
test('read-only bridge refuses mutation before I/O', async t => {
  const { call, transport } = await setup(t);
  const result = await call('/v1/request', { operation: 'context.save', input: { body: { applicationId: 14 } } });
  assert.equal(result.code, 'READ_ONLY_BRIDGE');
  assert.equal(transport.calls.length, 0);
});
test('tracking bridge TTL and explicit revoke invalidate access', async t => {
  let now = 1000;
  const { call, transport } = await setup(t, { ttlMs: 1000, now: () => now });
  assert.equal((await call('/v1/ensure')).data.state, 'READY');
  now = 2000;
  assert.equal((await call('/v1/status')).data.state, 'EXPIRED');
  const count = transport.calls.length;
  assert.equal((await call('/v1/request', { operation: 'context.list', input: { query: { applicationId: 14 } } })).code, 'EXPIRED');
  assert.equal(transport.calls.length, count);
});
test('tracking bridge rejects arbitrary origins and webpage requests', async t => {
  const { call, transport } = await setup(t);
  assert.equal((await call('/v1/ensure', { origin: 'https://other.example.test' })).code, 'BRIDGE_ORIGIN_MISMATCH');
  assert.equal((await call('/v1/ensure', {}, { Origin: 'https://evil.example.test' })).ok, false);
  assert.equal(transport.calls.length, 0);
});
test('browser authentication errors preserve recovery category', async () => {
  for (const [status, code] of [[401, 'AUTH_REQUIRED'], [403, 'API_PERMISSION_DENIED'], [503, 'AUTH_DEPENDENCY_FAILED']]) {
    const page = { cdp: async () => ({ result: { value: { ok: false, status, payload: {} } } }) };
    const transport = createBrowserTransport({ baseUrl: origin, session: 'fixture', page });
    await assert.rejects(transport.request('GET', '/api/user/getCurrentUser'), e => e.code === code);
  }
});

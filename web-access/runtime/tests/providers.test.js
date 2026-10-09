'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { bearerProvider, supersetProvider, deviceProvider } = require('../src/providers');
const { createGateway } = require('../src/gateway');
const { nativeAdapter } = require('../src/native-adapter');
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

test('native API tool verifies identity and works without OpenCLI or a browser', async t => {
  const requests = [];
  const provider = bearerProvider({ origin: 'https://api.example.test', tokenEnv: 'FIXTURE_TOKEN', env: { FIXTURE_TOKEN: 'fixture-private' },
    fetchImpl: async (url, options) => { requests.push({ url, options }); return json(url.endsWith('/me') ? { id: 7 } : { result: [] }); } });
  const adapter = nativeAdapter({ provider, profile: { identity: { path: '/me', subjectPath: 'id' }, operations: { list: { path: '/objects', effect: 'read' } } } });
  const bridge = await createGateway({ origin: 'https://api.example.test', port: 0, adapter, expectedSubject: '7' });
  t.after(() => bridge.close());
  const response = await fetch(bridge.url + '/v1/request', { method: 'POST', headers: { Authorization: `Bearer ${bridge.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin: bridge.origin, operation: 'list', input: {} }) });
  const result = await response.json(); assert.equal(result.ok, true);
  assert.equal(requests[0].options.headers.Authorization, 'Bearer fixture-private');
  assert.equal(JSON.stringify(result).includes('fixture-private'), false);
  assert.equal(requests.length, 2);
});
test('native operation registry rejects URLs and unbound arguments before platform I/O', () => {
  let calls = 0;
  const adapter = nativeAdapter({ provider: { request() { calls++; } }, profile: { identity: { path: '/me', subjectPath: 'id' }, operations: { list: { path: '/objects', effect: 'read' } } } });
  for (const input of [{ url: 'https://evil.example' }, { token: 'fixture' }, { body: {} }]) {
    assert.throws(() => adapter.prepare('/v1/request', { origin: 'https://api.example', operation: 'list', input }, { allowWrites: false }), e => e.code === 'INVALID_INPUT');
  }
  assert.equal(calls, 0);
});
test('Superset refuses automatic personal-password login and never retries a write', async () => {
  let calls = 0;
  const personal = supersetProvider({ origin: 'https://superset.example', env: { SUPERSET_USERNAME: 'fixture', SUPERSET_PASSWORD: 'fixture' }, fetchImpl: async () => { calls++; return json({}); } });
  await assert.rejects(personal.request('GET', '/api/v1/me/'), e => e.code === 'AUTH_REQUIRED');
  assert.equal(calls, 0);
  const write = supersetProvider({ origin: 'https://superset.example', env: { SUPERSET_TOKEN: 'fixture-token', SUPERSET_ACCOUNT_TYPE: 'service' }, fetchImpl: async () => { calls++; return json({}, 401); } });
  await assert.rejects(write.request('POST', '/api/v1/example/', {}), e => e.code === 'AUTH_REQUIRED');
  assert.equal(calls, 1);
});
test('device flow enforces interval, slow_down and target API verification without exporting tokens', async () => {
  let now = 0, polls = 0;
  const provider = deviceProvider({ devicePath: '/api/device-code', tokenPath: '/api/login/oauth/access_token', issuer: 'https://idp.example', origin: 'https://api.example', clientId: 'public-fixture', now: () => now,
    fetchImpl: async (url, options) => {
      if (url.endsWith('/api/device-code')) return json({ device_code: 'private-device', user_code: 'ABCD-EFGH', verification_uri: 'https://idp.example/device', expires_in: 60, interval: 2 });
      if (url.startsWith('https://api.example')) { assert.equal(options.headers.Authorization, 'Bearer private-access'); return json({ id: 7 }); }
      polls++; assert.ok(options.body.includes('device_code=private-device'));
      return polls === 1 ? json({ error: 'slow_down' }, 400) : json({ access_token: 'private-access', token_type: 'Bearer' });
    } });
  const first = await provider.begin(); assert.equal(first.state, 'NEEDS_USER');
  assert.equal(JSON.stringify(first).includes('private-device'), false);
  await provider.poll(); assert.equal(polls, 0);
  now = 2000; assert.equal((await provider.poll()).pollAfterMs, 7000);
  now = 8999; await provider.poll(); assert.equal(polls, 1);
  now = 9000; const authorized = await provider.poll(); assert.equal(authorized.state, 'VERIFYING');
  assert.equal(JSON.stringify(authorized).includes('private-access'), false);
  assert.deepEqual(await provider.request('GET', '/me'), { id: 7 });
});
test('device flow refuses issuer changes and has explicit denied/expired outcomes', async () => {
  const bad = deviceProvider({ devicePath: '/api/device-code', tokenPath: '/api/login/oauth/access_token', issuer: 'https://idp.example', origin: 'https://api.example', clientId: 'fixture', fetchImpl: async () => json({
    device_code: 'fixture', user_code: 'ABCD', verification_uri: 'https://evil.example/device', expires_in: 60,
  }) });
  await assert.rejects(bad.begin(), e => e.code === 'UNSUPPORTED_DEVICE_GRANT');
  for (const [error, expected] of [['access_denied', 'REVOKED'], ['expired_token', 'EXPIRED']]) {
    let now = 0;
    const p = deviceProvider({ devicePath: '/api/device-code', tokenPath: '/api/login/oauth/access_token', issuer: 'https://idp.example', origin: 'https://api.example', clientId: 'fixture', now: () => now,
      fetchImpl: async url => json(url.endsWith('/api/device-code') ? { device_code: 'private-device', user_code: 'ABCD', verification_uri: 'https://idp.example/device', expires_in: 60, interval: 1 } : { error }, url.endsWith('/api/device-code') ? 200 : 400) });
    await p.begin(); now = 1000; assert.equal((await p.poll()).state, expected);
  }
});

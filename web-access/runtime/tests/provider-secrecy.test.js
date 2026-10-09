'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { bearerProvider, supersetProvider, deviceProvider } = require('../src/providers');
const { nativeAdapter } = require('../src/native-adapter');
const { createGateway } = require('../src/gateway');
test('ordinary upstream diagnostic fields cannot export the bearer through the actual gateway', async t => {
  const secret = 'synthetic-opaque-credential';
  const provider = bearerProvider({ origin: 'https://fixture.example', tokenEnv: 'FIXTURE_TOKEN', env: { FIXTURE_TOKEN: secret },
    fetchImpl: async url => new Response(JSON.stringify(url.endsWith('/me') ? { id: 7 } : { message: `debug ${secret}`, nested: [`prefix-${secret}-suffix`] })) });
  const gateway = await createGateway({ origin: 'https://fixture.example', port: 0, expectedSubject: '7',
    adapter: nativeAdapter({ provider, profile: { identity: { path: '/me', subjectPath: 'id' }, operations: { list: { effect: 'read', path: '/list' } } } }) });
  t.after(() => gateway.close());
  const response = await fetch(gateway.url + '/v1/request', { method: 'POST', headers: { Authorization: `Bearer ${gateway.key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ origin: gateway.origin, operation: 'list', input: {} }) });
  const body = await response.text(); assert.equal(response.status, 200); assert.equal(JSON.parse(body).ok, true);
  assert.equal(body.includes(secret), false); assert.match(body, /redacted/);
});
test('Superset ordinary responses redact both its bearer and reflected service password', async () => {
  const token = 'synthetic-service-bearer', password = 'synthetic-service-password';
  const provider = supersetProvider({ origin: 'https://fixture.example', env: { SUPERSET_ACCOUNT_TYPE: 'service', SUPERSET_USERNAME: 'fixture', SUPERSET_PASSWORD: password },
    fetchImpl: async url => new Response(JSON.stringify(url.endsWith('/login') ? { access_token: token } : { message: `${token} and ${password}` })) });
  const response = JSON.stringify(await provider.request('GET', '/me'));
  assert.equal(response.includes(token), false); assert.equal(response.includes(password), false);
});
test('public device recovery refuses private codes encoded in URL query, path or fragment', async () => {
  const code = 'private/device';
  for (const uri of ['https://idp.example/device?code=private%2Fdevice', 'https://idp.example/private%252Fdevice', 'https://idp.example/device#private%2Fdevice']) {
    const provider = deviceProvider({ origin: 'https://fixture.example', issuer: 'https://idp.example', clientId: 'fixture-client', devicePath: '/device', tokenPath: '/token',
      fetchImpl: async () => new Response(JSON.stringify({ device_code: code, user_code: 'AB CD', verification_uri: uri, expires_in: 60 })) });
    await assert.rejects(provider.begin(), e => e.code === 'UNSUPPORTED_DEVICE_GRANT');
  }
});

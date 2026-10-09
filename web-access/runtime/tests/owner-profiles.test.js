'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { serve, loadProfile } = require('../src');
const { profileRoot, readOwnerProfile } = require('./profile-fixtures');

test('CMS synthetic offline profile binds identity, collection permission and tenant with no credential export', async t => {
  const original = global.fetch, calls = [], token = 'synthetic-owner-credential';
  const cmsOrigin = 'https://cms-owner.fixture.invalid';
  let permit = true, tenant = 'owned';
  const cmsProfile = { schemaVersion: 1, tool: 'marketing-cms', owner: 'marketing-cms', provider: 'bearer-env', origin: cmsOrigin,
    tokenEnv: 'MARKETING_CMS_TOKEN', identity: { path: '/api/users/me', subjectPath: 'user.email' }, permissionProbe: '/api/access',
    operations: { me: { effect: 'read', path: '/api/users/me' }, access: { effect: 'read', path: '/api/access' },
      'paywalls-list': { effect: 'read', path: '/api/paywalls', query: {
        limit: { type: 'integer', minimum: 1, maximum: 100, default: 10 }, tenant: { type: 'string', binding: 'tenantId', queryName: 'where[tenantId][equals]' },
      }, permissionPath: 'collections.paywalls.read.permission', responseBindings: [{ binding: 'tenantId', path: 'docs.*.tenantId' }] } } };
  const skillsRoot = profileRoot(t, [cmsProfile,
    readOwnerProfile('skills/observability/troubleshooting/references/auth-profile.json'),
    readOwnerProfile('skills/business-operations/addx-console-admin/references/auth-profile.json'),
    readOwnerProfile('skills/delivery/cicd-platform/references/auth-profile.json')]);
  global.fetch = async (url, options) => {
    if (!String(url).startsWith(cmsOrigin)) {
      assert.equal(new URL(url).hostname, '127.0.0.1', 'owner fixture must not access external services');
      return original(url, options);
    }
    calls.push(new URL(url)); assert.equal(options.headers.Authorization, 'Bearer ' + token);
    const path = new URL(url).pathname;
    return new Response(JSON.stringify(path === '/api/users/me' ? { user: { email: 'tester@example.invalid' }, token }
      : path === '/api/access' ? { collections: { paywalls: { read: { permission: permit } } } }
        : { docs: [{ id: 1, tenantId: tenant }], message: token }));
  };
  t.after(() => { global.fetch = original; });
  const gateway = await serve({ tool: 'marketing-cms', expectedSubject: 'tester@example.invalid', tenantId: 'owned', port: 0,
    env: { SAAS_SKILLS_ROOT: skillsRoot, MARKETING_CMS_TOKEN: token } });
  t.after(() => gateway.close());
  const call = async (route, operation, input) => (await original(gateway.url + route, { method: 'POST',
    headers: { Authorization: 'Bearer ' + gateway.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin: gateway.origin, ...(operation ? { operation, input } : {}) }) })).json();
  assert.equal((await call('/v1/ensure')).data.state, 'READY');
  const result = await call('/v1/request', 'paywalls-list', { limit: 2 });
  assert.equal(result.ok, true); assert.equal(JSON.stringify(result).includes(token), false);
  assert.equal(calls.at(-1).searchParams.get('where[tenantId][equals]'), 'owned');
  const before = calls.length;
  assert.equal((await call('/v1/request', 'api-keys-list', {})).code, 'UNKNOWN_OPERATION'); assert.equal(calls.length, before);
  permit = false;
  assert.equal((await call('/v1/request', 'paywalls-list', {})).code, 'API_PERMISSION_DENIED');
  assert.equal(gateway.status().state, 'BLOCKED_PERMISSION');
  assert.equal(calls.at(-1).pathname, '/api/access');
  permit = true; tenant = 'foreign';
  assert.equal((await call('/v1/request', 'paywalls-list', {})).code, 'RESOURCE_MISMATCH');
});

test('Troubleshooting owner envelope failure never authenticates and never exports its JWT', async t => {
  const original = global.fetch, token = 'synthetic-troubleshooting-credential'; let success = true;
  global.fetch = async (url, options) => {
    if (!String(url).startsWith('https://troubleshooting-us.addx.live')) return original(url, options);
    assert.equal(new URL(url).pathname, '/api/current');
    return new Response(JSON.stringify({ success, result: { data: { userid: 'tester', token } } }));
  };
  t.after(() => { global.fetch = original; });
  const skillsRoot = profileRoot(t, [readOwnerProfile('skills/observability/troubleshooting/references/auth-profile.json')]);
  const gateway = await serve({ tool: 'troubleshooting', expectedSubject: 'tester', port: 0, env: { SAAS_SKILLS_ROOT: skillsRoot, TROUBLESHOOTING_TOKEN: token } });
  t.after(() => gateway.close());
  const ensure = async () => (await original(gateway.url + '/v1/ensure', { method: 'POST',
    headers: { Authorization: 'Bearer ' + gateway.key, 'Content-Type': 'application/json' }, body: JSON.stringify({ origin: gateway.origin }) })).json();
  assert.equal((await ensure()).data.state, 'READY');
  success = false; const rejected = await ensure(); assert.equal(rejected.code, 'API_REQUEST_FAILED');
  assert.equal(JSON.stringify(rejected).includes(token), false);
  assert.notEqual(gateway.status().state, 'READY');
  assert.equal(loadProfile('troubleshooting', { skillsRoot }).runtimeVerification, 'pending');
});

test('Console owner uses raw token and POST identity on its declared API origin, and expiry exits READY', async t => {
  const original = global.fetch, token = 'opaque-console-owner-token'; let code = 0; const calls = [];
  global.fetch = async (url, options) => {
    if (!String(url).startsWith('https://revenus-sharing-backend.addx.live')) return original(url, options);
    calls.push([url, options.method]);
    assert.equal(options.headers.Authorization, token);
    assert.equal(options.method, 'POST'); assert.equal(new URL(url).pathname, '/api/user/info');
    return new Response(JSON.stringify({ code, data: { userId: 34, userToken: token }, message: token }));
  };
  t.after(() => { global.fetch = original; });
  const skillsRoot = profileRoot(t, [readOwnerProfile('skills/business-operations/addx-console-admin/references/auth-profile.json')]);
  const gateway = await serve({ tool: 'console', expectedSubject: '34', port: 0, env: { SAAS_SKILLS_ROOT: skillsRoot, CONSOLE_TOKEN: token } });
  t.after(() => gateway.close());
  assert.equal((await gateway.ensure()).state, 'READY');
  code = 50008; assert.equal((await gateway.ensure()).state, 'NEEDS_USER');
  assert.equal(calls.length, 2);
});

test('CICD fixed source identity uses result.userId, Bearer and code zero without accepting failed envelopes', async t => {
  const original = global.fetch, token = 'synthetic-cicd-credential'; let code = 0;
  global.fetch = async (url, options) => {
    if (!String(url).startsWith('https://cicd.addx.live')) return original(url, options);
    assert.equal(new URL(url).pathname, '/api/user/info'); assert.equal(options.method, 'GET');
    assert.equal(options.headers.Authorization, 'Bearer ' + token);
    return Response.json({ code, result: { userId: 34, token }, message: token });
  };
  t.after(() => { global.fetch = original; });
  const skillsRoot = profileRoot(t, [readOwnerProfile('skills/delivery/cicd-platform/references/auth-profile.json')]);
  const gateway = await serve({ tool: 'cicd', expectedSubject: '34', port: 0, env: { SAAS_SKILLS_ROOT: skillsRoot, CICD_TOKEN: token } });
  t.after(() => gateway.close());
  assert.equal((await gateway.ensure()).state, 'READY');
  code = 500; await assert.rejects(gateway.ensure(), { code: 'API_REQUEST_FAILED' });
  assert.notEqual(gateway.status().state, 'READY');
  assert.equal(loadProfile('cicd', { skillsRoot }).runtimeVerification, 'pending');
});

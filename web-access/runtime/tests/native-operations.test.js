'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { nativeAdapter } = require('../src/native-adapter');
const { createGateway } = require('../src/gateway');

const profile = { identity: { path: '/me', subjectPath: 'user.id' }, operations: {
  list: { effect: 'read', path: '/api/content', query: {
    page: { type: 'integer', minimum: 1, maximum: 1000, default: 1 },
    limit: { type: 'integer', minimum: 1, maximum: 100, default: 10 },
    key: { type: 'string', maxLength: 80, queryName: 'where[key][equals]' },
  } },
  get: { effect: 'read', path: '/api/content/{id}', parameters: {
    id: { type: 'string', required: true, pattern: '^[a-zA-Z0-9_-]{1,80}$' },
  } },
  protected: { effect: 'read', path: '/api/content', query: {
    tenant: { type: 'string', binding: 'tenantId', queryName: 'where[tenantId][equals]' },
  } },
  write: { effect: 'write', method: 'POST', path: '/api/content' },
} };

test('registered pagination, filters, path IDs and immutable scope work through the gateway', async t => {
  const calls = [];
  const provider = { request: async (method, path) => { calls.push([method, path]); return path === '/me' ? { user: { id: 7 } } : { docs: [{ id: 'content-1' }] }; } };
  const gateway = await createGateway({ origin: 'https://fixture.example', expectedSubject: '7', port: 0,
    scope: { tenantId: 'tenant-1' }, adapter: nativeAdapter({ provider, profile }) });
  t.after(() => gateway.close());
  const call = async (operation, input) => (await fetch(gateway.url + '/v1/request', { method: 'POST',
    headers: { Authorization: 'Bearer ' + gateway.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin: gateway.origin, operation, input }) })).json();
  assert.equal((await call('list', { page: 2, key: 'a&limit=999' })).ok, true);
  const url = new URL(calls.at(-1)[1], gateway.origin);
  assert.equal(url.searchParams.get('page'), '2'); assert.equal(url.searchParams.get('limit'), '10');
  assert.equal(url.searchParams.get('where[key][equals]'), 'a&limit=999');
  assert.equal((await call('get', { id: 'content-1' })).ok, true);
  assert.equal(calls.at(-1)[1], '/api/content/content-1');
  assert.equal((await call('protected', {})).ok, true);
  assert.equal(new URL(calls.at(-1)[1], gateway.origin).searchParams.get('where[tenantId][equals]'), 'tenant-1');
  for (const [operation, input, code] of [
    ['list', { limit: 101 }, 'INVALID_INPUT'], ['list', { unknown: true }, 'INVALID_INPUT'],
    ['get', { id: '../users' }, 'INVALID_INPUT'], ['get', {}, 'INVALID_INPUT'],
    ['protected', { tenant: 'tenant-2' }, 'RESOURCE_MISMATCH'], ['write', {}, 'DOMAIN_WORKFLOW_REQUIRED'],
  ]) {
    const before = calls.length; assert.equal((await call(operation, input)).code, code); assert.equal(calls.length, before);
  }
});

test('HTTP 200 business failures cannot establish an identity or pass an operation', async () => {
  const p = { ...profile, response: { successPath: 'success', successValue: true } };
  const adapter = nativeAdapter({ profile: p, provider: { request: async () => ({ success: false, user: { id: 7 } }) } });
  await assert.rejects(adapter.verify(), e => e.code === 'API_REQUEST_FAILED');
  await assert.rejects(adapter.prepare('/v1/request', { operation: 'list', input: {} }, { scope: {} })(), e => e.code === 'API_REQUEST_FAILED');
});

test('a scoped operation rejects foreign and unverifiable returned resources', async () => {
  const p = { ...profile, operations: { list: { ...profile.operations.list,
    responseBindings: [{ binding: 'tenantId', path: 'docs.*.tenantId' }] } } };
  for (const response of [{ docs: [{ tenantId: 'foreign' }] }, { docs: [{}] }, {}]) {
    const adapter = nativeAdapter({ profile: p, provider: { request: async () => response } });
    await assert.rejects(adapter.prepare('/v1/request', { operation: 'list', input: {} }, { scope: { tenantId: 'owned' } })(), e => e.code === 'RESOURCE_MISMATCH');
  }
  const adapter = nativeAdapter({ profile: p, provider: { request: async () => ({ docs: [] }) } });
  assert.deepEqual(await adapter.prepare('/v1/request', { operation: 'list', input: {} }, { scope: { tenantId: 'owned' } })(), { docs: [] });
});

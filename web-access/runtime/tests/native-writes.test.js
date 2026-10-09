'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createGateway } = require('../src/gateway');
const { nativeAdapter } = require('../src/native-adapter');
const profile = { identity: { path: '/me', subjectPath: 'id' }, operations: {
  get: { effect: 'read', path: '/items/{id}', parameters: { id: { type: 'string', required: true } }, responseBindings: [{ binding: 'tenantId', path: 'tenantId' }] },
  create: { effect: 'write', method: 'POST', path: '/items', body: { name: { type: 'string', required: true }, tenantId: { type: 'string', binding: 'tenantId', required: true } },
    writeContract: { kind: 'crud', resultProbe: { operation: 'get', idPath: 'doc.id', inputName: 'id', matches: { name: 'body.name' } } } },
  update: { effect: 'write', method: 'PATCH', path: '/items/{id}', parameters: { id: { type: 'string', required: true } }, body: { name: { type: 'string', required: true } },
    writeContract: { kind: 'crud', preflight: { operation: 'get', input: { id: 'id' } }, resultProbe: { operation: 'get', idPath: 'doc.id', inputName: 'id', matches: { name: 'body.name' } } } },
  delete: { effect: 'write', method: 'DELETE', path: '/items/{id}', parameters: { id: { type: 'string', required: true } },
    writeContract: { kind: 'crud', preflight: { operation: 'get', input: { id: 'id' } }, resultProbe: { operation: 'get', input: { id: 'id' }, absent: true } } },
  query: { effect: 'read', method: 'POST', path: '/query', body: { term: { type: 'string', required: true } } },
  raw: { effect: 'write', method: 'POST', path: '/items' },
} };
function fixture() {
  const calls = [], docs = new Map(); let unknown = false;
  return { calls, docs, uncertain: () => { unknown = true; }, provider: { request: async (method, path, body) => {
    calls.push({ method, path, body });
    if (path === '/me') return { id: 7 };
    if (path === '/query') return { matches: [body.term] };
    const id = path.split('/').at(-1);
    if (method === 'GET') { if (!docs.has(id)) throw { code: 'RESOURCE_NOT_FOUND' }; return docs.get(id); }
    if (method === 'POST') { docs.set('one', { id: 'one', ...body }); if (unknown) throw { code: 'RESULT_UNKNOWN' }; return { doc: docs.get('one') }; }
    if (method === 'PATCH') { docs.set(id, { ...docs.get(id), ...body }); return { doc: docs.get(id) }; }
    if (method === 'DELETE') { const doc = docs.get(id); docs.delete(id); return { doc }; }
  } } };
}
async function gatewayFor(t, fixture, allowWrites, operations = ['create', 'update', 'delete']) {
  const gateway = await createGateway({ origin: 'https://fixture.example', expectedSubject: '7', port: 0, allowWrites,
    scope: { tenantId: 'tenant-one', allowedOperations: operations }, adapter: nativeAdapter({ profile, provider: fixture.provider }) });
  t.after(() => gateway.close());
  return async (operation, input = {}) => (await fetch(gateway.url + '/v1/request', { method: 'POST', headers: { Authorization: 'Bearer ' + gateway.key, 'Content-Type': 'application/json' }, body: JSON.stringify({ origin: gateway.origin, operation, input }) })).json();
}
test('registered CRUD writes are scoped, verified by reads, and cleaned up', async t => {
  const f = fixture(), call = await gatewayFor(t, f, true);
  assert.equal((await call('create', { body: { name: 'initial' } })).ok, true);
  assert.equal(f.calls.at(-1).method, 'GET');
  assert.equal(f.docs.get('one').tenantId, 'tenant-one');
  assert.equal((await call('update', { id: 'one', body: { name: 'changed' } })).ok, true);
  assert.equal(f.docs.get('one').name, 'changed');
  assert.equal((await call('delete', { id: 'one' })).ok, true);
  assert.equal(f.docs.size, 0);
});
test('readonly, unlisted, raw and schema-invalid writes fail before any upstream call', async t => {
  const f = fixture();
  const readonly = await gatewayFor(t, f, false), limited = await gatewayFor(t, f, true, ['update']);
  for (const [call, op, input, code] of [
    [readonly, 'create', { body: { name: 'n' } }, 'READ_ONLY_BRIDGE'],
    [limited, 'create', { body: { name: 'n' } }, 'OPERATION_NOT_ALLOWED'],
    [limited, 'raw', {}, 'DOMAIN_WORKFLOW_REQUIRED'],
    [limited, 'update', { id: 'one', body: { name: 'n', unknown: true } }, 'INVALID_INPUT'],
  ]) { const before = f.calls.length; assert.equal((await call(op, input)).code, code); assert.equal(f.calls.length, before); }
});
test('foreign resources are rejected by preflight and uncertain writes are never replayed', async t => {
  const f = fixture(), call = await gatewayFor(t, f, true);
  f.docs.set('foreign', { id: 'foreign', tenantId: 'other', name: 'original' });
  assert.equal((await call('update', { id: 'foreign', body: { name: 'n' } })).code, 'RESOURCE_MISMATCH');
  assert.equal(f.calls.some(c => c.method === 'PATCH'), false);
  f.uncertain();
  assert.equal((await call('create', { body: { name: 'uncertain' } })).code, 'RESULT_UNKNOWN');
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
});
test('read effect POST can execute in a readonly lease without being mistaken for mutation', async t => {
  const f = fixture(), call = await gatewayFor(t, f, false);
  const result = await call('query', { body: { term: 'read-query' } });
  assert.equal(result.ok, true); assert.deepEqual(result.data, { matches: ['read-query'] });
});

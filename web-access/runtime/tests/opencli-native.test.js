'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { installAdapter } = require('../src/opencli-install');
const { createGateway } = require('../src/gateway');
const { nativeAdapter } = require('../src/native-adapter');
const { saveCredentials } = require('../src/custody');
test('native adapters register with public OpenCLI and execute actual schema-bound bridge calls', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-native-opencli-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const profile = { schemaVersion: 1, tool: 'fixture-native', owner: 'fixture', provider: 'bearer-env', origin: 'https://fixture.example',
    identity: { path: '/me', subjectPath: 'id' }, operations: {
      list: { effect: 'read', path: '/items', query: { page: { type: 'integer', minimum: 1, maximum: 10, default: 1 } } },
      write: { effect: 'write', method: 'POST', path: '/items' },
    } };
  const gateway = await createGateway({ origin: 'https://fixture.example', port: 0, expectedSubject: '7',
    adapter: nativeAdapter({ profile, provider: { request: async (method, url) => url === '/me' ? { id: 7 } : { docs: [{ id: 'item-1' }], page: new URL(url, 'https://fixture.example').searchParams.get('page') } } }) });
  t.after(() => gateway.close());
  const file = path.join(root, 'credentials.json'); const cleanup = saveCredentials(file, { url: gateway.url, origin: gateway.origin, key: gateway.key, expiresAt: gateway.expiresAt });
  t.after(cleanup);
  const installed = installAdapter({ profile, root });
  fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'));
  await import(pathToFileURL(installed.file).href);
  const { getRegistry } = await import(pathToFileURL(require.resolve('@jackwener/opencli/registry')).href);
  const registry = getRegistry();
  assert.equal(registry.has('fixture-native/write'), false);
  const input = path.join(root, 'input.json'); fs.writeFileSync(input, JSON.stringify({ page: 2 }));
  const result = await registry.get('fixture-native/list').func({ 'credentials-file': file, 'input-file': input });
  assert.equal(result[0].result.page, '2'); assert.equal(JSON.stringify(result).includes(gateway.key), false);
  const foreign = await createGateway({ origin: 'https://foreign.example', port: 0, expectedSubject: '7',
    adapter: nativeAdapter({ profile, provider: { request: async () => { throw new Error('must not call foreign provider'); } } }) });
  t.after(() => foreign.close());
  const foreignFile = path.join(root, 'foreign.json');
  const removeForeign = saveCredentials(foreignFile, { url: foreign.url, origin: foreign.origin, key: foreign.key, expiresAt: foreign.expiresAt });
  t.after(removeForeign);
  await assert.rejects(registry.get('fixture-native/list').func({ 'credentials-file': foreignFile }), e => e.code === 'BRIDGE_ORIGIN_MISMATCH');
  fs.writeFileSync(input, '{"page":999}');
  await assert.rejects(registry.get('fixture-native/list').func({ 'credentials-file': file, 'input-file': input }), e => e.code === 'INVALID_INPUT');
  fs.writeFileSync(installed.file, 'user edit');
  assert.throws(() => installAdapter({ profile, root }), e => e.code === 'ADAPTER_EXISTS');
});

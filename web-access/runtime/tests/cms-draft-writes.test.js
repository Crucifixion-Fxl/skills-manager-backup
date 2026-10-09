'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { serve, loadProfile } = require('../src');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { installAdapter } = require('../src/opencli-install');
const { saveCredentials } = require('../src/custody');
const { profileRoot, cmsDraftProfile } = require('./profile-fixtures');
test('source-declared CMS drafts create/update/read locally and block unsafe document deletion and reject publishing or immutable changes', async t => {
  const original = global.fetch, records = new Map(), methods = [];
  const token = 'fixture-cms-private-token';
  const cmsOrigin = 'https://cms.fixture.invalid';
  const skillsRoot = profileRoot(t, [cmsDraftProfile(cmsOrigin)]);
  let publishedMain = false;
  global.fetch = async (url, options = {}) => {
    const target = new URL(url);
    if (target.origin !== cmsOrigin) {
      assert.equal(target.hostname, '127.0.0.1', 'fixture must never access any external service');
      return original(url, options);
    }
    assert.equal(options.headers.Authorization, 'Bearer ' + token);
    const reply = (data, status = 200) => new Response(JSON.stringify(data), { status });
    if (target.pathname === '/api/users/me') return reply({ user: { email: 'fixture@example.invalid' } });
    if (target.pathname === '/api/access') return reply({ collections: { 'color-categories': Object.fromEntries(['read','create','update','delete'].map(k => [k, { permission: true }])) } });
    assert.match(target.pathname, /^\/api\/color-categories(?:\/test1)?$/);
    methods.push(options.method);
    if (options.method === 'POST') { assert.equal(target.searchParams.get('draft'), 'true'); const doc = { id: 'test1', ...JSON.parse(options.body) }; records.set(doc.id, doc); return reply({ doc }); }
    if (!records.has('test1')) return reply({ errors: [] }, 404);
    if (options.method === 'GET') return reply({ ...records.get('test1'), ...(target.searchParams.get('draft') === 'false' && publishedMain ? { _status: 'published' } : {}) });
    if (options.method === 'PATCH') { assert.equal(target.searchParams.get('draft'), 'true'); Object.assign(records.get('test1'), JSON.parse(options.body)); return reply({ doc: records.get('test1') }); }
    if (options.method === 'DELETE') { const doc = records.get('test1'); records.delete('test1'); return reply({ doc }); }
    assert.fail('unexpected method');
  };
  t.after(() => { global.fetch = original; });
  const operations = ['color-categories-draft-create','color-categories-draft-update','color-categories-unpublished-delete'];
  const gateway = await serve({ tool: 'marketing-cms', expectedSubject: 'fixture@example.invalid', allowWrites: true,
    allowedOperations: operations, port: 0, env: { SAAS_SKILLS_ROOT: skillsRoot, MARKETING_CMS_TOKEN: token } });
  t.after(() => gateway.close());
  const call = async (operation, input) => (await original(gateway.url + '/v1/request', { method: 'POST',
    headers: { Authorization: 'Bearer ' + gateway.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin: gateway.origin, operation, input }) })).json();
  assert.equal((await gateway.ensure()).state, 'READY');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cms-draft-opencli-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'));
  const credentialsFile = path.join(root, 'lease.json');
  saveCredentials(credentialsFile, { url: gateway.url, origin: gateway.origin, key: gateway.key, expiresAt: gateway.expiresAt });
  const installed = installAdapter({ profile: loadProfile('marketing-cms', { skillsRoot }), root });
  await import(pathToFileURL(installed.file).href);
  const { getRegistry } = await import(pathToFileURL(require.resolve('@jackwener/opencli/registry')).href);
  const registry = getRegistry();
  const opencli = async (operation, input) => {
    const inputFile = path.join(root, 'input.json'); fs.writeFileSync(inputFile, JSON.stringify(input));
    return registry.get('marketing-cms/' + operation).func({ 'credentials-file': credentialsFile, 'input-file': inputFile });
  };

  assert.equal((await opencli(operations[0], { body: { color_class: 'saas-access-acceptance', display_name: 'Fixture' } }))[0].result.doc.id, 'test1');
  assert.equal(records.get('test1')._status, 'draft');
  let before = methods.length;
  assert.equal((await call(operations[1], { id: 'test1', body: { _status: 'published', display_name: 'Unsafe' } })).code, 'INVALID_INPUT');
  assert.equal((await call(operations[1], { id: 'test1', body: { color_class: 'changed', display_name: 'Unsafe' } })).code, 'INVALID_INPUT');
  assert.equal(methods.length, before);
  assert.equal((await opencli(operations[1], { id: 'test1', body: { display_name: 'Updated' } }))[0].result.doc.display_name, 'Updated');
  assert.equal(records.get('test1').display_name, 'Updated');
  records.get('test1')._status = 'published'; before = methods.filter(x => x === 'DELETE').length;
  assert.equal((await call(operations[2], { id: 'test1' })).code, 'DOMAIN_WORKFLOW_REQUIRED');
  assert.equal(methods.filter(x => x === 'DELETE').length, before);
  records.get('test1')._status = 'draft';
  publishedMain = true;
  assert.equal((await call(operations[2], { id: 'test1' })).code, 'DOMAIN_WORKFLOW_REQUIRED');
  assert.equal(methods.filter(x => x === 'DELETE').length, before);
  publishedMain = false;
  assert.equal(registry.has('marketing-cms/' + operations[2]), false);
  assert.equal((await call(operations[2], { id: 'test1' })).code, 'DOMAIN_WORKFLOW_REQUIRED');
  // Local fixture owns the in-memory data; no platform delete is sent for cleanup.
  records.clear();
  assert.equal(records.size, 0);
});

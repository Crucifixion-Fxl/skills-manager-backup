'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { loadProfile, serve } = require('../src');
test('standalone package loads explicit installed plugin profiles and rejects missing catalog or escaped owner files', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-profile-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const skillsRoot = path.join(root, 'skills'); fs.mkdirSync(skillsRoot);
  assert.throws(() => loadProfile('fixture', { skillsRoot }), e => e.code === 'PROFILE_CATALOG_REQUIRED');
  const catalog = path.join(skillsRoot, 'platform-onboarding/references'); fs.mkdirSync(catalog, { recursive: true });
  const owner = path.join(skillsRoot, 'fixture/references'); fs.mkdirSync(owner, { recursive: true });
  const file = path.join(owner, 'auth-profile.json');
  const profile = { schemaVersion: 1, tool: 'fixture', owner: 'fixture', provider: 'bearer-env' };
  fs.writeFileSync(file, JSON.stringify(profile));
  fs.writeFileSync(path.join(catalog, 'tools.json'), JSON.stringify({ tools: [{ id: 'fixture', owner: 'fixture', accessProfile: 'skills/fixture/references/auth-profile.json' }] }));
  assert.deepEqual(loadProfile('fixture', { skillsRoot }), profile);
  fs.writeFileSync(file, JSON.stringify({ ...profile, owner: 'different-owner' }));
  assert.throws(() => loadProfile('fixture', { skillsRoot }), e => e.code === 'INVALID_PROVIDER');
  fs.unlinkSync(file); fs.writeFileSync(path.join(root, 'foreign.json'), JSON.stringify(profile)); fs.symlinkSync(path.join(root, 'foreign.json'), file);
  assert.throws(() => loadProfile('fixture', { skillsRoot }), e => e.code === 'INVALID_PROVIDER');
});

test('legacy catalog probing is explicit even when serve resolves a root internally', async t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-profile-legacy-'));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  const root = path.join(temp, 'skills'); fs.mkdirSync(root);
  const catalog = path.join(root, 'platform-onboarding/references'); fs.mkdirSync(catalog, { recursive: true });
  const owner = path.join(root, 'fixture/references'); fs.mkdirSync(owner, { recursive: true });
  const profile = { schemaVersion: 1, tool: 'fixture', owner: 'fixture', provider: 'bearer-env' };
  fs.writeFileSync(path.join(owner, 'auth-profile.json'), JSON.stringify(profile));
  fs.writeFileSync(path.join(catalog, 'tools.json'), JSON.stringify({ tools: [
    { id: 'fixture', owner: 'fixture', accessProfile: 'skills/fixture/references/auth-profile.json' },
  ] }));

  assert.deepEqual(loadProfile('fixture', { skillsRoot: root }), profile, 'direct explicit roots retain legacy layout support');
  assert.throws(() => loadProfile('fixture', { skillsRoot: root, allowLegacyCatalog: false }),
    error => error.code === 'PROFILE_CATALOG_REQUIRED');

  const previous = process.env.SAAS_SKILLS_ROOT;
  process.env.SAAS_SKILLS_ROOT = root;
  t.after(() => { if (previous === undefined) delete process.env.SAAS_SKILLS_ROOT; else process.env.SAAS_SKILLS_ROOT = previous; });
  await assert.rejects(serve({ tool: 'fixture', env: {}, expectedSubject: 'unused', port: 0 }),
    error => error.code === 'PROFILE_CATALOG_REQUIRED');
});

test('tracking bridge path is authoritative, explicit for legacy roots, and realpath-contained', t => {
  const { resolveTrackingBridgePath } = require('../src');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-bridge-root-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const canonical = path.join(root, 'observability/tracking-lifecycle/cli/src');
  const legacy = path.join(root, 'tracking-lifecycle/cli/src');
  fs.mkdirSync(canonical, { recursive: true });
  fs.mkdirSync(legacy, { recursive: true });
  fs.writeFileSync(path.join(legacy, 'remote-bridge.js'), 'module.exports = {};');

  assert.throws(() => resolveTrackingBridgePath(root, true), error => error.code === 'PROFILE_NOT_VERIFIED',
    'an incomplete categorized runtime must not fall back to a legacy copy');
  fs.rmSync(canonical, { recursive: true });
  assert.throws(() => resolveTrackingBridgePath(root, false), error => error.code === 'PROFILE_NOT_VERIFIED');
  assert.equal(resolveTrackingBridgePath(root, true), fs.realpathSync(path.join(legacy, 'remote-bridge.js')));

  const outside = path.join(path.dirname(root), 'external-remote-bridge.js');
  fs.writeFileSync(outside, 'module.exports = {};');
  t.after(() => fs.rmSync(outside, { force: true }));
  fs.mkdirSync(canonical, { recursive: true });
  fs.symlinkSync(outside, path.join(canonical, 'remote-bridge.js'));
  assert.throws(() => resolveTrackingBridgePath(root, true), error => error.code === 'PROFILE_NOT_VERIFIED',
    'a categorized bridge resolving outside skillsRoot must fail closed');
});

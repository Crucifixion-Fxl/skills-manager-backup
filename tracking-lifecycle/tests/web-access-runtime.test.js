'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const resolverSource = fs.readFileSync(path.join(__dirname, '../cli/src/web-access-runtime.js'), 'utf8');

function fixture(t, callerLayout, { canonical = false, legacy = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-runtime-layout-'));
  const skillsRoot = path.join(root, 'skills');
  const caller = path.join(skillsRoot, callerLayout, 'cli/src');
  fs.mkdirSync(caller, { recursive: true });
  const resolver = path.join(caller, 'web-access-runtime.js');
  fs.writeFileSync(resolver, resolverSource);
  const runtimeRoots = {};
  if (canonical) runtimeRoots.canonical = path.join(skillsRoot, 'agent-harness/web-access/runtime/src');
  if (legacy) runtimeRoots.legacy = path.join(skillsRoot, 'web-access/runtime/src');
  for (const runtimeRoot of Object.values(runtimeRoots)) fs.mkdirSync(runtimeRoot, { recursive: true });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, skillsRoot, resolver, runtimeRoots };
}

function resolveInFixture(resolver, name = 'probe.js') {
  const source = `try { const value = require(${JSON.stringify(resolver)}).webAccessRuntimeModule(${JSON.stringify(name)}); process.stdout.write(JSON.stringify(value)); } catch (error) { process.stdout.write(JSON.stringify({ error: error.code })); }`;
  return spawnSync(process.execPath, ['-e', source], { encoding: 'utf8' });
}

test('resolver derives the real flat caller root and loads its legacy flat runtime', t => {
  const f = fixture(t, 'tracking-lifecycle', { legacy: true });
  fs.writeFileSync(path.join(f.runtimeRoots.legacy, 'probe.js'), "module.exports = { layout: 'legacy-flat' };\n");
  const result = resolveInFixture(f.resolver);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { layout: 'legacy-flat' });
});

test('categorized runtime wins when both layouts exist and an escaped module fails closed', t => {
  const f = fixture(t, 'observability/tracking-lifecycle', { canonical: true, legacy: true });
  fs.writeFileSync(path.join(f.runtimeRoots.canonical, 'probe.js'), "module.exports = { layout: 'categorized' };\n");
  fs.writeFileSync(path.join(f.runtimeRoots.legacy, 'probe.js'), "module.exports = { layout: 'legacy-flat' };\n");
  let result = resolveInFixture(f.resolver);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { layout: 'categorized' });

  const outside = path.join(f.root, 'outside.js');
  fs.writeFileSync(outside, "module.exports = { layout: 'outside' };\n");
  fs.rmSync(path.join(f.runtimeRoots.canonical, 'probe.js'));
  fs.symlinkSync(outside, path.join(f.runtimeRoots.canonical, 'probe.js'));
  result = resolveInFixture(f.resolver);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { error: 'WEB_ACCESS_RUNTIME_UNAVAILABLE' });

  fs.rmSync(path.join(f.skillsRoot, 'agent-harness/web-access/runtime/src'), { recursive: true, force: true });
  const outsideRoot = path.join(f.root, 'external-runtime-src');
  fs.mkdirSync(outsideRoot, { recursive: true });
  fs.writeFileSync(path.join(outsideRoot, 'probe.js'), "module.exports = { layout: 'outside-root' };\n");
  fs.symlinkSync(outsideRoot, path.join(f.skillsRoot, 'agent-harness/web-access/runtime/src'), 'dir');
  result = resolveInFixture(f.resolver);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { error: 'WEB_ACCESS_RUNTIME_UNAVAILABLE' });
});

test('resolver rejects path-like module names instead of accepting arbitrary require paths', t => {
  const f = fixture(t, 'observability/tracking-lifecycle', { canonical: true, legacy: true });
  const result = resolveInFixture(f.resolver, '../outside.js');
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { error: 'WEB_ACCESS_RUNTIME_UNAVAILABLE' });
});

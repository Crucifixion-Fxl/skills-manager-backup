'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { install, adapterSource } = require('../cli/src/opencli-install');

test('generated adapter registers with public OpenCLI API and preserves a production gate', async t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-adapter-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const entry = path.resolve(__dirname, '../cli/bin/events-tdd.js');
  const file = path.join(folder, 'adapter.mjs');
  fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(folder, 'node_modules'));
  fs.writeFileSync(file, adapterSource(entry));
  await import(pathToFileURL(file).href);
  const { getRegistry } = await import(pathToFileURL(require.resolve('@jackwener/opencli/registry')).href);
  const commands = getRegistry();
  const coverage = commands.get('tracking/site-coverage');
  const result = await coverage.func({});
  assert.equal(result[0].result.summary.frontendPages, 21);
  assert.equal(result[0].result.summary.missingBackendRoutes, 8);
  await assert.rejects(commands.get('tracking/publish').func({}), e => e.code === 'PROTECTED_CI_REQUIRED');
  await assert.rejects(commands.get('tracking/api-call').func({ 'args-file': (() => {
    const input = path.join(folder, 'args.json'); fs.writeFileSync(input, '["--token=must-not-pass"]'); return input;
  })() }), e => e.code === 'INVALID_AUTH');
  const installed = install({ root: folder });
  fs.writeFileSync(installed.file, 'user edit');
  assert.throws(() => install({ root: folder }), e => e.code === 'ADAPTER_EXISTS');
});

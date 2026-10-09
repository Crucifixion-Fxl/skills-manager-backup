'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { webAccessRuntimeModule } = require('../cli/src/web-access-runtime');
const browser = webAccessRuntimeModule('browser.js');
const ssh = webAccessRuntimeModule('ssh.js');
const { createClient } = webAccessRuntimeModule('client.js');
test('bridge and SSH entrypoints use the selected extension tab, and foreground cleanup removes its lease file', { timeout: 5000 }, async t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'bridge-entrypoint-'));
  let selected;
  const originalConnect = browser.connectBrowser, originalSpec = ssh.sshSpec, originalTunnel = ssh.serveTunnel;
  browser.connectBrowser = async options => {
    selected = options;
    if (!options.profile || !options.tab) { const e = Error('BROWSER_TARGET_REQUIRED'); e.code = 'BROWSER_TARGET_REQUIRED'; throw e; }
    return { supportsWrites: false, cdp: async (_method, params) => ({ result: { value: { ok: true, status: 200,
      payload: { code: 200, data: params.expression.includes('/api/user/getCurrentUser') ? { userId: 7 } : [{ id: 14 }] } } } }), close: async () => {} };
  };
  const paths = ['../cli/src/browser-transport', '../cli/src/remote-bridge', '../cli/src/ssh-bridge'].map(p => require.resolve(p));
  for (const p of paths) delete require.cache[p];
  const { serveBridge } = require('../cli/src/remote-bridge');
  const { serveTunnel } = require('../cli/src/ssh-bridge');
  t.after(() => { browser.connectBrowser = originalConnect; ssh.sshSpec = originalSpec; ssh.serveTunnel = originalTunnel; for (const p of paths) delete require.cache[p]; fs.rmSync(folder, { recursive: true, force: true }); });
  const args = { port: '0', session: 'fixture', profile: 'fixture-profile', tab: 'fixture-tab', 'expected-subject': '7', 'application-id': '14', 'private-output': folder + '/credentials.json' };
  const env = { TRACKING_PLATFORM_BASE_URL: 'https://fixture.example' };
  let probe;
  await serveBridge(args, env, () => {
    probe = createClient({ file: args['private-output'] }).ensure().then(r => ({ state: r.state }), e => ({ error: e.code })).finally(() => process.emit('SIGTERM'));
  });
  assert.deepEqual(await probe, { state: 'READY' });
  assert.equal(selected.tab, 'fixture-tab'); assert.equal(fs.existsSync(args['private-output']), false);
  ssh.sshSpec = () => ({});
  ssh.serveTunnel = async (_args, bridge) => { try { return await bridge.ensure(); } finally { await bridge.close(); } };
  const state = await serveTunnel(args, env, () => {});
  assert.equal(state.state, 'READY'); assert.equal(selected.tab, 'fixture-tab');
});

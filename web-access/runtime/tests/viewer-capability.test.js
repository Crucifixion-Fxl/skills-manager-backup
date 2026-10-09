'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const { once } = require('node:events');
const WebSocket = require('ws');
const { createViewer } = require('../src/viewer');
async function fixture(t, options = {}) {
  const tcp = net.createServer(socket => socket.write('RFB 003.008\n'));
  await new Promise(resolve => tcp.listen(0, '127.0.0.1', resolve));
  const viewer = await createViewer({ port: 0, vncPort: tcp.address().port, ...options });
  t.after(async () => { await viewer.close(); await new Promise(resolve => tcp.close(resolve)); });
  return viewer;
}
async function bootstrap(viewer, capability = viewer.capability) {
  const response = await fetch(viewer.url + 'bootstrap', { method: 'POST', headers: { Origin: new URL(viewer.url).origin,
    'Content-Type': 'application/json' }, body: JSON.stringify(capability) });
  if (response.status === 200) { const data = await response.clone().json(); response.protocols = ['web-view-v1', 'web-view-proof.' + data.session + '.' + data.proof]; }
  return response;
}
const cookieOf = response => response.headers.get('set-cookie')?.split(';')[0];
test('HTTP and VNC WebSocket reject unauthenticated loopback requests', async t => {
  const viewer = await fixture(t);
  for (const route of ['', 'viewer.js', 'novnc/core/rfb.js']) {
    assert.equal((await fetch(viewer.url + route)).status, 403);
  }
  const ws = new WebSocket(viewer.url.replace('http:', 'ws:') + 'websockify', { origin: new URL(viewer.url).origin });
  await once(ws, 'error');
});
test('task capability bootstrap is private, one-time and cookies do not authorize another session', async t => {
  const first = await fixture(t), second = await fixture(t);
  assert.equal((await bootstrap(first, { ...first.capability, token: 'wrong' })).status, 403);
  assert.equal((await bootstrap(second, first.capability)).status, 403);
  const response = await bootstrap(first);
  assert.equal(response.status, 200);
  const cookie = cookieOf(response);
  assert.match(response.headers.get('set-cookie'), /HttpOnly/);
  assert.match(response.headers.get('set-cookie'), /SameSite=Strict/);
  assert.equal((await bootstrap(first)).status, 403);
  assert.equal((await fetch(first.url, { headers: { Cookie: cookie } })).status, 200);
  assert.equal((await fetch(second.url, { headers: { Cookie: cookie } })).status, 403);
  const ws = new WebSocket(first.url.replace('http:', 'ws:') + 'websockify', response.protocols, {
    origin: new URL(first.url).origin, headers: { Cookie: cookie } });
  const [banner] = await once(ws, 'message');
  assert.equal(banner.toString(), 'RFB 003.008\n'); ws.terminate();
});
test('expired bootstrap is rejected and TTL cleanup invalidates active viewer', async t => {
  const viewer = await fixture(t, { ttlMs: 1000 });
  assert.equal((await bootstrap(viewer, { ...viewer.capability, expiresAt: 1 })).status, 403);
  const response = await bootstrap(viewer);
  assert.equal(response.status, 200);
  const cookie = cookieOf(response);
  await new Promise(resolve => setTimeout(resolve, 1100));
  await assert.rejects(fetch(viewer.url, { headers: { Cookie: cookie } }));
});
test('expiry and explicit task revocation close existing VNC WebSockets', async t => {
  for (const expired of [false, true]) {
    const viewer = await fixture(t, { ttlMs: expired ? 1000 : 30000 });
    const response = await bootstrap(viewer);
    const ws = new WebSocket(viewer.url.replace('http:', 'ws:') + 'websockify', response.protocols, {
      origin: new URL(viewer.url).origin, headers: { Cookie: cookieOf(response) } });
    await once(ws, 'message');
    const closed = once(ws, 'close');
    if (!expired) await viewer.close();
    await closed;
    await assert.rejects(fetch(viewer.url));
  }
});
test('private capability and launch files have restrictive modes, no secrets in opener argv, and clean up', async t => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const { readCapability, launchBootstrap } = require('../src/viewer');
  const { EventEmitter } = require('node:events');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'viewer-private-test-')); fs.chmodSync(root, 0o700);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const capfile = path.join(root, 'cap.json');
  const viewer = await fixture(t, { capabilityFile: capfile });
  assert.equal(fs.statSync(capfile).mode & 0o777, 0o600);
  assert.deepEqual(readCapability(capfile), viewer.capability);
  let launchArgs;
  const launcher = launchBootstrap(viewer.url, viewer.capability, { spawnImpl: (...args) => { launchArgs = args; return new EventEmitter(); } });
  assert.equal(fs.statSync(launcher.file).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.dirname(launcher.file)).mode & 0o777, 0o700);
  assert.equal(JSON.stringify(launchArgs).includes(viewer.capability.token), false);
  assert.equal(viewer.url.includes(viewer.capability.token), false);
  assert.match(fs.readFileSync(launcher.file, 'utf8'), /bootstrap#/);
  launcher.close(); assert.equal(fs.existsSync(launcher.file), false);
  await viewer.close(); assert.equal(fs.existsSync(capfile), false);
});
test('unsafe private file modes and preexisting files fail closed without deleting another task file', async t => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const { readCapability } = require('../src/viewer');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'viewer-private-test-')); fs.chmodSync(root, 0o700);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, 'cap.json'); fs.writeFileSync(file, 'other task', { mode: 0o600 });
  await assert.rejects(createViewer({ port: 0, vncPort: 5900, capabilityFile: file }), e => e.code === 'INVALID_VIEWER_CAPABILITY_FILE');
  assert.equal(fs.readFileSync(file, 'utf8'), 'other task');
  fs.chmodSync(file, 0o644);
  assert.throws(() => readCapability(file), e => e.code === 'INVALID_VIEWER_CAPABILITY_FILE');
  const link = path.join(root, 'link'); fs.symlinkSync(file, link);
  assert.throws(() => readCapability(link));
});
test('malformed Unicode cookie and bootstrap fields reject without crashing the server', async t => {
  const viewer = await fixture(t);
  const response = await bootstrap(viewer);
  const name = cookieOf(response).split('=')[0];
  assert.equal((await fetch(viewer.url, { headers: { Cookie: name + '=' + 'é'.repeat(64) } })).status, 403);
  assert.equal((await fetch(viewer.url, { headers: { Cookie: cookieOf(response) } })).status, 200);
});
test('browser opener failures are sanitized and destroy private launcher', async t => {
  const fs = require('node:fs');
  const { EventEmitter } = require('node:events');
  const { launchBootstrap } = require('../src/viewer');
  const viewer = await fixture(t);
  const launcher = launchBootstrap(viewer.url, viewer.capability, { spawnImpl: () => {
    const child = new EventEmitter(); queueMicrotask(() => child.emit('exit', 1)); return child;
  } });
  await assert.rejects(launcher.opened, e => e.code === 'VIEWER_BROWSER_OPEN_FAILED' && !e.message.includes(viewer.capability.token));
  assert.equal(fs.existsSync(launcher.file), false);
});
test('cookie disclosed to another loopback port cannot open RFB without origin-scoped proof', async t => {
  const first = await fixture(t), second = await fixture(t);
  const response = await bootstrap(first), other = await bootstrap(second);
  // A cookie is host-scoped, so it alone is never a VNC authorization.
  for (const protocols of [undefined, other.protocols, ['web-view-v1', 'web-view-proof.wrong']]) {
    const ws = new WebSocket(first.url.replace('http:', 'ws:') + 'websockify', protocols, {
      origin: new URL(first.url).origin, headers: { Cookie: cookieOf(response) } });
    await once(ws, 'error');
  }
  const ws = new WebSocket(second.url.replace('http:', 'ws:') + 'websockify', response.protocols, {
    origin: new URL(second.url).origin, headers: { Cookie: cookieOf(response) } });
  await once(ws, 'error');
});

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readCapability } = require('../src/viewer');
const net = require('node:net');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { once } = require('node:events');
const WebSocket = require('ws');
const entry = path.join(__dirname, '../bin/web-view.js');

test('local viewer CLI forwards the explicitly owned VNC port and removes its listener on exit', { timeout: 10000 }, async t => {
  const tcp = net.createServer(socket => socket.write('RFB 003.008\n'));
  await new Promise(resolve => tcp.listen(0, '127.0.0.1', resolve));
  const child = spawn(process.execPath, [entry, '--port=0', `--vnc-port=${tcp.address().port}`], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stopped = false, peer;
  const exited = once(child, 'exit').then(([code, signal]) => { stopped = true; return { code, signal }; });
  t.after(async () => {
    peer?.terminate();
    if (!stopped) child.kill('SIGTERM');
    await exited;
    await new Promise(resolve => tcp.close(resolve));
  });
  const output = await new Promise((resolve, reject) => {
    let buffer = '', errors = '';
    child.stderr.on('data', bytes => { errors += bytes; });
    child.stdout.on('data', bytes => { buffer += bytes; if (buffer.includes('\n')) resolve(JSON.parse(buffer.split('\n')[0])); });
    child.once('exit', () => reject(Error(errors || 'CLI exited before listening')));
    child.once('error', reject);
  });
  assert.equal(output.state, 'VIEWER_LISTENING');
  assert.equal(new URL(output.url).hostname, '127.0.0.1');
  const capability = readCapability(output.capabilityFile);
  const response = await fetch(output.url + 'bootstrap', { method: 'POST', headers: { Origin: new URL(output.url).origin }, body: JSON.stringify(capability) });
  assert.equal(response.status, 200);
  const proof = await response.clone().json();
  const protocols = ['web-view-v1', 'web-view-proof.' + proof.session + '.' + proof.proof];
  const cookie = response.headers.get('set-cookie').split(';')[0];
  assert.equal((await fetch(output.url, { headers: { Cookie: cookie } })).status, 200);
  assert.equal(fs.existsSync(output.capabilityFile), false);
  assert.equal(JSON.stringify(output).includes(capability.token), false);
  peer = new WebSocket(output.url.replace('http:', 'ws:') + 'websockify', protocols, { headers: { Cookie: cookie }, origin: new URL(output.url).origin });
  const [banner] = await once(peer, 'message');
  assert.equal(banner.toString(), 'RFB 003.008\n');
  peer.terminate();
  child.kill('SIGTERM');
  assert.deepEqual(await exited, { code: 0, signal: null });
  await assert.rejects(fetch(output.url));
});

test('local viewer CLI refuses to guess a VNC port when no task-owned port is supplied', () => {
  const result = spawnSync(process.execPath, [entry, '--port=0'], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'INVALID_VIEWER_CONFIG');
});

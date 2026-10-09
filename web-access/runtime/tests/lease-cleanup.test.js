'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { remoteCredentialWorker } = require('../src/ssh');
test('foreground Companion removes its credentials and exits when the lease expires', { timeout: 5000 }, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-lease-')), file = path.join(root, 'credentials.json');
  const child = spawn(process.execPath, [path.resolve(__dirname, '../bin/web-access.js'), 'serve', '--tool=superset', '--expected-subject=1', '--port=0', '--ttl-seconds=1', `--credentials-file=${file}`],
    { env: { PATH: process.env.PATH, SAAS_SKILLS_ROOT: path.resolve(__dirname, '../../../..') }, stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(() => { child.kill('SIGTERM'); fs.rmSync(root, { recursive: true, force: true }); });
  const exited = new Promise(resolve => child.once('exit', resolve));
  const ready = await new Promise(resolve => child.stdout.once('data', data => resolve(data.toString())));
  assert.match(ready, /COMPANION_LISTENING/); assert.equal(fs.existsSync(file), true);
  assert.equal(await exited, 0); assert.equal(fs.existsSync(file), false);
});
test('remote worker removes the private file on expiry even while SSH stdin stays open', { timeout: 5000 }, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-remote-expiry-')), file = path.join(root, 'credentials.json');
  const server = http.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const child = spawn(process.execPath, ['-e', `(${remoteCredentialWorker.toString()})(${JSON.stringify(file)})`], { stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(async () => { child.kill('SIGTERM'); fs.rmSync(root, { recursive: true, force: true }); await new Promise(resolve => server.close(resolve)); });
  const exit = new Promise(resolve => child.once('exit', resolve));
  const ready = new Promise(resolve => child.stdout.once('data', data => resolve(data.toString())));
  child.stdin.write(JSON.stringify({ url: `http://127.0.0.1:${server.address().port}`, origin: 'https://fixture.example', key: 'a'.repeat(64), expiresAt: Date.now() + 1000 }) + '\n');
  assert.match(await ready, /TUNNEL_READY/); assert.equal(fs.existsSync(file), true);
  assert.equal(await exit, 0); assert.equal(fs.existsSync(file), false);
});

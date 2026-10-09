'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { readCredentials, saveCredentials } = require('../src/custody');
const { sshSpec, remoteCredentialWorker } = require('../src/ssh');
const { parse } = require('../bin/web-access');
const value = { url: 'http://127.0.0.1:19826', origin: 'https://fixture.example', key: 'a'.repeat(64), expiresAt: 1 };
test('private credential custody rejects symlinks and shared parents and protects replacements on cleanup', t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-custody-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = folder + '/credentials.json', cleanup = saveCredentials(file, value);
  assert.deepEqual(readCredentials(file), value);
  fs.chmodSync(folder, 0o755);
  assert.throws(() => readCredentials(file), e => e.code === 'INVALID_BRIDGE_CREDENTIALS');
  fs.chmodSync(folder, 0o700);
  fs.renameSync(file, file + '.previous'); fs.writeFileSync(file, 'replacement');
  cleanup(); assert.equal(fs.readFileSync(file, 'utf8'), 'replacement');
  fs.symlinkSync(file + '.previous', folder + '/link');
  assert.throws(() => readCredentials(folder + '/link'), e => e.code === 'INVALID_BRIDGE_CREDENTIALS');
});
test('SSH owns stdin credential lifetime and does not embed a bridge key in argv', async t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-worker-')), file = folder + '/private/credentials.json';
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const spec = sshSpec({ 'ssh-target': 'devbox', 'remote-credentials': file }, 19827);
  assert.equal(spec.argv.includes('127.0.0.1:19826:127.0.0.1:19827'), true);
  assert.equal(spec.argv.join(' ').includes(value.key), false);
  const listener = http.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => listener.close(resolve)));
  const forwarded = { ...value, expiresAt: Date.now() + 60000, url: 'http://127.0.0.1:' + listener.address().port };
  const worker = spawn(process.execPath, ['-e', `(${remoteCredentialWorker.toString()})(${JSON.stringify(file)})`], { stdio: ['pipe', 'pipe', 'pipe'] });
  const exit = new Promise(resolve => worker.once('exit', code => resolve(code)));
  const ready = new Promise(resolve => worker.stdout.once('data', chunk => resolve(chunk.toString())));
  worker.stdin.write(JSON.stringify(forwarded) + '\n');
  assert.equal(await ready, '{"status":"TUNNEL_READY"}\n');
  assert.deepEqual(readCredentials(file), forwarded);
  worker.stdin.end(); assert.equal(await exit, 0); assert.equal(fs.existsSync(file), false);
});
test('CLI rejects duplicate/unknown options and secret arguments', () => {
  for (const argv of [['serve', '--token=secret'], ['serve', '--tool=a', '--tool=b'], ['serve', '--tool', 'a']]) {
    assert.throws(() => parse(argv), e => e.code === 'INVALID_INPUT');
  }
});

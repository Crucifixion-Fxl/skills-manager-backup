'use strict';
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const fs = require('node:fs');
const path = require('node:path');
const { fail } = require('./gateway');
const execute = promisify(execFile);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function sshFailure(text) {
  if (/address already in use|cannot listen to port|could not request local forwarding|port forwarding failed/i.test(text)) return 'LOCAL_PORT_IN_USE';
  if (/permission denied|authentication failed|sign_and_send_pubkey|read_passphrase/i.test(text)) return 'SSH_AUTH_REQUIRED';
  if (/administratively prohibited|open failed.*prohibited/i.test(text)) return 'SSH_FORWARDING_DENIED';
  if (/could not resolve|connection refused|connection timed out|no route to host/i.test(text)) return 'SSH_UNREACHABLE';
  if (/host key verification failed|remote host identification has changed/i.test(text)) return 'SSH_HOSTKEY_REQUIRED';
  return 'SSH_TUNNEL_FAILED';
}
async function openSshMaster({ target, spawnImpl = spawn, controlImpl = execute } = {}) {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,199}$/.test(target || '')) fail('INVALID_SSH_CONFIG');
  // This private path also fits macOS's Unix socket path limit.
  const root = fs.mkdtempSync(path.join('/tmp', 'saas-ssh-')); fs.chmodSync(root, 0o700);
  const controlPath = path.join(root, 'ctl');
  let child, exited = false, failure = 'SSH_TUNNEL_FAILED';
  const cleanup = () => fs.rmSync(root, { recursive: true, force: true });
  try {
    child = spawnImpl('ssh', ['-N', '-T', '-M', '-S', controlPath,
      '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes', '-o', 'ControlPersist=no', '-o', 'ExitOnForwardFailure=yes',
      '-o', 'GatewayPorts=no', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', target],
      { stdio: ['ignore', 'ignore', 'pipe'] });
    child.stderr.on('data', chunk => { failure = sshFailure(chunk.toString()); });
    const exit = new Promise(resolve => {
      const done = code => { if (exited) return; exited = true; cleanup(); resolve(code); };
      child.once('exit', done); child.once('error', () => done(1));
    });
    const control = args => controlImpl('ssh', ['-F', '/dev/null', '-S', controlPath, ...args, target], { timeout: 3000, maxBuffer: 4096 });
    let ready = false;
    for (let i = 0; i < 60 && !exited; i++) {
      try { await control(['-O', 'check']); ready = true; break; }
      catch { await pause(200); }
    }
    if (!ready || exited) fail(failure);
    return { controlPath, exit, get exited() { return exited; }, get failure() { return failure; },
      execArgs: ['-F', '/dev/null', '-S', controlPath, '-T', target],
      async forward(direction, fromPort, toPort) {
        if (!['L', 'R'].includes(direction) || ![fromPort, toPort].every(p => Number.isInteger(p) && p >= 1 && p <= 65535)) fail('INVALID_SSH_CONFIG');
        try { await control(['-O', 'forward', `-${direction}`, `127.0.0.1:${fromPort}:127.0.0.1:${toPort}`]); }
        catch (error) { fail(sshFailure(error.stderr || '')); }
      },
      close() { if (!exited) child.kill('SIGTERM'); else cleanup(); }
    };
  } catch (error) { if (child && !exited) child.kill('SIGTERM'); else cleanup(); throw error; }
}
module.exports = { openSshMaster, sshFailure };

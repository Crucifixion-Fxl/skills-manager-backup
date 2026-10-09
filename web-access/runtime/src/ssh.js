'use strict';

const path = require('node:path');
const { spawn } = require('node:child_process');
const { openSshMaster } = require('./ssh-master');
const { AccessError: ContractError } = require('./gateway');

// This runs remotely over the same SSH connection that owns the reverse tunnel.
// The key goes through stdin, never argv, the terminal, or a public listener.
function remoteCredentialWorker(file) {
  const fs = require('node:fs'), path = require('node:path'), readline = require('node:readline');
  let created = false, inode;
  const cleanup = () => {
    if (created) {
      try { if (fs.lstatSync(file).ino === inode) fs.unlinkSync(file); } catch { /* no secret output */ }
    }
  };
  process.on('exit', cleanup);
  process.on('SIGTERM', () => process.exit(0));
  const lines = readline.createInterface({ input: process.stdin });
  lines.once('line', line => {
    try {
      const data = JSON.parse(line);
      if (!/^[a-f0-9]{64}$/.test(data.key) || typeof data.url !== 'string' || typeof data.origin !== 'string') throw new Error();
      const lifetime = Number(data.expiresAt) - Date.now();
      if (!Number.isFinite(lifetime) || lifetime <= 0 || lifetime > 8 * 60 * 60 * 1000) throw new Error();
      const endpoint = new URL(data.url);
      if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1' || endpoint.pathname !== '/' || endpoint.username || endpoint.password) throw new Error();
      // sshd can override -R's address. Inspect actual remote listeners before saving the key.
      const wanted = Number(endpoint.port).toString(16).toUpperCase().padStart(4, '0');
      let found = false;
      for (const [source, address] of [['/proc/net/tcp', '0100007F'], ['/proc/net/tcp6', '00000000000000000000000001000000']]) {
        if (!fs.existsSync(source)) continue;
        for (const row of fs.readFileSync(source, 'utf8').split('\n')) {
          const fields = row.trim().split(/\s+/), local = fields[1]?.split(':');
          if (fields[3] !== '0A' || local?.[1] !== wanted) continue;
          if (local[0] !== address) throw new Error(); found = true;
        }
      }
      if (!found) throw new Error();
      const directory = path.dirname(file);
      // Only create the final private directory, never silently chmod existing user directories.
      if (!fs.existsSync(directory)) fs.mkdirSync(directory, { mode: 0o700 });
      const stat = fs.lstatSync(directory);
      if (fs.realpathSync(directory) !== directory || !stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw new Error();
      const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
      created = true; inode = fs.fstatSync(fd).ino;
      try { fs.writeFileSync(fd, JSON.stringify(data)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      process.stdout.write('{"status":"TUNNEL_READY"}\n');
      setTimeout(() => process.exit(0), lifetime);
    } catch { process.stderr.write('remote credential setup failed\n'); process.exit(1); }
  });
  lines.on('close', () => process.exit(0));
}

const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
function sshSpec(args, localPort) {
  const target = args['ssh-target'];
  const file = args['remote-credentials'];
  const node = args['remote-node'] || 'node';
  const remotePort = Number(args['remote-port'] || 19826);
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,199}$/.test(target || '') || !path.posix.isAbsolute(file || '')
      || /[\x00-\x1f\x7f]/.test(file) || !/^(?:node|\/[a-zA-Z0-9_./-]+)$/.test(node)
      || !Number.isInteger(remotePort) || remotePort < 1024 || remotePort > 65535
      || !Number.isInteger(localPort) || localPort < 1 || localPort > 65535) {
    throw new ContractError('INVALID_SSH_CONFIG', 'SSH target alias, absolute private credential path, and unprivileged ports are required');
  }
  const script = `(${remoteCredentialWorker.toString()})(${JSON.stringify(file)})`;
  return { remotePort, argv: ['-T', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ControlPersist=no', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'GatewayPorts=no', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-R', `127.0.0.1:${remotePort}:127.0.0.1:${localPort}`, target,
    `${quote(node)} -e ${quote(script)}`] };
}

async function serveTunnel(args, bridge, writeOutput) {
  const spec = sshSpec(args, Number(new URL(bridge.url).port));
  let master;
  try { master = await openSshMaster({ target: args['ssh-target'] }); await master.forward('R', spec.remotePort, Number(new URL(bridge.url).port)); }
  catch (error) { master?.close(); if (master) await master.exit; await bridge.close(); throw error; }
  const child = spawn('ssh', [...master.execArgs, spec.argv.at(-1)], { stdio: ['pipe', 'pipe', 'pipe'] });
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  let stopping = false;
  const stop = () => { stopping = true; child.stdin.end(); child.kill('SIGTERM'); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  try {
    const ready = new Promise((resolve, reject) => {
      let received = '';
      const timer = setTimeout(() => reject(new ContractError('SSH_TUNNEL_FAILED', 'SSH tunnel did not become ready')), 20000);
      child.stdout.on('data', chunk => {
        received += chunk.toString();
        if (received.length > 4096) { clearTimeout(timer); reject(new ContractError('SSH_TUNNEL_FAILED', 'unexpected SSH response')); return; }
        if (received.includes('{"status":"TUNNEL_READY"}')) { clearTimeout(timer); resolve(); }
      });
      child.once('error', () => { clearTimeout(timer); reject(new ContractError('SSH_TUNNEL_FAILED', 'could not start SSH')); });
      child.once('exit', () => { clearTimeout(timer); reject(new ContractError('SSH_TUNNEL_FAILED', 'SSH setup failed; check host alias, key, remote Node, and private directory')); });
    });
    // Do not echo ssh stderr: it can contain local paths or untrusted server text.
    child.stderr.resume(); child.stdin.on('error', () => {});
    child.stdin.write(JSON.stringify({ url: `http://127.0.0.1:${spec.remotePort}`, key: bridge.key, origin: bridge.origin, expiresAt: bridge.expiresAt }) + '\n');
    await ready;
    writeOutput({ status: 'TUNNEL_READY', origin: bridge.origin, remotePort: spec.remotePort,
      writesEnabled: args['allow-writes'] === 'true',
      remoteSetup: { SAAS_AUTH_CREDENTIALS_FILE: args['remote-credentials'] }, expiresAt: bridge.expiresAt,
      lifetime: 'foreground process; remote credential file is removed on disconnect' }, {});
    const exit = await exited;
    if (!stopping && exit.code !== 0) throw new ContractError('SSH_TUNNEL_FAILED', 'SSH disconnected; in-flight writes need readback before retry');
  } finally {
    process.off('SIGINT', stop); process.off('SIGTERM', stop);
    if (child.exitCode === null) { child.stdin.end(); child.kill('SIGTERM'); }
    master.close(); await master.exit;
    await bridge.close();
  }
}

module.exports = { sshSpec, remoteCredentialWorker, serveTunnel };

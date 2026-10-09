"use strict";
const net = require('node:net');
const { fail } = require('./gateway');
const { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { validateCapability, launchBootstrap } = require('./viewer');
const execute = promisify(execFile);
const shellQuote = value => "'" + value.split("'").join("'\"'\"'") + "'";
async function readRemoteCapability(master, port, capabilityFile, execImpl = execute) {
  if (capabilityFile && (!capabilityFile.startsWith('/') || /[\r\n\0]/.test(capabilityFile))) fail('INVALID_VIEWER_CAPABILITY_FILE');
  const location = capabilityFile ? JSON.stringify(capabilityFile) : `os.path.join(os.path.expanduser('~'),'.cache','addx-web-view','viewer-${port}.json')`;
  const program = `import os,stat,json\np=${location}\nf=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)\ns=os.fstat(f)\nassert stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_mode&0o077==0 and s.st_size<=2048\nwith os.fdopen(f) as r: print(r.read())`;
  try {
    const result = await execImpl('ssh', [...master.execArgs, 'python3 -c ' + shellQuote(program)], { timeout: 5000, maxBuffer: 4096 });
    return validateCapability(JSON.parse(result.stdout));
  } catch { fail('VIEWER_CAPABILITY_REQUIRED'); }
}
const { openSshMaster, sshFailure } = require('./ssh-master');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function availablePort(preferred) {
  const server = net.createServer();
  try { await new Promise((resolve, reject) => { server.once('error', reject); server.listen(preferred, '127.0.0.1', resolve); }); }
  catch (error) { if (preferred && error.code === 'EADDRINUSE') return availablePort(0); throw error; }
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve)); return port;
}

async function viewerTunnel({ target, port = 19827, ttlMs = 15 * 60 * 1000, fetchImpl = global.fetch, spawnImpl, controlImpl, capabilityFile, readCapabilityImpl = readRemoteCapability, execImpl, openBrowser = true, browserSpawnImpl } = {}) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || !Number.isInteger(ttlMs) || ttlMs < 1000 || ttlMs > 8 * 60 * 60 * 1000) fail('INVALID_SSH_CONFIG');
  if (typeof fetchImpl !== 'function') fail('NODE_20_REQUIRED');
  const localPort = await availablePort(port);
  const master = await openSshMaster({ target, spawnImpl, controlImpl });
  let timer, stop, launcher;
  try {
    await master.forward('L', localPort, port);
    const capability = await readCapabilityImpl(master, port, capabilityFile, execImpl);
    validateCapability(capability);
    const url = `http://127.0.0.1:${localPort}/`;
    let ready = false;
    for (let i = 0; i < 30 && !master.exited; i++) {
      try {
        const response = await fetchImpl(url + 'bootstrap', { redirect: 'error', signal: AbortSignal.timeout(1000) });
        if (response.ok && (await response.text()).includes('<title>浏览器画面</title>')) { ready = true; break; }
      } catch {}
      await pause(200);
    }
    if (!ready || master.exited) fail(master.failure === 'SSH_TUNNEL_FAILED' ? 'VIEWER_NOT_REACHABLE' : master.failure);
    launcher = launchBootstrap(url, capability, { open: openBrowser, spawnImpl: browserSpawnImpl });
    await launcher.opened;
    stop = () => { launcher.close(); clearTimeout(timer); process.off('SIGINT', stop); process.off('SIGTERM', stop); master.close(); };
    timer = setTimeout(stop, ttlMs); timer.unref();
    process.once('SIGINT', stop); process.once('SIGTERM', stop);
    const exit = master.exit.finally(() => { launcher.close(); clearTimeout(timer); process.off('SIGINT', stop); process.off('SIGTERM', stop); });
    return { url, bootstrapFile: launcher.file, exit, controlPath: master.controlPath, close: stop };
  } catch (error) { launcher?.close(); master.close(); await master.exit; throw error; }
}
module.exports = { viewerTunnel, sshFailure, readRemoteCapability };

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const { createViewer } = require('../src/viewer');
const { createGateway } = require('../src/gateway');
const { openSshMaster } = require('../src/ssh-master');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const { viewerTunnel } = require('../src/viewer-tunnel');
async function listener(port = 0) {
  const server = net.createServer(socket => socket.destroy());
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  return server;
}
const shutdown = server => new Promise(resolve => server.close(resolve));
test('actual SSH ignores inherited conflicting forwards, chooses a free local port and removes its own listener',
  { skip: process.env.SAAS_SSH_FIXTURE !== '1', timeout: 45000 }, async t => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'saas-ssh-fixture-'));
    const name = `saas-view-fixture-${process.pid}`;
    let tunnel, master, gateway, viewer, browser;
    const conflicting = await listener();
    const occupied = await listener();
    const blockedPort = conflicting.address().port, remotePort = occupied.address().port;
    t.after(async () => {
      await browser?.close();
      if (viewer) await viewer.close();
      if (master) { master.close(); await master.exit; }
      if (gateway) await gateway.close();
      if (tunnel) { tunnel.close(); await tunnel.exit; }
      await shutdown(conflicting); await shutdown(occupied);
      await run('docker', ['rm', '-f', name]).catch(() => {});
      await fs.rm(root, { force: true, recursive: true });
    });
    await run('ssh-keygen', ['-q', '-t', 'ed25519', '-N', '', '-f', path.join(root, 'key')]);
    viewer = await createViewer({ port: 0, vncPort: blockedPort });
    await fs.writeFile(path.join(root, 'cap.json'), JSON.stringify(viewer.capability), { mode: 0o600 });
    await fs.writeFile(path.join(root, 'entry.sh'), `#!/bin/sh\nset -eu\npasswd -d root >/dev/null\ncp /mnt/key.pub /tmp/fixture-authorized\nchmod 600 /tmp/fixture-authorized\nssh-keygen -A\nmkdir -p /run/sshd\nprintf 'Port 22\\nListenAddress 0.0.0.0\\nAuthorizedKeysFile /tmp/fixture-authorized\\nStrictModes no\\nPermitRootLogin prohibit-password\\nPasswordAuthentication no\\nKbdInteractiveAuthentication no\\nAllowTcpForwarding yes\\n' > /tmp/sshd_config\n/usr/sbin/sshd -f /tmp/sshd_config\ncp /mnt/cap.json /tmp/viewer-cap.json\nchmod 600 /tmp/viewer-cap.json\nexec sleep 120\n`);
    await run('docker', ['run', '-d', '--name', name, '-p', '127.0.0.1::22', '-v', `${root}:/mnt:ro`, '--entrypoint', 'sh', 'hostd-186-ssh:20261006', '/mnt/entry.sh']);
    await run('docker', ['exec', name, 'python3', '-c', "import os,time\nfor i in range(50):\n if os.path.isfile('/tmp/viewer-cap.json'): break\n time.sleep(.1)\nelse: raise RuntimeError('fixture startup failed')"]);
    const { stdout } = await run('docker', ['port', name, '22/tcp']);
    const sshPort = Number(stdout.trim().split(':').at(-1));
    const config = path.join(root, 'config');
    await fs.writeFile(config, `Host fixture\n HostName 127.0.0.1\n Port ${sshPort}\n User root\n IdentityFile ${path.join(root, 'key')}\n IdentitiesOnly yes\n StrictHostKeyChecking no\n UserKnownHostsFile /dev/null\n LocalForward 127.0.0.1:${blockedPort} 127.0.0.1:${blockedPort}\n ControlMaster auto\n ControlPath ${root}/inherited-master\n`);
    await assert.rejects(run('ssh', ['-F', config, '-N', '-T', '-o', 'ExitOnForwardFailure=yes', '-o', 'ControlPath=none', 'fixture'], { timeout: 3000 }), e => /Address already in use|Could not request local forwarding/i.test(e.stderr));
    master = await openSshMaster({ target: 'fixture', spawnImpl: (cmd, args, options) => spawn(cmd, ['-F', config, ...args], options) });
    await master.forward('R', remotePort, Number(new URL(viewer.url).port));
    tunnel = await viewerTunnel({ target: 'fixture', port: remotePort, capabilityFile: '/tmp/viewer-cap.json', openBrowser: false,
      spawnImpl: (cmd, args, options) => spawn(cmd, ['-F', config, ...args], options) });
    assert.notEqual(new URL(tunnel.url).port, String(remotePort));
    assert.equal((await fetch(tunnel.url)).status, 403);
    const { chromium } = require('../../../../observability/tracking-lifecycle/node_modules/playwright-core');
    browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true, chromiumSandbox: true });
    const page = await browser.newPage();
    await page.goto('file://' + tunnel.bootstrapFile);
    await page.waitForURL(tunnel.url);
    assert.match(await page.title(), /浏览器画面/);
    assert.equal(await page.evaluate(() => document.cookie), '');
    assert.equal(await page.evaluate(() => location.hash), '');
    // The daily auth reverse tunnel uses the same isolated master and mux session.
    gateway = await createGateway({ origin: 'https://fixture.example', expectedSubject: '7', port: 0,
      adapter: { verify: async () => ({ subject: '7' }), prepare: () => async () => [] } });
    await master.forward('R', 33333, Number(new URL(gateway.url).port));
    const program = "import json,sys,urllib.request;d=json.load(sys.stdin);r=urllib.request.Request('http://127.0.0.1:33333/v1/ensure',data=json.dumps({'origin':d['origin']}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+d['key']});print(urllib.request.urlopen(r).read().decode())";
    const worker = spawn('ssh', [...master.execArgs, `python3 -c '${program.replaceAll("'", "'\\''")}'`], { stdio: ['pipe', 'pipe', 'pipe'] });
    // Quote Python without putting the private bridge key in argv.
    let reply = ''; worker.stdout.on('data', data => { reply += data.toString(); });
    const completed = new Promise(resolve => worker.once('exit', resolve));
    worker.stdin.end(JSON.stringify({ origin: gateway.origin, key: gateway.key }));
    assert.equal(await completed, 0);
    assert.equal(JSON.parse(reply).data.state, 'READY');
    assert.equal(reply.includes(gateway.key), false);
    master.close(); await master.exit;
    const privateSocket = tunnel.controlPath;
    assert.equal((await fs.stat(path.dirname(privateSocket))).mode & 0o777, 0o700);
    const bootstrapFile = tunnel.bootstrapFile;
    tunnel.close(); await tunnel.exit;
    await assert.rejects(fs.stat(bootstrapFile), { code: 'ENOENT' });
    await assert.rejects(fs.stat(privateSocket), { code: 'ENOENT' });
    await assert.rejects(fetch(tunnel.url));
    assert.equal(conflicting.listening, true); assert.equal(occupied.listening, true);
  });

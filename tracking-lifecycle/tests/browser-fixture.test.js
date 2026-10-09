'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('node:https');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright-core');
const { startBridge } = require('../cli/src/remote-bridge');

test('real isolated Chrome keeps HttpOnly session local and binds exact tab, identity and App',
  { skip: process.env.SAAS_BROWSER_FIXTURE !== '1', timeout: 45000 }, async t => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'saas-browser-test-'));
    let context, bridge, server;
    t.after(async () => {
      await bridge?.close(); await context?.close();
      if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
      fs.rmSync(folder, { recursive: true, force: true });
    });
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', folder + '/key.pem',
      '-out', folder + '/cert.pem', '-days', '1', '-subj', '/CN=127.0.0.1'], { stdio: 'ignore' });
    let subject = 7, cookieSeen = false;
    server = https.createServer({ key: fs.readFileSync(folder + '/key.pem'), cert: fs.readFileSync(folder + '/cert.pem') }, (req, res) => {
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/login') { res.setHeader('Set-Cookie', 'fixture_session=local-only; HttpOnly; Secure; SameSite=Strict; Path=/'); res.end('{}'); return; }
      cookieSeen = req.headers.cookie === 'fixture_session=local-only';
      if (!cookieSeen) { res.writeHead(401); res.end('{}'); return; }
      const data = req.url === '/api/user/getCurrentUser' ? { userId: subject }
        : req.url === '/api/info/getAllApplication' ? [{ id: 14 }] : { applicationId: 14, items: [] };
      res.end(JSON.stringify({ code: 200, data }));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'https://127.0.0.1:' + server.address().port;
    context = await chromium.launchPersistentContext(folder + '/chrome', { headless: true,
      executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', chromiumSandbox: true,
      ignoreHTTPSErrors: true, args: ['--remote-debugging-port=0'] });
    const page = context.pages()[0]; await page.goto(origin + '/login');
    assert.equal(await page.evaluate(() => document.cookie), '');
    const port = fs.readFileSync(folder + '/chrome/DevToolsActivePort', 'utf8').split('\n')[0];
    bridge = await startBridge({ origin, port: 0, session: 'isolated-fixture', expectedSubject: '7', applicationId: '14',
      env: { OPENCLI_CDP_ENDPOINT: 'http://127.0.0.1:' + port, OPENCLI_CDP_TARGET: origin + '/login' } });
    async function call(route, input = {}) {
      const response = await fetch(bridge.url + route, { method: 'POST', headers: {
        'Content-Type': 'application/json', Authorization: `Bearer ${bridge.key}` }, body: JSON.stringify({ origin, ...input }) });
      return response.json();
    }
    const ready = await call('/v1/ensure');
    assert.equal(ready.data.state, 'READY'); assert.equal(cookieSeen, true);
    assert.equal(JSON.stringify(ready).includes('local-only'), false);
    const request = { operation: 'context.list', input: { query: { applicationId: 14 } } };
    assert.equal((await call('/v1/request', request)).ok, true);
    subject = 8;
    assert.equal((await call('/v1/request', request)).code, 'AUTH_IDENTITY_MISMATCH');
    assert.equal((await call('/v1/disconnect')).data.state, 'REVOKED');
    assert.equal((await call('/v1/request', request)).code, 'REVOKED');
    await bridge.close();
    assert.equal(page.isClosed(), false);
  });

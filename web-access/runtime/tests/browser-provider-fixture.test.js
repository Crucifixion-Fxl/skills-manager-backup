'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { browserProvider } = require('../src/browser-provider');
test('real Chrome consumes HttpOnly session in the selected tab without exporting its JWT',
  { skip: process.env.SAAS_VIEWER_BROWSER_FIXTURE !== '1', timeout: 15000 }, async t => {
    const { chromium } = require('../../../../observability/tracking-lifecycle/node_modules/playwright-core');
    const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'], headless: true });
    t.after(() => browser.close());
    const context = await browser.newContext(), page = await context.newPage();
    const token = 'eyJmaXh0dXJl.e30.fixtureSignature'; let calls = 0, status = 200;
    await context.addCookies([{ name: 'payload-token', value: token, domain: 'fixture.example', path: '/', secure: true, httpOnly: true }]);
    await page.route('https://fixture.example/**', async route => {
      if (new URL(route.request().url()).pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html>fixture</html>' });
      calls++; assert.ok(route.request().headers().cookie.includes(token));
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ user: { email: 'fixture@example.invalid' }, token, diagnostic: 'echo ' + token }) });
    });
    await page.goto('https://fixture.example/');
    assert.equal(await page.evaluate(() => document.cookie), '');
    const cdp = await context.newCDPSession(page), outputs = [];
    const provider = browserProvider({ origin: 'https://fixture.example', page: { cdp: async (method, args) => { const value = await cdp.send(method, args); outputs.push(value); return value; }, close: () => cdp.detach() } });
    const response = await provider.request('GET', '/api/users/me');
    assert.equal(response.user.email, 'fixture@example.invalid');
    assert.equal(JSON.stringify(outputs).includes(token), false);
    const before = calls;
    await assert.rejects(provider.request('POST', '/api/users'), e => e.code === 'READ_ONLY_PROVIDER');
    await assert.rejects(provider.request('GET', '//foreign.example/'), e => e.code === 'INVALID_ENDPOINT');
    assert.equal(calls, before);
    status = 401; await assert.rejects(provider.request('GET', '/api/users/me'), e => e.code === 'AUTH_REQUIRED');
    await page.route('https://other.example/**', route => route.fulfill({ contentType: 'text/html', body: '<html>other</html>' }));
    await page.goto('https://other.example/');
    await assert.rejects(provider.request('GET', '/api/users/me'), e => e.code === 'BROWSER_ORIGIN_MISMATCH');
    await provider.close(); await assert.rejects(provider.request('GET', '/api/users/me'), e => e.code === 'REVOKED');
  });

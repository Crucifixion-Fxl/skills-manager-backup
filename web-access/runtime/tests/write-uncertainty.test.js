'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { browserProvider } = require('../src/browser-provider');
const { bearerProvider } = require('../src/providers');
const origin = 'https://fixture.example';
test('browser mutation requires explicitly non-retrying write backend before dispatch', async () => {
  for (const supportsWrites of [false, undefined]) {
    let calls = 0;
    const provider = browserProvider({ origin, allowWrites: true, page: { supportsWrites, cdp: async () => { calls++; } } });
    await assert.rejects(provider.request('PATCH', '/record', {}), { code: 'READ_ONLY_PROVIDER' });
    assert.equal(calls, 0);
  }
});
test('committed browser mutation with lost response remains unknown and is dispatched once', async () => {
  let commits = 0;
  const page = { supportsWrites: true, cdp: async (_, { expression }) => ({ result: { value: await vm.runInNewContext(expression, {
    location: { origin }, document: { cookie: '' }, AbortSignal, TextDecoder, Uint8Array,
    fetch: async () => { commits++; throw Error('response lost'); },
  }) } }) };
  const provider = browserProvider({ origin, allowWrites: true, page });
  await assert.rejects(provider.request('PATCH', '/record', {}), { code: 'RESULT_UNKNOWN' });
  assert.equal(commits, 1);
  page.cdp = async () => { throw Error('CDP disconnected'); };
  await assert.rejects(provider.request('PATCH', '/record', {}), { code: 'RESULT_UNKNOWN' });
});
test('native truncated mutation response remains unknown; declared POST read stays read', async () => {
  let calls = 0;
  const provider = bearerProvider({ origin, tokenEnv: 'TEST_TOKEN', env: { TEST_TOKEN: 'private-fixture' },
    fetchImpl: async () => { calls++; return new Response('{', { status: 200 }); } });
  await assert.rejects(provider.request('PATCH', '/record', {}), { code: 'RESULT_UNKNOWN' });
  assert.equal(calls, 1);
  await assert.rejects(provider.request('POST', '/identity', {}, { effect: 'read' }), { code: 'API_REQUEST_FAILED' });
});

test('write effect overrides a shared POST read endpoint before browser dispatch', async () => {
  let calls = 0;
  const provider = browserProvider({ origin, allowWrites: true, readPostPaths: ['/shared'],
    page: { supportsWrites: false, cdp: async () => { calls++; return { result: { value: { data: {} } } }; } } });
  await assert.rejects(provider.request('POST', '/shared', { action: 'create' }, { effect: 'write' }), { code: 'READ_ONLY_PROVIDER' });
  assert.equal(calls, 0);
  await provider.request('POST', '/shared', {}, { effect: 'read' });
  assert.equal(calls, 1);
});

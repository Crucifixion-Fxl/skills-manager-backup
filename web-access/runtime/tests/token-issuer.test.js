'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { issueToken, revokeToken } = require('../src/token-issuer');
const { readToken } = require('../src/token-custody');
const { credentialEnvironment } = require('../src/token-consumer');
const { runAdministrative } = require('../../../../observability/tracking-lifecycle/cli/src/platform-admin-cli');
const { profileRoot, readOwnerProfile } = require('./profile-fixtures');
const profile = readOwnerProfile('skills/observability/tracking-lifecycle/references/auth-profile.json');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'web-token-fixture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, 'token.json'), calls = [], rows = [];
  const secret = 'tmp_' + 'secretFixture'.repeat(3), expiresAt = Date.now() + 86400000;
  const state = { subject: '34', loseCreate: false, loseRevoke: false, mismatch: false };
  const page = { supportsWrites: true, cdp: async (_, { expression }) => ({ result: { value: await vm.runInNewContext(expression, {
    location: { origin: profile.origin }, AbortSignal,
    fetch: async (url, options) => {
      assert.equal(new URL(url).origin, profile.origin); calls.push([new URL(url).pathname, options.method]);
      const pathname = new URL(url).pathname;
      if (pathname === profile.identity.path) return Response.json({ success: true, data: { userId: state.subject } });
      if (options.method === 'GET') return Response.json({ success: true, data: state.mismatch ? [] : rows });
      if (pathname.endsWith('/delete')) {
        rows.splice(0); if (state.loseRevoke) throw Error('lost response');
        return Response.json({ success: true, data: true });
      }
      const body = JSON.parse(options.body);
      rows.push({ id: 7, status: 0, expiresAt, appId: body.appId });
      if (state.loseCreate) throw Error('committed then response lost');
      return Response.json({ success: true, data: { ...rows[0], token: secret } });
    },
  }) } }) };
  const args = { profile, issuer: 'project', input: { body: { name: 'acceptance', expiryDays: 30, appId: 123 } },
    expectedSubject: '34', secretFile: file, retention: 'once', page,
    fetchImpl: async (url, options) => {
      assert.equal(String(url), profile.origin + '/api/role/current');
      assert.equal(options.headers.Authorization, 'Bearer ' + secret);
      return Response.json({ success: true, data: { userId: state.subject } });
    } };
  return { args, file, page, state, rows, calls, secret };
}
test('platform token is issued into private custody, read back by id/scope, and revoked before local cleanup', async t => {
  const x = fixture(t), result = await issueToken(x.args);
  assert.equal(result.state, 'TOKEN_VERIFIED'); assert.equal(result.resource, 123);
  assert.equal(JSON.stringify(result).includes(x.secret), false);
  assert.equal(fs.statSync(x.file).mode & 0o777, 0o600);
  assert.equal(readToken(x.file).record.secret, x.secret);
  const revoked = await revokeToken(x.args); assert.equal(revoked.state, 'REVOKED');
  assert.equal(fs.existsSync(x.file), false); assert.equal(x.rows.length, 0);
});
test('unsafe storage, missing application binding, identity change and readonly browser reject before issuing', async t => {
  const x = fixture(t);
  await assert.rejects(issueToken({ ...x.args, input: { body: { name: 'a', expiryDays: 30 } } }), { code: 'INVALID_INPUT' });
  assert.equal(x.calls.length, 0);
  fs.writeFileSync(x.file, 'pre-existing');
  await assert.rejects(issueToken(x.args), { code: 'PRIVATE_STORAGE_UNAVAILABLE' }); assert.equal(x.calls.length, 0);
  fs.unlinkSync(x.file); x.page.supportsWrites = false;
  await assert.rejects(issueToken(x.args), { code: 'READ_ONLY_PROVIDER' }); assert.equal(x.calls.length, 0);
  x.page.supportsWrites = true; x.state.subject = 'foreign';
  await assert.rejects(issueToken(x.args), { code: 'IDENTITY_CHANGED' });
  assert.equal(x.calls.filter(x => x[1] === 'POST').length, 0);
});
test('lost issuance response is never replayed and unverified custody remains for reconciliation', async t => {
  const x = fixture(t); x.state.loseCreate = true;
  await assert.rejects(issueToken(x.args), { code: 'RESULT_UNKNOWN' });
  assert.equal(x.calls.filter(x => x[1] === 'POST').length, 1); assert.equal(fs.existsSync(x.file), false);
  x.state.loseCreate = false; x.state.mismatch = true;
  await assert.rejects(issueToken(x.args), { code: 'TOKEN_SAVED_UNVERIFIED' });
  assert.equal(readToken(x.file).record.state, 'UNVERIFIED');
  assert.equal(x.calls.filter(x => x[1] === 'POST').length, 2);
});
test('lost revoke response retains custody and subsequent reconciliation checks list before replay', async t => {
  const x = fixture(t); await issueToken(x.args); x.state.loseRevoke = true;
  await assert.rejects(revokeToken(x.args), { code: 'RESULT_UNKNOWN' });
  assert.equal(fs.existsSync(x.file), true);
  const count = x.calls.filter(x => x[0].endsWith('/delete')).length;
  await revokeToken(x.args);
  assert.equal(x.calls.filter(x => x[0].endsWith('/delete')).length, count); assert.equal(fs.existsSync(x.file), false);
});

test('native consumer uses verified private credential only for the same platform, subject and application', async t => {
  const x = fixture(t); await issueToken(x.args);
  const options = { profile, secretFile: x.file, expectedSubject: '34', applicationId: 123 };
  const credentials = credentialEnvironment(options);
  assert.equal(credentials.environment.TMT_TOKEN, x.secret);
  assert.throws(() => credentialEnvironment({ ...options, expectedSubject: 'foreign' }), { code: 'IDENTITY_CHANGED' });
  assert.throws(() => credentialEnvironment({ ...options, applicationId: 999 }), { code: 'RESOURCE_MISMATCH' });
  assert.throws(() => credentialEnvironment({ ...options, now: () => Date.now() + 86400001 }), { code: 'EXPIRED' });
  const raw = JSON.parse(fs.readFileSync(x.file)); raw.state = 'UNVERIFIED'; fs.writeFileSync(x.file, JSON.stringify(raw));
  assert.throws(() => credentialEnvironment(options), { code: 'TOKEN_UNVERIFIED' });
});

test('tracking native API consumes the new private file without exporting its credential', async t => {
  const x = fixture(t), skillsRoot = profileRoot(t, [profile]); await issueToken(x.args);
  const original = global.fetch; let calls = 0;
  global.fetch = async (url, options) => {
    if (new URL(url).pathname === '/api/role/current') return Response.json({ success: true, data: { userId: 34 } });
    calls++; assert.equal(new URL(url).pathname, '/api/baseSchema/list');
    assert.equal(new URL(url).searchParams.get('applicationId'), '123');
    assert.equal(options.method, 'GET'); assert.equal(options.headers.Authorization, 'Bearer ' + x.secret);
    return Response.json({ success: true, data: [{ id: 123, diagnostic: x.secret }] });
  };
  t.after(() => { global.fetch = original; });
  const requestFile = path.join(path.dirname(x.file), 'request.json'); fs.writeFileSync(requestFile, JSON.stringify({ query: { applicationId: 123 } }));
  const args = { operation: 'baseSchema.getAllBaseSchemas', request: requestFile,
    'token-file': x.file, 'expected-subject': '34', 'application-id': '123' };
  let output;
  await runAdministrative('api-call', args, { SAAS_SKILLS_ROOT: skillsRoot }, value => { output = value; });
  assert.equal(calls, 1); assert.equal(JSON.stringify(output).includes(x.secret), false);
  await assert.rejects(runAdministrative('api-call', { ...args, auth: 'pat' }, { SAAS_SKILLS_ROOT: skillsRoot }, () => {}), { code: 'TOKEN_SCOPE_MISMATCH' });
  await assert.rejects(runAdministrative('api-call', args, { SAAS_SKILLS_ROOT: skillsRoot, TRACKING_PLATFORM_BASE_URL: 'https://foreign.example' }, () => {}), { code: 'TOKEN_SCOPE_MISMATCH' });
  fs.writeFileSync(requestFile, JSON.stringify({ query: { applicationId: 999 } }));
  await assert.rejects(runAdministrative('api-call', args, { SAAS_SKILLS_ROOT: skillsRoot }, () => {}), { code: 'RESOURCE_MISMATCH' });
  fs.writeFileSync(requestFile, JSON.stringify({ body: { applicationId: 123, releaseId: 1 } }));
  await assert.rejects(runAdministrative('api-call', { ...args, operation: 'release.getReleaseEvents' }, { SAAS_SKILLS_ROOT: skillsRoot }, () => {}), { code: 'PROJECT_TOKEN_READ_ONLY' });
  assert.equal(calls, 1);
});

test('failed verified promotion keeps the readable unverified credential for recovery', async t => {
  const x = fixture(t), original = fs.writeFileSync;
  fs.writeFileSync = (file, data, ...rest) => {
    if (typeof file === 'number' && String(data).includes('"state":"VERIFIED"')) throw Object.assign(Error('disk full'), { code: 'ENOSPC' });
    return original(file, data, ...rest);
  };
  t.after(() => { fs.writeFileSync = original; });
  await assert.rejects(issueToken(x.args), { code: 'TOKEN_SAVED_UNVERIFIED' });
  assert.equal(readToken(x.file).record.state, 'UNVERIFIED');
  assert.equal(readToken(x.file).record.secret, x.secret);
  assert.equal(x.rows.length, 1);
});

test('first storage failure reports an issued record for recovery without claiming the credential was saved', async t => {
  const x = fixture(t), original = fs.writeFileSync;
  fs.writeFileSync = (file, data, ...rest) => {
    if (typeof file === 'number' && String(data).includes('"state":"UNVERIFIED"')) throw Object.assign(Error('disk full'), { code: 'ENOSPC' });
    return original(file, data, ...rest);
  };
  t.after(() => { fs.writeFileSync = original; });
  await assert.rejects(issueToken(x.args), error => {
    assert.equal(error.code, 'TOKEN_ISSUANCE_STORAGE_FAILED'); assert.equal(error.publicRecovery.recordId, '7');
    assert.equal(JSON.stringify(error.publicRecovery).includes(x.secret), false); return true;
  });
  assert.equal(fs.existsSync(x.file), false); assert.equal(x.rows.length, 1);
  assert.equal(x.calls.filter(x => x[1] === 'POST').length, 1);
});

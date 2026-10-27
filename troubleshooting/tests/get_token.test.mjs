import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.resolve(here, '../references/get_token.mjs');
const oldToken = 'synthetic.old.jwt';
const newToken = 'synthetic.new.jwt';
const identity = { status_code: 200, error_code: '', result: { data: { userid: 'synthetic-user' } } };
const login = () => [
  { body: { data: { prepare_token: 'synthetic-prepare' } } },
  { body: { data: { token: newToken } } },
  { body: identity },
];

function run({ cache = oldToken, responses = [], args = ['us'], envToken } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), 'troubleshooting-auth-test-'));
  const cachePath = path.join(dir, `.troubleshooting-token-${args[0]}`);
  const eventsPath = path.join(dir, 'events.json');
  const fixturePath = path.join(dir, 'fixture.json');
  try {
    if (cache !== null) writeFileSync(cachePath, cache);
    // Large synthetic response bodies must not exceed Linux execve's per-variable limit.
    writeFileSync(fixturePath, JSON.stringify({ responses }));
    const env = { PATH: process.env.PATH, AUTH_TEST_HOME: dir,
      AUTH_TEST_EVENTS: eventsPath, AUTH_TEST_FIXTURE_FILE: fixturePath };
    if (envToken !== undefined) env.TROUBLESHOOTING_TOKEN = envToken;
    const child = spawnSync(process.execPath, ['--import', path.join(here, 'auth-http-fixture.mjs'), script, ...args],
      { env, encoding: 'utf8', timeout: 5000 });
    assert.equal(child.error, undefined, 'helper must terminate without waiting for real OAuth');
    return { ...child, events: JSON.parse(readFileSync(eventsPath)),
      cache: existsSync(cachePath) ? readFileSync(cachePath, 'utf8') : null };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

for (const status of [403, 429, 500, 503, 302]) {
  test(`cached HTTP ${status} stops without OAuth or cache mutation`, () => {
    const r = run({ responses: [{ status }, ...login()] });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.equal(r.events.browsers, 0);
    assert.equal(r.events.requests.length, 1);
    assert.equal(r.cache, oldToken);
  });
}
for (const failure of [{ error: true }, { timeout: true }, { aborted: true }]) {
  test(`cache transport failure ${JSON.stringify(failure)} stops safely`, () => {
    const r = run({ responses: [failure, ...login()] });
    assert.equal(r.status, 1);
    assert.equal(r.events.browsers, 0);
    assert.equal(r.stdout, '');
    assert.ok(!r.stderr.includes('SYNTHETIC_SECRET'));
  });
}
for (const body of [
  { success: false, result: { data: { userid: 'synthetic-user' } } },
  { success: true, result: { data: {} } },
  { error_code: 'DENIED', result: { data: { userid: 'synthetic-user' } } },
  { ...identity, success: false },
  { ...identity, status_code: 403 },
  { result: { data: { userid: 'synthetic-user' } } },
]) {
  test(`HTTP 200 rejects invalid identity envelope ${JSON.stringify(body)}`, () => {
    const r = run({ responses: [{ body }] });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.equal(r.events.browsers, 0);
  });
}
for (const response of [{ raw: 'SYNTHETIC_SECRET invalid JSON' }, { raw: 'x'.repeat(300_000) }]) {
  test(`bad or oversized identity body (${response.raw.length} bytes) is not accepted`, () => {
    const r = run({ responses: [response] });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.ok(!r.stderr.includes('SYNTHETIC_SECRET'));
    assert.equal(r.events.browsers, 0);
  });
}
for (const body of [identity, { success: true, result: { data: { userid: 'synthetic-user' } } }]) {
  test(`valid identity envelope reuses cache (${Object.keys(body).join(',')})`, () => {
    const r = run({ responses: [{ body }] });
    assert.equal(r.status, 0);
    assert.equal(r.stdout, oldToken);
    assert.equal(r.events.browsers, 0);
    assert.equal(r.events.requests[0].authorization, 'Bearer synthetic.old.jwt');
  });
}
test('401 performs one OAuth exchange and validates the new credential before caching', () => {
  const r = run({ responses: [{ status: 401 }, ...login()] });
  assert.equal(r.status, 0);
  assert.equal(r.events.browsers, 1);
  assert.deepEqual(r.events.requests.map(x => x.method), ['GET', 'POST', 'POST', 'GET']);
  assert.equal(r.events.requests.at(-1).authorization, 'Bearer synthetic.new.jwt');
  assert.equal(r.stdout, newToken);
  assert.equal(r.cache, newToken);
});
test('force-login skips old cache and validates only the freshly acquired JWT', () => {
  const r = run({ args: ['staging-eu', '--force-login'], responses: login() });
  assert.equal(r.status, 0);
  assert.deepEqual(r.events.requests.map(x => x.method), ['POST', 'POST', 'GET']);
  assert.equal(r.events.requests.at(-1).url, 'https://troubleshooting-staging-eu.addx.live/api/current');
  assert.equal(r.stdout, newToken);
});
for (const status of [401, 403, 503]) {
  test(`new JWT rejected with ${status} is not output, cached or retried`, () => {
    const responses = login(); responses[2] = { status };
    const r = run({ cache: null, responses });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.equal(r.cache, null);
    assert.equal(r.events.browsers, 1);
    assert.equal(r.events.requests.length, 3);
  });
}
for (const response of [
  { raw: 'SYNTHETIC_SECRET invalid JSON' },
  { body: { error: 'SYNTHETIC_SECRET' } },
  { status: 503, body: { data: { prepare_token: 'SYNTHETIC_SECRET' } } },
  { error: true },
]) {
  test(`prepare failure is redacted (${response.status ?? (response.error ? 'network' : response.raw ? 'JSON' : 'shape')})`, () => {
    const r = run({ cache: null, responses: [response, ...login()] });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.ok(!r.stderr.includes('SYNTHETIC_SECRET'));
    assert.equal(r.events.requests.length, 1);
  });
}
test('confirm failure does not log response secrets', () => {
  const r = run({ cache: null, responses: [login()[0], { body: { error: 'SYNTHETIC_SECRET' } }] });
  assert.equal(r.status, 1);
  assert.ok(!r.stderr.includes('SYNTHETIC_SECRET'));
});
test('existing PAT cannot be replaced by force-login', () => {
  const r = run({ args: ['us', '--force-login'], envToken: 'tmt_SYNTHETIC_ONLY', responses: [{ body: identity }] });
  assert.equal(r.status, 1);
  assert.equal(r.events.requests.length, 0);
  assert.equal(r.events.browsers, 0);
  assert.equal(r.stdout, '');
});
for (const envToken of ['synthetic-unknown-credential', 'synthetic.injected.jwt', 'tmt_SYNTHETIC_ONLY', ' ']) {
  for (const force of [false, true]) {
    for (const cache of [null, oldToken]) {
      test(`injected credential is preserved (${envToken}, force=${force}, cache=${cache !== null})`, () => {
        const r = run({ envToken, cache, args: ['us', ...(force ? ['--force-login'] : [])],
          responses: cache && !force ? [{ body: identity }] : login() });
        assert.equal(r.status, 1);
        assert.equal(r.events.requests.length, 0);
        assert.equal(r.events.browsers, 0);
        assert.equal(r.stdout, '');
        assert.equal(r.cache, cache);
        assert.ok(!r.stderr.includes(envToken.trim()) || !envToken.trim());
      });
    }
  }
}
test('empty injected value still permits normal JWT login', () => {
  const r = run({ envToken: '', cache: null, responses: login() });
  assert.equal(r.status, 0);
  assert.equal(r.stdout, newToken);
  assert.equal(r.cache, newToken);
});
test('PAT accidentally placed in JWT cache is rejected without network', () => {
  const r = run({ cache: 'tmt_SYNTHETIC_ONLY', responses: [{ body: identity }] });
  assert.equal(r.status, 1);
  assert.equal(r.events.requests.length, 0);
  assert.equal(r.stdout, '');
});
test('unknown environment stops before network or OAuth', () => {
  const r = run({ args: ['untrusted'], responses: [{ body: identity }] });
  assert.equal(r.status, 1);
  assert.equal(r.events.requests.length, 0);
});

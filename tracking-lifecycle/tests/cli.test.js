const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const YAML = require('yaml');
const { digest } = require('../cli/src/contracts');

const cli = path.resolve(__dirname, '../cli/bin/events-tdd.js');
function fixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'events-tdd-'));
  const event = { type: 'PAGE', trackerType: 'BASE', spm: ['feed'], point: 'feed', parameters: [] };
  const baseline = { schemaVersion: 1, kind: 'baseline', host: 'vicohome', application: 'smart_camera',
    applicationId: 14, release: { id: 10, version: '1-0-6', status: 3 }, events: [event] };
  const change = { schemaVersion: 1, kind: 'change', issue: '101', host: 'vicohome', applicationId: 14,
    baseDigest: digest(baseline.events), operations: [{ event: { type: 'PAGE', trackerType: 'BASE', spm: ['feed'] },
      field: 'parameters.source', before: { exists: false }, after: { valueType: 'string', isRequired: false } }] };
  for (const [name, data] of Object.entries({ 'baseline.yaml': baseline, 'change.yaml': change })) {
    fs.writeFileSync(path.join(dir, name), YAML.stringify(data));
  }
  fs.writeFileSync(path.join(dir, 'usage.json'), JSON.stringify({ events: [{ ...event, issue: '101' }] }));
  return dir;
}

test('CLI builds selected issue candidate from YAML and usage manifest', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const out = path.join(dir, 'candidate.yaml');
  const result = spawnSync(process.execPath, [cli, 'build-candidate',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change=${path.join(dir, 'change.yaml')}`,
    `--usage=${path.join(dir, 'usage.json')}`, '--commit=abc', '--artifact-digest=sha256:artifact',
    '--release-id=11', '--version=1-0-7', `--out=${out}`], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const candidate = YAML.parse(fs.readFileSync(out, 'utf8'));
  assert.deepEqual(candidate.selectedIssues, ['101']);
  assert.equal(candidate.events[0].parameters[0].name, 'source');
});

test('CLI refuses publish outside protected CI before loading a token', () => {
  const result = spawnSync(process.execPath, [cli, 'publish'], { encoding: 'utf8', env: { PATH: process.env.PATH } });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PROTECTED_CI_REQUIRED/);
  const wrongRef = spawnSync(process.execPath, [cli, 'publish'], { encoding: 'utf8', env: {
    PATH: process.env.PATH, CI_COMMIT_REF_PROTECTED: 'true', CI_PIPELINE_SOURCE: 'push',
    CI_COMMIT_SHA: 'abc', CI_JOB_ID: '1', CI_COMMIT_BRANCH: 'main', TRACKING_RELEASE_REF: 'release/1-0-7',
  } });
  assert.match(wrongRef.stderr, /PROTECTED_CI_REQUIRED/);
});

test('CLI accepts an explicit selected change list for a combined release', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'selected.yaml'), YAML.stringify({ changes: ['change.yaml'] }));
  const result = spawnSync(process.execPath, [cli, 'check-contract',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change-list=${path.join(dir, 'selected.yaml')}`],
  { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).selectedIssues, ['101']);
});

test('CLI derives local report binding from projected contract and commit', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const baseline = YAML.parse(fs.readFileSync(path.join(dir, 'baseline.yaml'), 'utf8'));
  const event = { ...baseline.events[0], fields: { source: 'home' } };
  fs.writeFileSync(path.join(dir, 'scenario.yaml'), YAML.stringify({ schemaVersion: 1, kind: 'scenario',
    host: 'vicohome', applicationId: 14, expectations: [{ event: event, count: 1, fields: { source: 'home' } }] }));
  fs.writeFileSync(path.join(dir, 'capture.json'), JSON.stringify({ events: [event] }));
  const result = spawnSync(process.execPath, [cli, 'verify-local',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change=${path.join(dir, 'change.yaml')}`,
    `--scenario=${path.join(dir, 'scenario.yaml')}`, `--capture=${path.join(dir, 'capture.json')}`,
    '--commit=abc'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).binding.commit, 'abc');
});

test('CLI sandbox evidence must match the requested namespace and report binding', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const candidatePath = path.join(dir, 'candidate.yaml');
  const build = spawnSync(process.execPath, [cli, 'build-candidate',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change=${path.join(dir, 'change.yaml')}`,
    `--usage=${path.join(dir, 'usage.json')}`, '--commit=abc', '--artifact-digest=sha256:artifact',
    '--release-id=11', '--version=1-0-7', `--out=${candidatePath}`], { encoding: 'utf8' });
  assert.equal(build.status, 0, build.stderr);
  const candidate = YAML.parse(fs.readFileSync(candidatePath, 'utf8'));
  const lockPath = path.join(dir, 'lock.yaml');
  fs.writeFileSync(lockPath, YAML.stringify({ schemaVersion: 1, kind: 'platformLock',
    host: candidate.host, application: candidate.application, applicationId: candidate.applicationId,
    releaseId: candidate.releaseId, version: candidate.version, selectedIssues: candidate.selectedIssues,
    digest: digest(candidate.events), events: candidate.events }));
  const event = { type: 'PAGE', trackerType: 'BASE', spm: ['feed'] };
  const scenarioPath = path.join(dir, 'scenario.yaml');
  fs.writeFileSync(scenarioPath, YAML.stringify({ schemaVersion: 1, kind: 'scenario',
    host: candidate.host, applicationId: candidate.applicationId,
    expectations: [{ event, count: 1, fields: { source: 'feed' } }] }));
  const eventIdsPath = path.join(dir, 'event-ids.json');
  fs.writeFileSync(eventIdsPath, JSON.stringify(['evt-101']));
  const evidencePath = path.join(dir, 'evidence.json');
  const evidence = { namespace: 'ci-101-202-999', queryLimit: 50, truncated: false, bad: [],
    good: [{ ...event, eventId: 'evt-101', schemaVersion: '1-0-7',
      schemaUri: 'iglu:com.smart_camera/feed_pv/jsonschema/1-0-7', fields: { source: 'feed' } }] };
  fs.writeFileSync(evidencePath, JSON.stringify(evidence));
  const args = ['verify-sandbox', `--candidate=${candidatePath}`, `--lock=${lockPath}`,
    `--scenario=${scenarioPath}`, `--event-ids=${eventIdsPath}`, `--evidence=${evidencePath}`,
    '--namespace=ci-101-202-303', '--commit=abc'];
  const wrong = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8' });
  assert.notEqual(wrong.status, 0);
  assert.match(wrong.stderr, /NAMESPACE_MISMATCH/);
  fs.writeFileSync(evidencePath, JSON.stringify({ ...evidence, namespace: 'ci-101-202-303' }));
  const correct = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8' });
  assert.equal(correct.status, 0, correct.stderr);
  const report = JSON.parse(correct.stdout);
  assert.equal(report.namespace, 'ci-101-202-303');
  assert.equal(report.binding.namespace, report.namespace);
});

test('project hook runs without a shell and requires a declared output', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'events-hook-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const config = { schemaVersion: 1, hooks: { local: {
    argv: [process.execPath, '-e', 'require("fs").writeFileSync("capture.json", "{}")'],
    outputs: ['capture.json'],
  } } };
  fs.writeFileSync(path.join(dir, 'tracking.config.yaml'), YAML.stringify(config));
  const result = spawnSync(process.execPath, [cli, 'run-hook',
    `--config=${path.join(dir, 'tracking.config.yaml')}`, '--hook=local'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).status, 'PASS');
  config.hooks.local.outputs = ['missing.json'];
  fs.writeFileSync(path.join(dir, 'tracking.config.yaml'), YAML.stringify(config));
  const missing = spawnSync(process.execPath, [cli, 'run-hook',
    `--config=${path.join(dir, 'tracking.config.yaml')}`, '--hook=local'], { encoding: 'utf8' });
  assert.notEqual(missing.status, 0);
  assert.match(missing.stderr, /HOOK_OUTPUT_MISSING/);
});

test('candidate binds the hash of the actual artifact file', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'app.bin'), 'candidate-binary');
  const result = spawnSync(process.execPath, [cli, 'build-candidate',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change=${path.join(dir, 'change.yaml')}`,
    `--usage=${path.join(dir, 'usage.json')}`, '--commit=abc',
    `--artifact-file=${path.join(dir, 'app.bin')}`, '--release-id=11', '--version=1-0-7'],
  { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(YAML.parse(result.stdout).artifactDigest, /^sha256:[0-9a-f]{64}$/);
});

test('enabled M2 blocks publish before platform API when acceptance is absent', t => {
  const dir = fixture();
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'app.bin'), 'candidate-binary');
  const build = spawnSync(process.execPath, [cli, 'build-candidate',
    `--baseline=${path.join(dir, 'baseline.yaml')}`, `--change=${path.join(dir, 'change.yaml')}`,
    `--usage=${path.join(dir, 'usage.json')}`, '--commit=abc',
    `--artifact-file=${path.join(dir, 'app.bin')}`, '--release-id=11', '--version=1-0-7',
    `--out=${path.join(dir, 'candidate.yaml')}`], { encoding: 'utf8' });
  assert.equal(build.status, 0, build.stderr);
  const result = spawnSync(process.execPath, [cli, 'publish',
    `--candidate=${path.join(dir, 'candidate.yaml')}`,
    `--artifact-file=${path.join(dir, 'app.bin')}`], { encoding: 'utf8', env: {
    PATH: process.env.PATH, CI_COMMIT_REF_PROTECTED: 'true', CI_PIPELINE_SOURCE: 'push',
    CI_COMMIT_SHA: 'abc', CI_JOB_ID: '1', CI_PROJECT_ID: '101', CI_PIPELINE_ID: '202',
    CI_COMMIT_BRANCH: 'release/1-0-7',
    TRACKING_RELEASE_REF: 'release/1-0-7', TRACKING_M2_ENABLED: 'true',
  } });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /MISSING_ARG/);
  assert.doesNotMatch(result.stderr, /MISSING_TOKEN/);
});

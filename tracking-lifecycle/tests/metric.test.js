const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const YAML = require('yaml');
const { verifyMetric, verifyMetrics, validateAcceptance } = require('../cli/src/metric');
const { digest } = require('../cli/src/contracts');
const { validateStagingTarget } = require('../cli/src/staging-target');

const event = { type: 'PAGE', trackerType: 'BASE', spm: ['home'] };
const candidate = { host: 'demo', application: 'demo_app', applicationId: 1, selectedIssues: ['101'],
  commit: 'abc', artifactDigest: 'sha256:artifact', releaseId: 2, version: '1-0-1',
  events: [{ ...event, point: 'home', parameters: [] }] };
const contract = { schemaVersion: 1, kind: 'metric', issue: '101', metricId: 'paid_completion_rate',
  decisionQuestion: 'What share of paid users completed?',
  prd: { uri: 'repo:docs/prd.md', revision: 'sha256:prd' },
  observabilityDesign: { uri: 'repo:docs/observability.html', revision: 'sha256:design' },
  formula: { kind: 'ratio', numerator: { event }, denominator: { event },
    distinctBy: 'user_id', population: 'paid_users', window: '2026-10-02' },
  superset: { datasetId: 1321, chartId: 41, assetRevision: 'chart-v2' },
  expected: { value: 0.5, tolerance: 0.01 } };
const usTarget = { schemaVersion: 1, kind: 'stagingTarget', region: 'us', environment: 'staging',
  collectorEndpoint: 'https://us-test-log.theunismart.com', warehouseId: 'athena-us-stage',
  allowedSourceTables: ['analytics_staging.dwd_event_home_pv_hi'] };
const staging = { phase: 'staging', status: 'PASS', runId: 'run-101',
  region: 'us', warehouseId: 'athena-us-stage',
  targetDigest: validateStagingTarget(usTarget).targetDigest,
  binding: { host: 'demo', applicationId: 1, commit: 'abc', artifactDigest: 'sha256:artifact',
    contractDigest: digest(candidate.events), lockDigest: digest(candidate.events),
    releaseId: 2, version: '1-0-1' } };
const evidence = { runId: 'run-101', commit: 'abc', artifactDigest: 'sha256:artifact',
  region: 'us', warehouseId: 'athena-us-stage',
  envFilter: ['staging'],
  warehouse: { queryId: 'athena-1', sourceTables: ['analytics_staging.dwd_event_home_pv_hi'],
    numerator: 1, denominator: 2, value: 0.5 },
  superset: { queryId: 'chart-1', datasetId: 1321, chartId: 41, assetRevision: 'chart-v2',
    sourceTables: ['analytics_staging.dwd_event_home_pv_hi'], value: 0.5 } };

test('PRD metric acceptance requires independent staging SQL and Superset parity', () => {
  const result = verifyMetric(contract, evidence, staging, candidate, usTarget);
  assert.equal(result.status, 'PASS');
  assert.equal(result.metricId, 'paid_completion_rate');
  assert.equal(result.runId, 'run-101');
});

test('metric contract fails closed on missing business definition', () => {
  assert.throws(() => verifyMetric({ ...contract, formula: { ...contract.formula, distinctBy: '' } },
    evidence, staging, candidate, usTarget), e => e.code === 'CONTRACT_INCOMPLETE');
  assert.throws(() => verifyMetric({ ...contract, prd: { ...contract.prd, revision: '' } },
    evidence, staging, candidate, usTarget), e => e.code === 'CONTRACT_INCOMPLETE');
});

test('invalid env or a production SQL branch cannot pass staging acceptance', () => {
  assert.throws(() => verifyMetric(contract, { ...evidence, envFilter: ['staging', 'prod'] }, staging, candidate, usTarget),
    e => e.code === 'INVALID_ENV');
  assert.throws(() => verifyMetric(contract, { ...evidence, superset: { ...evidence.superset,
    sourceTables: ['analytics.dwd_event_home_pv_hi'] } }, staging, candidate, usTarget), e => e.code === 'INVALID_ENV');
});

test('metric parity accepts a regional staging target without US table names', () => {
  const target = { schemaVersion: 1, kind: 'stagingTarget', region: 'cn', environment: 'staging',
    collectorEndpoint: 'https://collector-cn-stage.example.test', warehouseId: 'athena-cn-stage',
    allowedSourceTables: ['tracking_stage.enriched', 'tracking_stage.bad_events'] };
  const regional = { ...evidence, region: 'cn', warehouseId: 'athena-cn-stage',
    warehouse: { ...evidence.warehouse, sourceTables: ['tracking_stage.enriched'] },
    superset: { ...evidence.superset, sourceTables: ['tracking_stage.enriched'] } };
  const stagingCn = { ...staging, region: 'cn', warehouseId: 'athena-cn-stage',
    sourceTables: ['tracking_stage.enriched', 'tracking_stage.bad_events'],
    targetDigest: validateStagingTarget(target).targetDigest };
  assert.equal(verifyMetric(contract, regional, stagingCn, candidate, target).status, 'PASS');
  assert.throws(() => verifyMetric(contract, { ...regional, region: 'us' }, stagingCn, candidate, target),
    e => e.code === 'WRONG_REGION');
  assert.throws(() => verifyMetric(contract, { ...regional,
    superset: { ...regional.superset, sourceTables: ['tracking.enriched'] } }, stagingCn, candidate, target),
  e => e.code === 'INVALID_ENV');
});

test('wrong denominator, chart value or stale staging binding fails', () => {
  assert.throws(() => verifyMetric(contract, { ...evidence, warehouse: { ...evidence.warehouse, denominator: 0 } },
    staging, candidate, usTarget), e => e.code === 'METRIC_DENOMINATOR');
  assert.throws(() => verifyMetric(contract, { ...evidence, superset: { ...evidence.superset, value: 0.4 } },
    staging, candidate, usTarget), e => e.code === 'METRIC_PARITY');
  assert.throws(() => verifyMetric(contract, { ...evidence, runId: 'old' }, staging, candidate, usTarget),
    e => e.code === 'STALE_REPORT');
});

test('combined metric report is bound to the exact selected metric contracts', () => {
  const report = verifyMetrics([contract], { metrics: [{ metricId: contract.metricId, ...evidence }] }, staging, candidate, usTarget);
  assert.equal(report.status, 'PASS');
  assert.equal(report.metrics.length, 1);
  assert.equal(validateAcceptance(report, [contract], candidate, usTarget).status, 'PASS');
  assert.throws(() => validateAcceptance({ ...report, metrics: [{ ...report.metrics[0], targetDigest: 'stale' }] },
    [contract], candidate, usTarget), e => e.code === 'STALE_REPORT');
  assert.throws(() => validateAcceptance(report, [{ ...contract, expected: { ...contract.expected, value: 0.7 } }], candidate, usTarget),
    e => e.code === 'STALE_REPORT');
  assert.throws(() => verifyMetrics([contract], { metrics: [] }, staging, candidate, usTarget),
    e => e.code === 'MISSING_METRIC_EVIDENCE');
});

test('CLI verifies a selected metric list and writes acceptance artifact', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-metric-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'metric.yaml'), YAML.stringify(contract));
  fs.writeFileSync(path.join(dir, 'selected-metrics.yaml'), YAML.stringify({ metrics: ['metric.yaml'] }));
  fs.writeFileSync(path.join(dir, 'candidate.yaml'), YAML.stringify(candidate));
  fs.writeFileSync(path.join(dir, 'staging-target.yaml'), YAML.stringify(usTarget));
  fs.writeFileSync(path.join(dir, 'staging.json'), JSON.stringify(staging));
  fs.writeFileSync(path.join(dir, 'evidence.json'), JSON.stringify({ metrics: [{ metricId: contract.metricId, ...evidence }] }));
  const cli = path.resolve(__dirname, '../cli/bin/events-tdd.js');
  const result = spawnSync(process.execPath, [cli, 'verify-metrics',
    `--candidate=${path.join(dir, 'candidate.yaml')}`,
    `--target=${path.join(dir, 'staging-target.yaml')}`,
    `--staging-report=${path.join(dir, 'staging.json')}`,
    `--metric-list=${path.join(dir, 'selected-metrics.yaml')}`,
    `--evidence=${path.join(dir, 'evidence.json')}`], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).status, 'PASS');
});

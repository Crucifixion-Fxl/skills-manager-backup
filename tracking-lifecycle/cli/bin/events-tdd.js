#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const YAML = require('yaml');
const { ContractError, parseContract, normalizeEvents, digest, project, planAgainstCurrent } = require('../src/contracts');
const { PlatformClient, createWorkorder, postChanges } = require('../src/platform');
const { IgluReader, verifyBaseline, publishCandidate, assertPublishedBoundary } = require('../src/release');
const { MicroClient } = require('../src/micro');
const { verifyLocal, verifySandbox, verifyStaging, verifyUsage, bindReport } = require('../src/verification');
const { runHook } = require('../src/hooks');
const { verifyMetrics, validateAcceptance } = require('../src/metric');
const { validateStagingTarget } = require('../src/staging-target');
const { runAdministrative } = require('../src/platform-admin-cli');
const { createRemoteTransport, serveBridge } = require('../src/remote-bridge');

function argsFrom(argv) {
  const args = { change: [], metric: [], report: [], 'prod-schema-base': [] };
  for (const arg of argv) {
    if (!arg.startsWith('--')) throw new ContractError('INVALID_ARG', `unexpected argument ${arg}`);
    const eq = arg.indexOf('=');
    if (eq < 3) throw new ContractError('INVALID_ARG', `expected --name=value: ${arg}`);
    const key = arg.slice(2, eq), value = arg.slice(eq + 1);
    if (['change', 'metric', 'report', 'prod-schema-base'].includes(key)) args[key].push(value);
    else args[key] = value;
  }
  return args;
}

function requireArg(args, key) {
  if (!args[key] || (Array.isArray(args[key]) && !args[key].length)) {
    throw new ContractError('MISSING_ARG', `--${key} is required`);
  }
  return args[key];
}

const readYaml = file => parseContract(fs.readFileSync(file, 'utf8'));
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));
function writeOutput(value, args, format = 'json') {
  const encoded = format === 'yaml' ? YAML.stringify(value) : JSON.stringify(value, null, 2) + '\n';
  if (args.out) {
    fs.mkdirSync(path.dirname(args.out), { recursive: true });
    fs.writeFileSync(args.out, encoded);
  } else process.stdout.write(encoded);
}

function changesFrom(args) {
  const files = [...args.change];
  if (args['change-list']) {
    const list = readYaml(args['change-list']);
    if (!Array.isArray(list.changes) || !list.changes.length || files.length) {
      throw new ContractError('INVALID_CHANGE_LIST', 'change-list must contain changes and cannot be combined with --change');
    }
    files.push(...list.changes.map(file => path.resolve(path.dirname(args['change-list']), file)));
  }
  if (!files.length) throw new ContractError('MISSING_ARG', '--change or --change-list is required');
  return files.map(readYaml);
}
function metricsFrom(args) {
  const files = [...args.metric];
  if (args['metric-list']) {
    const list = readYaml(args['metric-list']);
    if (!Array.isArray(list.metrics) || !list.metrics.length || files.length) {
      throw new ContractError('INVALID_METRIC_LIST', 'metric-list must contain metrics and cannot be combined with --metric');
    }
    files.push(...list.metrics.map(file => path.resolve(path.dirname(args['metric-list']), file)));
  }
  if (!files.length) throw new ContractError('MISSING_ARG', '--metric or --metric-list is required');
  return files.map(readYaml);
}
function artifactDigestFrom(args) {
  if (args['artifact-file']) {
    return `sha256:${crypto.createHash('sha256').update(fs.readFileSync(args['artifact-file'])).digest('hex')}`;
  }
  return requireArg(args, 'artifact-digest');
}
function reportBinding(phase, contract, args, env) {
  const binding = { host: contract.host, applicationId: contract.applicationId,
    commit: args.commit || env.CI_COMMIT_SHA, contractDigest: digest(contract.events) };
  if (!binding.commit) throw new ContractError('MISSING_ARG', '--commit or CI_COMMIT_SHA is required');
  if (phase !== 'local') {
    const lock = readYaml(requireArg(args, 'lock'));
    if (lock.kind !== 'platformLock' || lock.host !== contract.host ||
        String(lock.applicationId) !== String(contract.applicationId) ||
        lock.releaseId !== contract.releaseId || lock.version !== contract.version ||
        lock.digest !== digest(lock.events) || lock.digest !== binding.contractDigest ||
        JSON.stringify(lock.selectedIssues) !== JSON.stringify(contract.selectedIssues)) {
      throw new ContractError('PLATFORM_LOCK_MISMATCH', 'platform lock differs from candidate');
    }
    Object.assign(binding, { releaseId: contract.releaseId, version: contract.version,
      lockDigest: lock.digest });
    if (phase === 'staging') binding.artifactDigest = contract.artifactDigest;
  }
  return binding;
}
function readerFrom(args) { return new IgluReader({ baseUrls: requireArg(args, 'prod-schema-base') }); }
function platformFrom(env, browser) { return new PlatformClient({ baseUrl: env.TRACKING_PLATFORM_BASE_URL,
  token: env.TMT_TOKEN, request: browser ? browser.request.bind(browser) : undefined }); }

async function run(command, args, env = process.env) {
  if (command === 'bridge-serve') return serveBridge(args, env, writeOutput);
  if (command === 'bridge-tunnel') return require('../src/ssh-bridge').serveTunnel(args, env, writeOutput);
  if (command === 'site-coverage') return writeOutput(require('../src/website').coverage(), args);
  if (command === 'site-open') return writeOutput(await require('../src/website').openPage(args, env), args);
  if (command === 'opencli-install') return writeOutput(require('../src/opencli-install').install(args), args);
  if (args.auth === 'browser') throw new ContractError('AUTH_WORKFLOW_REQUIRED', 'start web-access Companion and use --auth=bridge for identity and App verification');
  let browser;
  if (args.auth === 'bridge' && !['api-catalog', 'api-plan'].includes(command)) {
    if (command === 'publish') throw new ContractError('PROTECTED_CI_REQUIRED', 'production publish cannot use a browser session');
    if (env.TMT_TOKEN || env.TMT_SESSION_COOKIE || env.TMT_MCP_API_KEY) {
      throw new ContractError('INVALID_AUTH', 'browser mode must not mix injected platform credentials');
    }
    browser = createRemoteTransport({ baseUrl: env.TRACKING_PLATFORM_BASE_URL, env });
  }
  try { return await runCommand(command, args, env, browser); }
  finally { if (browser) await browser.close(); }
}

async function runCommand(command, args, env, browser) {
  if (['api-catalog', 'api-plan', 'api-call'].includes(command)) {
    await runAdministrative(command, args, env, writeOutput, browser);
    return;
  }
  if (command === 'publish') {
    if (env.CI_COMMIT_REF_PROTECTED !== 'true' || env.CI_PIPELINE_SOURCE === 'merge_request_event' ||
        !env.CI_COMMIT_SHA || !env.CI_JOB_ID || !env.TRACKING_RELEASE_REF ||
        env.CI_COMMIT_BRANCH !== env.TRACKING_RELEASE_REF) {
      throw new ContractError('PROTECTED_CI_REQUIRED', 'publish only runs on the selected protected release branch');
    }
  }
  if (command === 'run-hook') {
    const configPath = requireArg(args, 'config');
    const result = await runHook(readYaml(configPath), requireArg(args, 'hook'),
      args.root || path.dirname(path.resolve(configPath)));
    writeOutput(result, args);
  } else if (command === 'check-contract') {
    const baseline = readYaml(requireArg(args, 'baseline'));
    const candidate = project(baseline, changesFrom(args));
    writeOutput({ status: 'PASS', baseDigest: candidate.baseDigest, candidateDigest: digest(candidate.events),
      selectedIssues: candidate.selectedIssues }, args);
  } else if (command === 'build-candidate') {
    const baseline = readYaml(requireArg(args, 'baseline'));
    const changes = changesFrom(args);
    const candidate = project(baseline, changes);
    delete candidate.release;
    Object.assign(candidate, { commit: requireArg(args, 'commit'), artifactDigest: artifactDigestFrom(args),
      releaseId: Number(requireArg(args, 'release-id')), version: requireArg(args, 'version') });
    verifyUsage(candidate, readJson(requireArg(args, 'usage')));
    writeOutput(candidate, args, 'yaml');
  } else if (command === 'verify-local') {
    const contract = project(readYaml(requireArg(args, 'baseline')), changesFrom(args));
    const result = verifyLocal(readYaml(requireArg(args, 'scenario')), readJson(requireArg(args, 'capture')), contract);
    writeOutput(bindReport('local', result, reportBinding('local', contract, args, env)), args);
  } else if (command === 'verify-sandbox') {
    const candidate = readYaml(requireArg(args, 'candidate'));
    const scenario = readYaml(requireArg(args, 'scenario'));
    const eventIds = readJson(requireArg(args, 'event-ids'));
    const namespace = requireArg(args, 'namespace');
    const evidence = args.evidence ? readJson(args.evidence) : await new MicroClient({
      baseUrl: env.TRACKING_SANDBOX_BASE_URL }).query({
      applicationId: candidate.applicationId, application: candidate.application,
      namespace, version: candidate.version,
      events: candidate.events, limit: Number(args.limit || 500),
    });
    const result = verifySandbox(scenario, evidence, { version: candidate.version,
      expectedEventIds: eventIds, contract: candidate, namespace });
    writeOutput(bindReport('sandbox', result,
      { ...reportBinding('sandbox', candidate, args, env), namespace }), args);
  } else if (command === 'verify-staging') {
    const candidate = readYaml(requireArg(args, 'candidate'));
    const result = verifyStaging(readYaml(requireArg(args, 'scenario')),
      readJson(requireArg(args, 'evidence')), { version: candidate.version, contract: candidate,
        target: readYaml(requireArg(args, 'target')) });
    writeOutput(bindReport('staging', result, reportBinding('staging', candidate, args, env)), args);
  } else if (command === 'verify-metrics') {
    const candidate = readYaml(requireArg(args, 'candidate'));
    const result = verifyMetrics(metricsFrom(args), readJson(requireArg(args, 'evidence')),
      readJson(requireArg(args, 'staging-report')), candidate, readYaml(requireArg(args, 'target')));
    writeOutput(result, args);
  } else if (command === 'pull-baseline') {
    const application = requireArg(args, 'application'), applicationId = Number(requireArg(args, 'application-id'));
    const client = platformFrom(env, browser);
    const releases = await client.listReleases(applicationId);
    if (!Array.isArray(releases) || releases.some(r => ![3, 4].includes(Number(r.releaseStatus)))) {
      throw new ContractError('BASELINE_NOT_RELEASED', 'no clean released boundary');
    }
    const latest = releases.filter(r => Number(r.releaseStatus) === 3).sort((a, b) => Number(b.id) - Number(a.id))[0];
    if (!latest) throw new ContractError('BASELINE_NOT_RELEASED', 'no released workorder');
    const current = await client.readTree({ application, applicationId });
    const baseline = { schemaVersion: 1, kind: 'baseline', host: requireArg(args, 'host'),
      application, applicationId, release: { id: latest.id, version: latest.version, status: 3 },
      events: normalizeEvents(current.events, application) };
    await verifyBaseline(client, readerFrom(args), baseline);
    writeOutput(baseline, args, 'yaml');
  } else if (command === 'pull-bootstrap') {
    const application = requireArg(args, 'application'), applicationId = Number(requireArg(args, 'application-id'));
    const client = platformFrom(env, browser);
    const releases = await client.listReleases(applicationId);
    if (!Array.isArray(releases) || releases.some(r => Number(r.releaseStatus) === 3)) {
      throw new ContractError('BASELINE_DRIFT', 'first release requires no published workorder');
    }
    const active = releases.filter(r => ![3, 4].includes(Number(r.releaseStatus)));
    if (active.length !== 1) throw new ContractError('WORKORDER_MISMATCH', 'first release requires one active workorder');
    const baseline = { schemaVersion: 1, kind: 'bootstrapBaseline', host: requireArg(args, 'host'),
      application, applicationId,
      release: { id: active[0].id, version: active[0].version, status: 0 }, events: [] };
    await verifyBaseline(client, null, baseline);
    writeOutput(baseline, args, 'yaml');
  } else if (command === 'verify-baseline') {
    const result = await verifyBaseline(platformFrom(env, browser), readerFrom(args), readYaml(requireArg(args, 'baseline')));
    writeOutput(result, args);
  } else if (command === 'create-workorder') {
    const baseline = readYaml(requireArg(args, 'baseline'));
    const options = { name: requireArg(args, 'name'), version: requireArg(args, 'version') };
    const result = browser?.createWorkorder ? await browser.createWorkorder(baseline, options) :
      await createWorkorder(platformFrom(env, browser), readerFrom(args), baseline, options);
    writeOutput(result, args);
  } else if (command === 'post') {
    const baseline = readYaml(requireArg(args, 'baseline'));
    const changes = changesFrom(args);
    const releaseId = Number(requireArg(args, 'release-id'));
    const previousLock = args['previous-lock'] ? readYaml(args['previous-lock']) : null;
    const result = browser?.postChanges ? await browser.postChanges(baseline, changes, releaseId, previousLock) :
      await postChanges(platformFrom(env, browser), baseline, changes, releaseId, previousLock);
    writeOutput({ schemaVersion: 1, kind: 'platformLock', host: baseline.host,
      application: baseline.application, applicationId: baseline.applicationId,
      releaseId: result.releaseId, version: result.version, baseDigest: digest(baseline.events),
      selectedIssues: changes.map(c => String(c.issue)), digest: result.digest,
      writes: result.writes, events: normalizeEvents(result.events, baseline.application) }, args, 'yaml');
  } else if (command === 'readback-platform') {
    const baseline = readYaml(requireArg(args, 'baseline'));
    const changes = changesFrom(args);
    const client = platformFrom(env, browser);
    const releaseId = Number(requireArg(args, 'release-id'));
    const version = requireArg(args, 'version');
    const releases = await client.listReleases(baseline.applicationId);
    assertPublishedBoundary(releases, baseline);
    const active = (Array.isArray(releases) ? releases : releases?.records || releases?.list || [])
      .filter(r => ![3, 4].includes(Number(r.releaseStatus)));
    if (active.length !== 1 || String(active[0].id) !== String(releaseId) ||
        active[0].version !== version ||
        (active[0].applicationId != null && String(active[0].applicationId) !== String(baseline.applicationId))) {
      throw new ContractError('WORKORDER_MISMATCH', 'platform lock requires the sole selected active workorder');
    }
    const current = await client.readTree(baseline);
    const plan = planAgainstCurrent(baseline, current, changes);
    if (!plan.complete) throw new ContractError('PLATFORM_READBACK_MISMATCH', 'platform tree is not selected projection');
    writeOutput({ schemaVersion: 1, kind: 'platformLock', host: baseline.host,
      application: baseline.application, applicationId: baseline.applicationId,
      releaseId, version,
      baseDigest: digest(baseline.events), selectedIssues: changes.map(c => String(c.issue)),
      digest: plan.targetDigest, events: normalizeEvents(current.events, baseline.application) }, args, 'yaml');
  } else if (command === 'publish') {
    const candidate = readYaml(requireArg(args, 'candidate'));
    const target = readYaml(requireArg(args, 'target'));
    const targetDigest = validateStagingTarget(target).targetDigest;
    if (!env.CI_PROJECT_ID || !env.CI_PIPELINE_ID) {
      throw new ContractError('PROTECTED_CI_REQUIRED', 'publish requires GitLab project and pipeline identity');
    }
    if (!args['artifact-file'] || candidate.commit !== env.CI_COMMIT_SHA ||
        candidate.artifactDigest !== artifactDigestFrom(args)) {
      throw new ContractError('CANDIDATE_DRIFT', 'CI commit or artifact digest differs from candidate');
    }
    if (env.TRACKING_M2_ENABLED === 'true') {
      validateAcceptance(readJson(requireArg(args, 'acceptance')), metricsFrom(args), candidate, target);
    }
    const result = await publishCandidate({ client: platformFrom(env, browser), schemaReader: readerFrom(args),
      baseline: readYaml(requireArg(args, 'baseline')), changes: changesFrom(args), candidate,
      usage: readJson(requireArg(args, 'usage')), reports: requireArg(args, 'report').map(readJson),
      sandboxNamespacePrefix: `ci-${env.CI_PROJECT_ID}-${env.CI_PIPELINE_ID}-`, targetDigest });
    writeOutput(bindReport('release', result, { host: candidate.host,
      applicationId: candidate.applicationId, commit: candidate.commit,
      artifactDigest: candidate.artifactDigest, contractDigest: digest(candidate.events),
      lockDigest: digest(candidate.events), releaseId: candidate.releaseId, version: candidate.version }), args);
  } else {
    throw new ContractError('UNKNOWN_COMMAND', `unknown command ${command || ''}`);
  }
}

async function main() {
  try { await run(process.argv[2], argsFrom(process.argv.slice(3))); }
  catch (error) {
    const code = error.code || 'UNEXPECTED_ERROR';
    process.stderr.write(JSON.stringify({ code, error: error.message }) + '\n');
    process.exitCode = 1;
  }
}

if (require.main === module) main();
module.exports = { run, argsFrom };

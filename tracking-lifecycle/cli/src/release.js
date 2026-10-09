'use strict';

const crypto = require('node:crypto');
const { ContractError, assertBaseline, digest, planAgainstCurrent } = require('./contracts');
const { verifyUsage } = require('./verification');

function schemaPath(application, event, version) {
  if (!/^[a-z][a-z0-9_]*$/.test(application) || !/^\d+-\d+-\d+$/.test(version)) {
    throw new ContractError('INVALID_SCHEMA_ID', 'application and release version required');
  }
  let name;
  if (event.type === 'PAGE' && event.trackerType === 'BASE' && event.spm.length === 1) {
    name = `${event.spm[0]}_pv`;
  } else if (['MODULE', 'COMPONENT'].includes(event.type) &&
      ['CLK', 'EXP'].includes(event.trackerType) && event.spm.length === (event.type === 'MODULE' ? 2 : 3)) {
    name = `${event.spm.join('_')}_${event.trackerType.toLowerCase()}`;
  } else if (event.type === 'SELF_DEFINE' && event.trackerType === 'BASE' && event.spm.length === 1) {
    name = event.point;
  } else {
    throw new ContractError('INVALID_SCHEMA_ID', 'event hierarchy or tracker type does not match platform schema path');
  }
  if (!/^[a-z][a-z0-9_]*$/.test(name)) throw new ContractError('INVALID_SCHEMA_ID', `invalid schema name ${name}`);
  return `com.${application}/${name}/jsonschema/${version}`;
}

class IgluReader {
  constructor({ baseUrls, fetchImpl = fetch } = {}) {
    this.baseUrls = baseUrls;
    this.fetch = fetchImpl;
  }

  async verify(application, version, events) {
    if (!Array.isArray(this.baseUrls) || !this.baseUrls.length ||
        this.baseUrls.some(url => !String(url).startsWith('https://'))) {
      throw new ContractError('BLOCKED_DEPENDENCY', 'read-only HTTPS prod Iglu schema URL is required');
    }
    const checked = [];
    for (const baseUrl of this.baseUrls) {
      for (const event of events) {
        const path = schemaPath(application, event, version);
        const response = await this.fetch(`${String(baseUrl).replace(/\/$/, '')}/${path}`, { signal: AbortSignal.timeout(30000) });
        if (!response.ok) throw new ContractError('PROD_SCHEMA_MISSING', `${path}: HTTP ${response.status}`);
        const schema = await response.json();
        const [vendor, name] = path.split('/');
        if (schema.self?.vendor !== vendor || schema.self?.name !== name || schema.self?.version !== version) {
          throw new ContractError('PROD_SCHEMA_MISMATCH', `${path}: self descriptor mismatch`);
        }
        if (schema.required !== undefined && !Array.isArray(schema.required)) {
          throw new ContractError('PROD_SCHEMA_MISMATCH', `${path}: malformed required fields`);
        }
        for (const param of event.parameters || []) {
          const type = schema.properties?.[param.name]?.type;
          if (type !== param.valueType) {
            throw new ContractError('PROD_SCHEMA_MISMATCH', `${path}: ${param.name} type mismatch`);
          }
          if (Boolean((schema.required || []).includes(param.name)) !== param.isRequired) {
            throw new ContractError('PROD_SCHEMA_MISMATCH', `${path}: ${param.name} required mismatch`);
          }
        }
        checked.push({ baseUrl, path, schema });
      }
    }
    const hash = crypto.createHash('sha256').update(JSON.stringify(checked.map(x => ({ baseUrl: x.baseUrl, path: x.path, schema: x.schema })))).digest('hex');
    return { verifiedPaths: checked.map(x => x.path), digest: hash };
  }
}

function releasesArray(value) {
  const rows = Array.isArray(value) ? value : value?.records || value?.list;
  if (!Array.isArray(rows)) throw new ContractError('PLATFORM_SHAPE', 'unrecognized release list');
  return rows;
}

function assertPublishedBoundary(releases, baseline) {
  assertBaseline(baseline);
  const rows = releasesArray(releases);
  if (baseline.kind === 'bootstrapBaseline') {
    if (rows.some(r => Number(r.releaseStatus) === 3)) {
      throw new ContractError('BASELINE_DRIFT', 'first-release boundary now has a published workorder');
    }
    const active = rows.filter(r => ![3, 4].includes(Number(r.releaseStatus)));
    if (active.length !== 1 || String(active[0].id) !== String(baseline.release.id) ||
        active[0].version !== baseline.release.version ||
        (active[0].applicationId != null && String(active[0].applicationId) !== String(baseline.applicationId))) {
      throw new ContractError('WORKORDER_MISMATCH', 'first-release boundary requires its sole selected active workorder');
    }
    return active[0];
  }
  const latest = rows.filter(r => Number(r.releaseStatus) === 3)
    .sort((a, b) => Number(b.id) - Number(a.id))[0];
  if (!latest) throw new ContractError('BASELINE_NOT_RELEASED', 'no released workorder');
  if (String(latest.id) !== String(baseline.release.id) ||
      latest.version !== baseline.release.version ||
      String(latest.applicationId || baseline.applicationId) !== String(baseline.applicationId)) {
    throw new ContractError('BASELINE_DRIFT', 'latest released workorder differs from baseline');
  }
  return latest;
}

async function verifyBaseline(client, schemaReader, baseline) {
  assertBaseline(baseline);
  const releases = releasesArray(await client.listReleases(baseline.applicationId));
  assertPublishedBoundary(releases, baseline);
  if (baseline.kind === 'bootstrapBaseline') {
    return { status: 'PASS', applicationId: baseline.applicationId, releaseId: baseline.release.id,
      digest: digest([]), prodSchemaDigest: null, boundary: 'FIRST_RELEASE_UNPUBLISHED' };
  }
  if (!schemaReader?.verify) throw new ContractError('BLOCKED_DEPENDENCY', 'prod schema reader is required');
  const released = releases.filter(r => Number(r.releaseStatus) === 3).sort((a, b) => Number(b.id) - Number(a.id));
  if (!released.length || String(released[0].id) !== String(baseline.release.id) ||
      released[0].version !== baseline.release.version ||
      releases.some(r => ![3, 4].includes(Number(r.releaseStatus)))) {
    throw new ContractError('BASELINE_NOT_RELEASED', 'baseline does not match latest clean released workorder');
  }
  const current = await client.readTree(baseline);
  if (digest(current.events) !== digest(baseline.events)) {
    throw new ContractError('BASELINE_DRIFT', 'current full tree differs from proposed released baseline');
  }
  const prod = await schemaReader.verify(baseline.application, baseline.release.version, baseline.events);
  return { status: 'PASS', applicationId: baseline.applicationId, releaseId: baseline.release.id,
    digest: digest(baseline.events), prodSchemaDigest: prod.digest };
}

function validateReports(candidate, reports, sandboxNamespacePrefix, targetDigest) {
  for (const phase of ['local', 'sandbox', 'staging']) {
    const report = reports.find(r => r.phase === phase);
    if (!report || report.status !== 'PASS') throw new ContractError('MISSING_REPORT', `${phase} PASS report required`);
    if (phase === 'staging' && targetDigest && report.targetDigest !== targetDigest) {
      throw new ContractError('STALE_REPORT', 'staging target differs from reviewed release target');
    }
    const expected = { host: candidate.host, applicationId: candidate.applicationId,
      commit: candidate.commit, contractDigest: digest(candidate.events) };
    if (phase !== 'local') Object.assign(expected, { lockDigest: digest(candidate.events),
      releaseId: candidate.releaseId, version: candidate.version });
    if (phase === 'staging') expected.artifactDigest = candidate.artifactDigest;
    for (const [key, value] of Object.entries(expected)) {
      if (String(report.binding?.[key]) !== String(value)) throw new ContractError('STALE_REPORT', `${phase}.${key} differs from candidate`);
    }
    if (phase === 'sandbox' && (typeof report.namespace !== 'string' ||
        !/^ci(?:-[a-z0-9]+){3,}$/.test(report.namespace) ||
        report.binding?.namespace !== report.namespace ||
        (sandboxNamespacePrefix && (!report.namespace.startsWith(sandboxNamespacePrefix) ||
          report.namespace.length <= sandboxNamespacePrefix.length)))) {
      throw new ContractError('STALE_REPORT', 'sandbox namespace differs from this CI run');
    }
  }
}

async function publishCandidate({ client, schemaReader, baseline, changes, candidate, reports, usage,
  sandboxNamespacePrefix, targetDigest }) {
  if (!client || !schemaReader?.verify) throw new ContractError('BLOCKED_DEPENDENCY', 'platform and prod schema reader required');
  if (!candidate?.commit || !candidate.artifactDigest || !candidate.releaseId || !candidate.version) {
    throw new ContractError('INVALID_CANDIDATE', 'commit, artifact digest, workorder ID and version required');
  }
  const releases = releasesArray(await client.listReleases(candidate.applicationId));
  assertPublishedBoundary(releases, baseline);
  const active = releases.filter(r => ![3, 4].includes(Number(r.releaseStatus)));
  if (active.length !== 1 || String(active[0].id) !== String(candidate.releaseId) ||
      active[0].version !== candidate.version ||
      (active[0].applicationId != null && String(active[0].applicationId) !== String(candidate.applicationId))) {
    throw new ContractError('WORKORDER_MISMATCH', 'candidate does not match sole active workorder');
  }
  const current = await client.readTree(candidate);
  const plan = planAgainstCurrent(baseline, current, changes);
  if (!plan.complete || digest(candidate.events) !== plan.targetDigest ||
      JSON.stringify(candidate.selectedIssues) !== JSON.stringify(changes.map(c => String(c.issue)))) {
    throw new ContractError('CANDIDATE_DRIFT', 'candidate, platform tree and selected issue projection differ');
  }
  validateReports(candidate, reports, sandboxNamespacePrefix, targetDigest);
  verifyUsage(candidate, usage);
  const unpassed = await client.getUnpassed(candidate.releaseId, candidate.applicationId);
  if (!Array.isArray(unpassed?.notUploaded) || !Array.isArray(unpassed?.unpassed) ||
      unpassed.notUploaded.length || unpassed.unpassed.length) {
    throw new ContractError('PLATFORM_VALIDATION', 'application-wide platform validation has missing or failed events');
  }
  const validation = await client.validate(candidate.releaseId, candidate.applicationId);
  if (validation?.passed !== true) throw new ContractError('PLATFORM_VALIDATION', 'platform validate did not pass');
  // Repeat the full tree check immediately before the existing API call. The platform has no CAS;
  // an unrelated UI writer can still race this call, and the post-release check must stay mandatory.
  const prePublish = await client.readTree(candidate);
  if (digest(prePublish.events) !== plan.targetDigest) throw new ContractError('CANDIDATE_DRIFT', 'platform tree changed before publish');
  let apiError;
  try { await client.onlineRelease(candidate.releaseId, candidate.applicationId); }
  catch (error) { apiError = error; }
  let after;
  try { after = releasesArray(await client.listReleases(candidate.applicationId)); }
  catch { throw new ContractError('RELEASE_UNKNOWN', 'publish outcome unknown; inspect workorder before retry'); }
  const selected = after.find(r => String(r.id) === String(candidate.releaseId));
  if (Number(selected?.releaseStatus) !== 3) {
    throw new ContractError(apiError ? 'RELEASE_UNKNOWN' : 'RELEASE_NOT_CONFIRMED', 'selected workorder is not RELEASED');
  }
  const readback = await client.readTree(candidate);
  if (digest(readback.events) !== plan.targetDigest) {
    throw new ContractError('RELEASE_READBACK_MISMATCH', 'released current tree differs from candidate');
  }
  const prod = await schemaReader.verify(candidate.application, candidate.version, candidate.events);
  return { status: 'PASS', releaseId: candidate.releaseId, version: candidate.version,
    candidateDigest: plan.targetDigest, artifactDigest: candidate.artifactDigest,
    prodSchemaDigest: prod.digest, apiErrorObserved: Boolean(apiError) };
}

module.exports = { schemaPath, IgluReader, verifyBaseline, publishCandidate, validateReports, assertPublishedBoundary };

'use strict';

const crypto = require('node:crypto');
const { ContractError, eventKey, digest } = require('./contracts');
const { validateStagingTarget } = require('./staging-target');

function present(value) { return typeof value === 'string' && value.trim().length > 0; }
function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

function verifyMetric(contract, evidence, staging, candidate, target) {
  const checkedTarget = validateStagingTarget(target);
  const formula = contract?.formula;
  if (contract?.schemaVersion !== 1 || contract.kind !== 'metric' || !present(contract.issue) ||
      !present(contract.metricId) || !present(contract.decisionQuestion) ||
      !present(contract.prd?.uri) || !present(contract.prd?.revision) ||
      !present(contract.observabilityDesign?.uri) || !present(contract.observabilityDesign?.revision) ||
      formula?.kind !== 'ratio' || !formula.numerator?.event || !formula.denominator?.event ||
      !present(formula.distinctBy) || !present(formula.population) || !present(formula.window) ||
      !Number.isInteger(contract.superset?.datasetId) || !Number.isInteger(contract.superset?.chartId) ||
      !present(contract.superset?.assetRevision) || !finite(contract.expected?.value) ||
      !finite(contract.expected?.tolerance) || contract.expected.tolerance < 0) {
    throw new ContractError('CONTRACT_INCOMPLETE', 'PRD, observability design, ratio grain and expected value are required');
  }
  if (!candidate?.selectedIssues?.map(String).includes(String(contract.issue)) ||
      !Array.isArray(candidate.events)) throw new ContractError('CONTRACT_INCOMPLETE', 'metric issue is not selected');
  const keys = new Set(candidate.events.map(event => eventKey(candidate.application, event)));
  for (const dependency of [formula.numerator.event, formula.denominator.event]) {
    if (!keys.has(eventKey(candidate.application, dependency))) {
      throw new ContractError('CONTRACT_INCOMPLETE', 'metric references an event outside candidate');
    }
  }
  const binding = { host: candidate.host, applicationId: candidate.applicationId,
    commit: candidate.commit, artifactDigest: candidate.artifactDigest,
    contractDigest: digest(candidate.events), lockDigest: digest(candidate.events),
    releaseId: candidate.releaseId, version: candidate.version };
  if (staging?.phase !== 'staging' || staging.status !== 'PASS' || !present(staging.runId) ||
      !Object.entries(binding).every(([key, value]) => String(staging.binding?.[key]) === String(value)) ||
      evidence?.runId !== staging.runId || evidence.commit !== candidate.commit ||
      evidence.artifactDigest !== candidate.artifactDigest ||
      staging.targetDigest !== checkedTarget.targetDigest) {
    throw new ContractError('STALE_REPORT', 'metric evidence does not belong to current staging candidate');
  }
  if (evidence.region !== checkedTarget.region || staging.region !== checkedTarget.region) {
    throw new ContractError('WRONG_REGION', 'metric region differs from reviewed staging target');
  }
  if (evidence.warehouseId !== checkedTarget.warehouseId || staging.warehouseId !== checkedTarget.warehouseId) {
    throw new ContractError('WRONG_WAREHOUSE', 'metric warehouse differs from reviewed staging target');
  }
  if (!same(evidence.envFilter, ['staging'])) {
    throw new ContractError('INVALID_ENV', 'Superset env filter must be exactly staging');
  }
  const warehouse = evidence.warehouse, superset = evidence.superset;
  if (!present(warehouse?.queryId) || !present(superset?.queryId) || warehouse.queryId === superset.queryId ||
      !Array.isArray(warehouse.sourceTables) || !warehouse.sourceTables.length ||
      !Array.isArray(superset.sourceTables) || !superset.sourceTables.length) {
    throw new ContractError('INVALID_EVIDENCE', 'independent warehouse and Superset queries required');
  }
  const allowed = new Set(checkedTarget.allowedSourceTables);
  for (const table of [...warehouse.sourceTables, ...superset.sourceTables]) {
    if (!allowed.has(table)) {
      throw new ContractError('INVALID_ENV', 'metric query used an unreviewed physical source table');
    }
  }
  if (!same([...warehouse.sourceTables].sort(), [...superset.sourceTables].sort())) {
    throw new ContractError('METRIC_SOURCE_MISMATCH', 'warehouse and Chart use different physical source tables');
  }
  if (superset.datasetId !== contract.superset.datasetId || superset.chartId !== contract.superset.chartId ||
      superset.assetRevision !== contract.superset.assetRevision) {
    throw new ContractError('SUPERSET_ASSET_DRIFT', 'Dataset, Chart or revision differs from metric contract');
  }
  if (!finite(warehouse.numerator) || !finite(warehouse.denominator) || warehouse.denominator <= 0) {
    throw new ContractError('METRIC_DENOMINATOR', 'ratio needs numeric numerator and positive denominator');
  }
  if (!finite(warehouse.value) || !finite(superset.value) ||
      Math.abs(warehouse.value - warehouse.numerator / warehouse.denominator) > 1e-9 ||
      Math.abs(warehouse.value - superset.value) > contract.expected.tolerance) {
    throw new ContractError('METRIC_PARITY', 'warehouse ratio and Superset Chart differ');
  }
  if (Math.abs(warehouse.value - contract.expected.value) > contract.expected.tolerance) {
    throw new ContractError('METRIC_EXPECTATION', 'observed ratio differs from PRD scenario expectation');
  }
  return { schemaVersion: 1, phase: 'metric', status: 'PASS', issue: String(contract.issue),
    metricId: contract.metricId, runId: staging.runId, warehouseQueryId: warehouse.queryId,
    supersetQueryId: superset.queryId, warehouseValue: warehouse.value,
    chartValue: superset.value, expectedValue: contract.expected.value,
    sourceTables: [...warehouse.sourceTables].sort(), region: checkedTarget.region,
    warehouseId: checkedTarget.warehouseId, targetDigest: checkedTarget.targetDigest, binding };
}

function metricContractsDigest(contracts) {
  if (!Array.isArray(contracts) || !contracts.length ||
      new Set(contracts.map(x => x.metricId)).size !== contracts.length) {
    throw new ContractError('CONTRACT_INCOMPLETE', 'nonempty unique metric contracts required');
  }
  const ordered = [...contracts].sort((a, b) => String(a.metricId).localeCompare(String(b.metricId)));
  return crypto.createHash('sha256').update(JSON.stringify(ordered)).digest('hex');
}

function verifyMetrics(contracts, evidence, staging, candidate, target) {
  const contractDigest = metricContractsDigest(contracts);
  if (!Array.isArray(evidence?.metrics) || evidence.metrics.length !== contracts.length ||
      new Set(evidence.metrics.map(x => x.metricId)).size !== evidence.metrics.length) {
    throw new ContractError('MISSING_METRIC_EVIDENCE', 'one independent evidence set per metric required');
  }
  const byId = new Map(evidence.metrics.map(item => [item.metricId, item]));
  const results = contracts.map(contract => {
    const item = byId.get(contract.metricId);
    if (!item) throw new ContractError('MISSING_METRIC_EVIDENCE', contract.metricId);
    return verifyMetric(contract, item, staging, candidate, target);
  });
  return { schemaVersion: 1, phase: 'metric', status: 'PASS',
    metricContractsDigest: contractDigest, targetDigest: results[0].targetDigest,
    metrics: results, binding: results[0].binding };
}

function validateAcceptance(report, contracts, candidate, target) {
  const expectedDigest = metricContractsDigest(contracts);
  const checkedTarget = validateStagingTarget(target);
  const binding = { host: candidate.host, applicationId: candidate.applicationId,
    commit: candidate.commit, artifactDigest: candidate.artifactDigest,
    contractDigest: digest(candidate.events), lockDigest: digest(candidate.events),
    releaseId: candidate.releaseId, version: candidate.version };
  if (report?.phase !== 'metric' || report.status !== 'PASS' ||
      report.metricContractsDigest !== expectedDigest || report.targetDigest !== checkedTarget.targetDigest ||
      !Array.isArray(report.metrics) ||
      report.metrics.length !== contracts.length ||
      !Object.entries(binding).every(([key, value]) => String(report.binding?.[key]) === String(value)) ||
      report.metrics.some(metric => metric.targetDigest !== checkedTarget.targetDigest ||
        metric.region !== checkedTarget.region || metric.warehouseId !== checkedTarget.warehouseId ||
        !Object.entries(binding).every(([key, value]) => String(metric.binding?.[key]) === String(value))) ||
      !same(report.metrics.map(x => x.metricId).sort(), contracts.map(x => x.metricId).sort())) {
    throw new ContractError('STALE_REPORT', 'metric acceptance does not match candidate and selected contracts');
  }
  return { status: 'PASS', metrics: report.metrics.length };
}

module.exports = { verifyMetric, verifyMetrics, validateAcceptance, metricContractsDigest };

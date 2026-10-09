'use strict';

const crypto = require('node:crypto');
const { ContractError } = require('./contracts');

const TABLE = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,2}$/;

function validateStagingTarget(target) {
  if (target?.schemaVersion !== 1 || target.kind !== 'stagingTarget' ||
      target.environment !== 'staging' || !/^[a-z][a-z0-9-]{0,31}$/.test(target.region || '') ||
      typeof target.warehouseId !== 'string' || !/^[A-Za-z0-9._:-]{2,128}$/.test(target.warehouseId) ||
      !Array.isArray(target.allowedSourceTables) || !target.allowedSourceTables.length ||
      new Set(target.allowedSourceTables).size !== target.allowedSourceTables.length ||
      target.allowedSourceTables.some(table => typeof table !== 'string' || !TABLE.test(table))) {
    throw new ContractError('INVALID_TARGET', 'reviewed staging region, warehouse and exact physical tables required');
  }
  let endpoint;
  try { endpoint = new URL(target.collectorEndpoint); }
  catch { throw new ContractError('INVALID_TARGET', 'staging collector URL required'); }
  if (endpoint.protocol !== 'https:' || !endpoint.hostname || endpoint.username || endpoint.password ||
      endpoint.search || endpoint.hash || endpoint.pathname !== '/') {
    throw new ContractError('INVALID_TARGET', 'staging collector must be an HTTPS origin without credentials or query');
  }
  const canonical = { schemaVersion: 1, kind: 'stagingTarget', environment: 'staging',
    region: target.region, collectorEndpoint: target.collectorEndpoint,
    warehouseId: target.warehouseId, allowedSourceTables: [...target.allowedSourceTables].sort() };
  if (Object.keys(target).some(key => !(key in canonical))) {
    throw new ContractError('INVALID_TARGET', 'unknown staging target field');
  }
  return { ...canonical, targetDigest: crypto.createHash('sha256')
    .update(JSON.stringify(canonical)).digest('hex') };
}

function verifyStagingSource(target, evidence) {
  const checked = validateStagingTarget(target);
  if (!Array.isArray(evidence?.sourceTables) || !evidence.sourceTables.length) {
    throw new ContractError('INVALID_EVIDENCE', 'staging physical source tables required');
  }
  if (evidence.region !== checked.region) throw new ContractError('WRONG_REGION', 'staging region differs from reviewed target');
  if (evidence.collectorEndpoint !== checked.collectorEndpoint) {
    throw new ContractError('WRONG_ENDPOINT', 'collector differs from reviewed staging target');
  }
  if (evidence.warehouseId !== checked.warehouseId) {
    throw new ContractError('WRONG_WAREHOUSE', 'warehouse differs from reviewed staging target');
  }
  const allowed = new Set(checked.allowedSourceTables);
  if (evidence.sourceTables.some(table => !allowed.has(table)) ||
      new Set(evidence.sourceTables).size !== evidence.sourceTables.length) {
    throw new ContractError('INVALID_ENV', 'query used an unreviewed physical source table');
  }
  return checked;
}

module.exports = { validateStagingTarget, verifyStagingSource };

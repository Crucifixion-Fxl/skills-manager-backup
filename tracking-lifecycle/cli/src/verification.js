'use strict';

const { ContractError, eventKey } = require('./contracts');
const { verifyStagingSource } = require('./staging-target');

function verifyEvents(scenario, rows) {
  if (scenario?.schemaVersion !== 1 || scenario.kind !== 'scenario' || !Array.isArray(scenario.expectations) ||
      !scenario.expectations.length || !scenario.expectations.some(x => Number.isInteger(x.count) && x.count > 0) ||
      scenario.expectations.some(x => !Number.isInteger(x.count) || x.count < 0) ||
      !Array.isArray(rows)) throw new ContractError('INVALID_SCENARIO', 'positive event expectation and event rows are required');
  const app = String(scenario.applicationId);
  const expectedKeys = new Set();
  for (const expectation of scenario.expectations) {
    const key = eventKey(app, expectation.event);
    expectedKeys.add(key);
    const matched = rows.filter(row => eventKey(app, row) === key);
    if (matched.length !== expectation.count) {
      throw new ContractError('EVENT_COUNT', `${key}: expected ${expectation.count}, got ${matched.length}`);
    }
    for (const row of matched) {
      for (const [field, value] of Object.entries(expectation.fields || {})) {
        if (row.fields?.[field] !== value) {
          throw new ContractError('FIELD_MISMATCH', `${key}: ${field} expected ${JSON.stringify(value)}`);
        }
      }
    }
  }
  for (const row of rows) {
    if (!expectedKeys.has(eventKey(app, row))) throw new ContractError('UNDECLARED_EVENT', 'unexpected event in this run');
  }
  return { status: 'PASS', eventCount: rows.length };
}

function verifyScenarioScope(scenario, contract) {
  if (!scenario?.host || !scenario?.applicationId || !contract?.host || !contract?.applicationId ||
      scenario.host !== contract.host || String(scenario.applicationId) !== String(contract.applicationId)) {
    throw new ContractError('SCENARIO_SCOPE', 'scenario host or application differs from projected contract');
  }
}

function verifyContractRows(contract, rows) {
  if (!contract?.application || !Array.isArray(contract.events)) {
    throw new ContractError('INVALID_CONTRACT', 'projected event contract required');
  }
  const byKey = new Map(contract.events.map(event => [eventKey(contract.application, event), event]));
  for (const row of rows) {
    const key = eventKey(contract.application, row);
    const event = byKey.get(key);
    if (!event) throw new ContractError('UNDECLARED_EVENT', `${key} not in projected contract`);
    const parameters = new Map(event.parameters.map(p => [p.name, p]));
    const fields = row.fields;
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) {
      throw new ContractError('FIELD_MISMATCH', `${key} has no payload fields`);
    }
    for (const parameter of event.parameters) {
      if (parameter.isRequired && !(parameter.name in fields)) {
        throw new ContractError('REQUIRED_FIELD', `${key}: ${parameter.name}`);
      }
    }
    for (const [name, value] of Object.entries(fields)) {
      const parameter = parameters.get(name);
      if (!parameter) throw new ContractError('UNDECLARED_FIELD', `${key}: ${name}`);
      const type = parameter.valueType;
      const valid = type === 'null' ? value === null : type === 'array' ? Array.isArray(value) : type === 'object'
        ? value !== null && typeof value === 'object' && !Array.isArray(value)
        : type === 'integer' ? Number.isInteger(value)
        : type === 'number' ? typeof value === 'number' && Number.isFinite(value)
        : typeof value === type;
      if (!valid) throw new ContractError('FIELD_TYPE', `${key}: ${name} must be ${type}`);
    }
  }
}

function verifySchemaRows(contract, version, rows) {
  const { schemaPath } = require('./release');
  const byKey = new Map(contract.events.map(event => [eventKey(contract.application, event), event]));
  for (const row of rows) {
    const event = byKey.get(eventKey(contract.application, row));
    if (!event || row.schemaUri !== `iglu:${schemaPath(contract.application, event, version)}`) {
      throw new ContractError('SCHEMA_URI', 'event schema URI differs from selected platform contract');
    }
  }
}

function verifyLocal(scenario, capture, contract) {
  verifyScenarioScope(scenario, contract);
  const result = verifyEvents(scenario, capture?.events);
  verifyContractRows(contract, capture.events);
  return result;
}

function verifySandbox(scenario, evidence, { version, expectedEventIds, contract, namespace }) {
  verifyScenarioScope(scenario, contract);
  if (!namespace || !evidence?.namespace || !Number.isInteger(evidence.queryLimit) || evidence.queryLimit < 1 ||
      !Array.isArray(evidence.good) || !Array.isArray(evidence.bad) || !Array.isArray(expectedEventIds)) {
    throw new ContractError('INVALID_EVIDENCE', 'sandbox namespace, query limit, good/bad and expected IDs required');
  }
  if (evidence.namespace !== namespace) {
    throw new ContractError('NAMESPACE_MISMATCH', 'sandbox evidence belongs to another namespace');
  }
  if (evidence.truncated || evidence.good.length >= evidence.queryLimit || evidence.bad.length >= evidence.queryLimit) {
    throw new ContractError('QUERY_TRUNCATED', 'sandbox query may have omitted events');
  }
  if (evidence.bad.length) throw new ContractError('BAD_EVENT', `${evidence.bad.length} sandbox bad events`);
  const actual = evidence.good.map(row => row.eventId).sort();
  if (new Set(actual).size !== actual.length || JSON.stringify(actual) !== JSON.stringify([...expectedEventIds].sort())) {
    throw new ContractError('EVENT_ID_MISMATCH', 'sandbox IDs differ from this run');
  }
  for (const row of evidence.good) {
    if (row.schemaVersion !== version) throw new ContractError('SCHEMA_VERSION', 'sandbox schema version differs from workorder');
  }
  const result = verifyEvents(scenario, evidence.good);
  verifyContractRows(contract, evidence.good);
  verifySchemaRows(contract, version, evidence.good);
  return { ...result, namespace: evidence.namespace, eventIds: actual };
}

function verifyStaging(scenario, evidence, { version, contract, target }) {
  verifyScenarioScope(scenario, contract);
  if (!evidence || !evidence.queryId || !evidence.runId || !evidence.sentAt || !evidence.now || !evidence.watermark ||
      !Array.isArray(evidence.rows) || !Array.isArray(evidence.badRows) ||
      !Array.isArray(evidence.sourceTables) || !evidence.sourceTables.length ||
      !evidence.queryWindow?.from || !evidence.queryWindow?.to) {
    throw new ContractError('INVALID_EVIDENCE', 'staging query ID, run ID, timestamps and rows required');
  }
  const checkedTarget = verifyStagingSource(target, evidence);
  if (evidence.runId !== scenario.runId) throw new ContractError('RUN_MISMATCH', 'staging evidence belongs to another run');
  for (const row of [...evidence.rows, ...evidence.badRows]) {
    if (row.runId !== evidence.runId) throw new ContractError('RUN_MISMATCH', 'unscoped warehouse row');
  }
  if (evidence.badRows.length) throw new ContractError('BAD_EVENT', `${evidence.badRows.length} staging bad events`);
  const sentAt = Date.parse(evidence.sentAt), now = Date.parse(evidence.now), watermark = Date.parse(evidence.watermark);
  const from = Date.parse(evidence.queryWindow.from), to = Date.parse(evidence.queryWindow.to);
  if (![sentAt, now, watermark].every(Number.isFinite) || !Number.isFinite(scenario.maxLagSeconds) || scenario.maxLagSeconds <= 0) {
    throw new ContractError('INVALID_EVIDENCE', 'invalid staging time or maxLagSeconds');
  }
  if (![from, to].every(Number.isFinite) || from > sentAt || to < sentAt || to > now || from > to) {
    throw new ContractError('INVALID_EVIDENCE', 'query window does not cover this run');
  }
  if (watermark < sentAt) {
    throw new ContractError(now - sentAt > scenario.maxLagSeconds * 1000 ? 'WAREHOUSE_TIMEOUT' : 'WAREHOUSE_DELAY',
      'warehouse watermark has not reached this run');
  }
  for (const row of evidence.rows) {
    if (row.schemaVersion !== version) throw new ContractError('SCHEMA_VERSION', 'staging schema version differs from workorder');
  }
  const result = verifyEvents(scenario, evidence.rows);
  verifyContractRows(contract, evidence.rows);
  verifySchemaRows(contract, version, evidence.rows);
  return { ...result, queryId: evidence.queryId, region: checkedTarget.region,
    warehouseId: checkedTarget.warehouseId, sourceTables: [...evidence.sourceTables].sort(),
    targetDigest: checkedTarget.targetDigest, watermark: evidence.watermark, runId: evidence.runId };
}

function verifyUsage(candidate, manifest) {
  if (!candidate?.application || !Array.isArray(candidate.events) || !Array.isArray(candidate.selectedIssues) ||
      !Array.isArray(manifest?.events) || !manifest.events.length) {
    throw new ContractError('INVALID_USAGE', 'candidate and nonempty usage manifest required');
  }
  const defined = new Set(candidate.events.map(event => eventKey(candidate.application, event)));
  const issues = new Set(candidate.selectedIssues.map(String));
  for (const event of manifest.events) {
    if (!event.issue || (event.issue !== 'baseline' && !issues.has(String(event.issue)))) {
      throw new ContractError('UNSELECTED_USAGE', 'usage has no selected issue owner');
    }
    if (!defined.has(eventKey(candidate.application, event))) {
      throw new ContractError('UNDECLARED_USAGE', 'candidate uses an undefined event');
    }
  }
  return { status: 'PASS', eventCount: manifest.events.length };
}

function bindReport(phase, result, binding) {
  const required = ['host', 'applicationId', 'commit', 'contractDigest'];
  if (phase !== 'local') required.push('releaseId', 'version', 'lockDigest');
  if (phase === 'sandbox') required.push('namespace');
  if (phase === 'staging' || phase === 'release') required.push('artifactDigest');
  const missing = required.filter(key => binding?.[key] === undefined || binding[key] === null || binding[key] === '');
  if (missing.length) throw new ContractError('MISSING_BINDING', `report missing ${missing.join(', ')}`);
  if (!['local', 'sandbox', 'staging', 'release'].includes(phase)) throw new ContractError('INVALID_PHASE', phase);
  if (phase === 'sandbox' && binding.namespace !== result.namespace) {
    throw new ContractError('NAMESPACE_MISMATCH', 'sandbox report binding differs from verified evidence');
  }
  return { schemaVersion: 1, phase, ...result, binding: Object.fromEntries(Object.entries(binding).sort(([a], [b]) => a.localeCompare(b))) };
}

module.exports = { verifyEvents, verifyContractRows, verifyLocal, verifySandbox, verifyStaging, verifyUsage, bindReport };

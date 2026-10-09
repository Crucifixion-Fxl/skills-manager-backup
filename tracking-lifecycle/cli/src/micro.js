'use strict';

const { ContractError } = require('./contracts');
const { schemaPath } = require('./release');
const { reviewedHttpsEndpoint } = require('./endpoint');

function readEncoded(parameters, plain, encoded) {
  try {
    if (parameters?.[plain]) return JSON.parse(parameters[plain]);
    if (parameters?.[encoded]) return JSON.parse(Buffer.from(parameters[encoded], 'base64').toString('utf8'));
  } catch (error) { throw new ContractError('MICRO_SHAPE', `${plain}/${encoded} is invalid JSON: ${error.message}`); }
  throw new ContractError('MICRO_SHAPE', `missing ${plain}/${encoded}`);
}

function normalizeMicroEvent(raw, application, version, events) {
  const parameters = raw?.rawEvent?.parameters;
  if (!parameters) throw new ContractError('MICRO_SHAPE', 'rawEvent.parameters missing');
  const payload = readEncoded(parameters, 'ue_pr', 'ue_px');
  const unstructured = payload.data;
  const uri = unstructured?.schema;
  if (typeof uri !== 'string') throw new ContractError('MICRO_SHAPE', 'unstructured schema URI missing');
  const matched = events.find(event => uri === `iglu:${schemaPath(application, event, version)}`);
  if (!matched) throw new ContractError('UNKNOWN_SCHEMA', `${uri} is not in the platform lock`);
  if (!unstructured.data || typeof unstructured.data !== 'object' || Array.isArray(unstructured.data)) {
    throw new ContractError('MICRO_SHAPE', 'unstructured event data missing');
  }
  return { type: matched.type, trackerType: matched.trackerType, spm: matched.spm,
    eventName: matched.eventName, eventId: parameters.eid || raw.id,
    schemaUri: uri, schemaVersion: version, fields: unstructured.data,
    collectorTime: raw.event?.dvce_created_tstamp || raw.rawEvent?.context?.timestamp };
}

async function requestMicro(baseUrl, path, body) {
  const response = await fetch(new URL(path, baseUrl), { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new ContractError('MICRO_HTTP', `${path}: HTTP ${response.status}`);
  const payload = await response.json();
  if (!Array.isArray(payload)) throw new ContractError('MICRO_SHAPE', `${path} did not return a list`);
  return payload;
}

class MicroClient {
  constructor({ baseUrl, request } = {}) {
    const endpoint = request ? null : reviewedHttpsEndpoint(baseUrl);
    this.request = request || ((path, body) => requestMicro(endpoint, path, body));
  }

  async query({ applicationId, application, namespace, version, events, limit = 500 }) {
    if (!/^ci(?:-[a-z0-9]+){3,}$/.test(namespace || '') || !applicationId || !application ||
        !version || !Array.isArray(events) || !Number.isInteger(limit) || limit < 1) {
      throw new ContractError('INVALID_NAMESPACE', 'unique ci-project-pipeline-job namespace and query inputs required');
    }
    // Micro filters Snowplow `aid` by the application point, not the manager's numeric ID.
    const body = { limit, app_id: application, namespace };
    const [goodRaw, badRaw] = await Promise.all([
      this.request('/micro/good', body), this.request('/micro/bad', body),
    ]);
    if (!Array.isArray(goodRaw) || !Array.isArray(badRaw)) {
      throw new ContractError('MICRO_SHAPE', 'good and bad responses must be lists');
    }
    return { namespace, queryLimit: limit, truncated: goodRaw.length >= limit || badRaw.length >= limit,
      good: goodRaw.map(row => normalizeMicroEvent(row, application, version, events)),
      bad: badRaw.map(row => ({ eventId: row?.rawEvent?.parameters?.eid || row?.id,
        errors: row?.errors || ['unknown bad event'] })) };
  }
}

module.exports = { MicroClient, normalizeMicroEvent, requestMicro };

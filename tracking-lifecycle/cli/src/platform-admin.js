'use strict';
const { BRIDGE_REFUSAL_CODES } = require('./bridge-errors');

const crypto = require('node:crypto');
const { ContractError } = require('./contracts');
const { reviewedHttpsEndpoint } = require('./endpoint');
const { sourceCommit, operations } = require('./platform-operations');

const catalog = new Map(operations.map(operation => [operation.id, operation]));
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const failInput = () => { throw new ContractError('INVALID_API_INPUT', 'request does not match the declared path/query/body binding'); };

function planOperation(id, input = {}) {
  const operation = catalog.get(id);
  if (!operation) throw new ContractError('UNKNOWN_OPERATION', 'operation is not in the source-bound catalog');
  if (!isObject(input) || Object.keys(input).some(key => !['path', 'query', 'body'].includes(key))) failInput();
  const path = input.path || {}, query = input.query || {};
  if (!isObject(path) || !isObject(query)
      || Object.keys(path).some(key => !operation.pathParameters.includes(key))
      || Object.keys(query).some(key => !operation.query.includes(key))) failInput();
  let requestPath = operation.path;
  for (const key of operation.pathParameters) {
    const value = String(path[key] ?? '');
    if (!/^[1-9][0-9]*(?![\s\S])/.test(value)) failInput();
    requestPath = requestPath.replace(`{${key}}`, value);
  }
  const parameters = new URLSearchParams();
  for (const key of Object.keys(query).sort()) {
    for (const value of Array.isArray(query[key]) ? query[key] : [query[key]]) {
      if (!['string', 'number', 'boolean'].includes(typeof value)
          || (typeof value === 'number' && !Number.isFinite(value))) failInput();
      parameters.append(key, String(value));
    }
  }
  if (parameters.size) requestPath += `?${parameters}`;
  if (operation.body ? !isObject(input.body) : input.body !== undefined) failInput();
  let body;
  try { body = operation.body ? JSON.parse(JSON.stringify(input.body)) : undefined; }
  catch { failInput(); }
  return { ...operation, requestPath, body };
}

function redact(value, secrets = [], depth = 0) {
  if (depth > 50) return '[REDACTED_DEPTH]';
  if (typeof value === 'string') {
    if (secrets.some(secret => secret && value.includes(secret))
        || /(?:tmt_|tmp_)[A-Za-z0-9_-]+/.test(value)
        || /[?&](?:code|state|token|access_token)=/i.test(value)) return '[REDACTED]';
    return value;
  }
  if (Array.isArray(value)) return value.map(item => redact(item, secrets, depth + 1));
  if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    /token|secret|password|cookie|authorization|^(?:code|state)$/i.test(key)
      ? '[REDACTED]' : redact(item, secrets, depth + 1)]));
  return value;
}

function publicPlan(plan) {
  // Never expose OAuth codes in the composed query string or request payload.
  return { operation: plan.id, method: plan.method, path: plan.path,
    pathParameters: plan.pathParameters, queryParameters: plan.query,
    body: redact(plan.body), auth: plan.auth, effect: plan.effect,
    workflow: plan.workflow, sourceCommit, status: 'PLAN_ONLY' };
}

class AdministrativeClient {
  constructor({ baseUrl, auth = 'project', token, cookie, apiKey, fetch: fetchImpl = global.fetch, secretSink, browserTransport } = {}) {
    this.origin = reviewedHttpsEndpoint(baseUrl);
    if (!['project', 'pat', 'session', 'mcp', 'browser', 'bridge'].includes(auth)) {
      throw new ContractError('INVALID_AUTH', 'supported modes: project, pat, session, mcp, browser, bridge; arbitrary JWT is not supported');
    }
    const provided = [token, cookie, apiKey].filter(value => value !== undefined && value !== '');
    if ((['browser', 'bridge'].includes(auth) ? provided.length !== 0 || !browserTransport || browserTransport.origin !== this.origin : provided.length !== 1) || provided.some(value => typeof value !== 'string'
        || value.length > 16384 || /[\x00-\x1f\x7f]/.test(value))) {
      throw new ContractError('INVALID_AUTH', 'exactly one valid credential for the selected auth mode is required');
    }
    if ((auth === 'project' && !/^tmp_[A-Za-z0-9_-]+(?![\s\S])/.test(token || ''))
        || (auth === 'pat' && !/^tmt_[A-Za-z0-9_-]+(?![\s\S])/.test(token || ''))
        || (auth === 'session' && (!cookie || token || apiKey || !cookie.includes('=')))
        || (auth === 'mcp' && (!apiKey || token || cookie))) {
      throw new ContractError('INVALID_AUTH', 'credential does not match selected auth mode');
    }
    this.auth = auth;
    // Credentials remain inside the request closure, not public client fields.
    this.send = ['browser', 'bridge'].includes(auth) ? plan => browserTransport.send(plan.method, plan.requestPath, plan.body) : async plan => fetchImpl(`${this.origin}${plan.requestPath}`, {
      method: plan.method,
      headers: { ...(auth === 'session' ? { Cookie: cookie } : auth === 'mcp'
        ? { 'X-MCP-API-Key': apiKey } : { Authorization: `Bearer ${token}` }),
      'Content-Type': 'application/json' },
      body: plan.body === undefined ? undefined : JSON.stringify(plan.body),
      redirect: 'manual', signal: AbortSignal.timeout(30000),
    });
    this.sanitize = value => redact(value, provided);
    this.secretSink = secretSink;
  }

  async call(id, input) {
    const plan = planOperation(id, input);
    if (plan.workflow) {
      throw new ContractError('TRACKING_WORKFLOW_REQUIRED', `use the existing ${plan.workflow} command with its contract gates`);
    }
    if (this.auth === 'project' && (plan.method !== 'GET' || plan.effect !== 'read')) {
      throw new ContractError('READ_ONLY_TOKEN', 'Project Token only supports GET operations without authentication side effects');
    }
    if (plan.auth === 'session' && !['session', 'browser', 'bridge'].includes(this.auth)) {
      throw new ContractError('SESSION_REQUIRED', 'this operation requires official interactive account login');
    }
    if ((plan.auth === 'mcp') !== (this.auth === 'mcp')) {
      throw new ContractError('INVALID_AUTH', 'MCP and platform credentials cannot be substituted');
    }
    if (plan.secretResponse && typeof this.secretSink !== 'function') {
      throw new ContractError('PRIVATE_OUTPUT_REQUIRED', 'operation requires an explicit private response destination before sending');
    }
    let response, payload;
    try {
      response = await this.send(plan);
      if (!response.ok || response.status >= 300) throw new Error('HTTP failure');
      payload = await response.json();
      if (payload?.code !== 200 || payload.success === false) throw new Error('business failure');
    } catch (error) {
      if (this.auth === 'bridge' && BRIDGE_REFUSAL_CODES.includes(error.code)) {
        throw new ContractError(error.code, error.code);
      }
      // A mutating endpoint may have partially committed. No retries and no
      // raw server error text, which can reflect credentials or private paths.
      throw new ContractError(plan.effect === 'read' ? 'API_REQUEST_FAILED' : 'API_RESULT_UNKNOWN',
        plan.effect === 'read' ? 'API request failed; no response evidence'
          : 'API result is unknown; inspect state before any repeat or publish');
    }
    if (plan.secretResponse) {
      try { await this.secretSink(payload.data); }
      catch { throw new ContractError('PRIVATE_OUTPUT_FAILED', 'API acknowledged, but private response could not be saved; do not repeat creation'); }
    }
    return { status: 'API_ACKNOWLEDGED', operation: id, sourceCommit,
      requestDigest: crypto.createHash('sha256').update(JSON.stringify({ id, input })).digest('hex'),
      readback: plan.effect === 'read' ? 'RESPONSE_ONLY' : 'PENDING',
      data: plan.secretResponse ? '[PRIVATE_RESPONSE_SAVED]' : this.sanitize(payload.data) };
  }
}

module.exports = { sourceCommit, operations, planOperation, publicPlan, redact, AdministrativeClient };

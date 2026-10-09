'use strict';

const { ContractError } = require('./contracts');
const { reviewedHttpsEndpoint } = require('./endpoint');
const { loopbackEndpoint, createBrowserTransport } = require('./browser-transport');
const { BRIDGE_REFUSAL_CODES } = require('./bridge-errors');
const { webAccessRuntimeModule } = require('./web-access-runtime');
function bridgeEndpoint(value) {
  let url;
  try { url = new URL(value); } catch { throw new ContractError('INVALID_BRIDGE_ENDPOINT', 'explicit bridge endpoint required'); }
  return url.protocol === 'http:' ? loopbackEndpoint(value) : reviewedHttpsEndpoint(value);
}

const { readCredentials, saveCredentials } = webAccessRuntimeModule('custody.js');

function createRemoteTransport({ baseUrl, env = process.env, fetch: fetchImpl = global.fetch } = {}) {
  const origin = reviewedHttpsEndpoint(baseUrl);
  const stored = env.TRACKING_BRIDGE_CREDENTIALS_FILE ? readCredentials(env.TRACKING_BRIDGE_CREDENTIALS_FILE) : {};
  if (stored.origin && stored.origin !== origin) throw new ContractError('BRIDGE_ORIGIN_MISMATCH', 'bridge credentials belong to another platform');
  const endpoint = bridgeEndpoint(env.TRACKING_BRIDGE_URL || stored.url);
  const key = env.TRACKING_BRIDGE_KEY || stored.key;
  if (!/^[a-f0-9]{64}$/.test(key || '')) throw new ContractError('INVALID_BRIDGE_CREDENTIALS', 'a valid private bridge key is required');
  async function call(route, input) {
    let response, payload;
    try {
      response = await fetchImpl(endpoint + route, { method: 'POST', redirect: 'manual',
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin, ...input }), signal: AbortSignal.timeout(route === '/v1/request' ? 35000 : 300000) });
      payload = await response.json();
    } catch {
      throw new ContractError('BRIDGE_RESULT_UNKNOWN', 'bridge response unavailable; inspect state before repeating any write');
    }
    if (!response.ok || !payload?.ok) {
      throw new ContractError(BRIDGE_REFUSAL_CODES.includes(payload?.code) ? payload.code : 'BRIDGE_REQUEST_FAILED',
        'bridge rejected the request or could not establish a reliable result; inspect local bridge and platform state');
    }
    return payload.data;
  }
  return {
    origin,
    async send(method, requestPath, body) {
      const { operationFor } = require('./browser-transport');
      const operation = operationFor(method, requestPath);
      const url = new URL(requestPath, origin);
      const query = {};
      for (const name of url.searchParams.keys()) {
        const values = url.searchParams.getAll(name); query[name] = values.length === 1 ? values[0] : values;
      }
      const paths = {};
      const pattern = new RegExp('^' + operation.path.replace(/\{\w+\}/g, '([1-9][0-9]*)') + '$');
      const matches = url.pathname.match(pattern);
      operation.pathParameters.forEach((name, index) => { paths[name] = matches[index + 1]; });
      const input = { path: paths, query, ...(body === undefined ? {} : { body }) };
      const data = await call('/v1/request', { operation: operation.id, input });
      return { ok: true, status: 200, json: async () => ({ code: 200, data }) };
    },
    async request(method, requestPath, body) {
      const response = await this.send(method, requestPath, body); return (await response.json()).data;
    },
    postChanges: (baseline, changes, releaseId, previousLock) => call('/v1/post', { baseline, changes, releaseId, previousLock }),
    createWorkorder: (baseline, options) => call('/v1/create-workorder', { baseline, options }),
    close: async () => {},
  };
}

async function startBridge({ origin: baseUrl, port = 19826, key,
  allowWrites = false, transport, session, profile, tab, env = process.env,
  expectedSubject = env.SAAS_AUTH_EXPECTED_SUBJECT,
  applicationId = env.TRACKING_APPLICATION_ID,
  contractHost = env.TRACKING_CONTRACT_HOST, ttlMs, now } = {}) {
  const { createGateway } = webAccessRuntimeModule('gateway.js');
  const { createTrackingAdapter } = require('./auth-adapter');
  const origin = reviewedHttpsEndpoint(baseUrl);
  const browser = transport || createBrowserTransport({ baseUrl: origin, session, profile, tab, env, workflow: true });
  try {
    return await createGateway({ origin, port, key, ttlMs, now, allowWrites, expectedSubject,
      scope: { applicationId: String(applicationId || '') },
      adapter: createTrackingAdapter({ transport: browser, applicationId, contractHost, env }) });
  } catch (error) { await browser.close(); throw error; }
}

async function serveBridge(args, env, writeOutput) {
  if (!args['private-output']) throw new ContractError('PRIVATE_OUTPUT_REQUIRED', 'bridge key needs --private-output in an owner-only directory');
  let bridge, cleanup;
  try {
    bridge = await startBridge({ origin: env.TRACKING_PLATFORM_BASE_URL, port: Number(args.port || 19826),
      allowWrites: args['allow-writes'] === 'true', session: args.session, profile: args.profile, tab: args.tab, env,
      expectedSubject: args['expected-subject'] || env.SAAS_AUTH_EXPECTED_SUBJECT,
      applicationId: args['application-id'] || env.TRACKING_APPLICATION_ID,
      ttlMs: args['ttl-seconds'] ? Number(args['ttl-seconds']) * 1000 : undefined });
    cleanup = saveCredentials(args['private-output'], { url: bridge.url, key: bridge.key, origin: bridge.origin, expiresAt: bridge.expiresAt });
    writeOutput({ status: 'BRIDGE_LISTENING', url: bridge.url, origin: bridge.origin,
      writesEnabled: args['allow-writes'] === 'true', credentialSaved: true,
      connection: 'local CLI or SSH reverse tunnel; keep the bridge in the foreground' }, {});
    await new Promise(resolve => {
      const stop = () => { clearTimeout(timer); process.off('SIGINT', stop); process.off('SIGTERM', stop); resolve(); };
      const timer = setTimeout(stop, Math.max(0, bridge.expiresAt - Date.now()));
      process.once('SIGINT', stop); process.once('SIGTERM', stop);
    });
  } finally { cleanup?.(); await bridge?.close(); }
}

module.exports = { bridgeEndpoint, readCredentials, createRemoteTransport, startBridge, serveBridge };

'use strict';

const { webAccessRuntimeModule } = require('./web-access-runtime');
const { connectBrowser, loopback } = webAccessRuntimeModule('browser.js');
const { ContractError } = require('./contracts');
const { reviewedHttpsEndpoint } = require('./endpoint');
const { operations } = require('./platform-operations');

// Executed in the target origin. No cookie export, arbitrary URL, or redirect.
async function browserFetch(origin, method, requestPath, body) {
  if (location.origin !== origin) return { ok: false, status: 0, originMismatch: true };
  try {
    const response = await fetch(origin + requestPath, {
      method, credentials: 'include', redirect: 'manual',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
    const payload = await response.json();
    return { ok: response.ok, status: response.status, payload };
  } catch { return { ok: false, status: 0 }; }
}

function operationFor(method, requestPath) {
  const url = new URL(requestPath, 'https://binding.invalid');
  if (url.origin !== 'https://binding.invalid' || !requestPath.startsWith('/api/') || url.hash) {
    throw new ContractError('UNKNOWN_OPERATION', 'browser transport only accepts catalog-bound platform paths');
  }
  const operation = operations.find(op => op.method === method &&
    new RegExp('^' + op.path.replace(/\{\w+\}/g, '[1-9][0-9]*') + '$').test(url.pathname));
  if (!operation || [...url.searchParams.keys()].some(key => !operation.query.includes(key))) {
    throw new ContractError('UNKNOWN_OPERATION', 'browser request is not in the source-bound catalog');
  }
  return operation;
}

const loopbackEndpoint = loopback;

function createBrowserTransport({ baseUrl, session, profile, env = process.env, page: providedPage,
  workflow = false, tab } = {}) {
  const origin = reviewedHttpsEndpoint(baseUrl);
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(session || '')) {
    throw new ContractError('BROWSER_SESSION_REQUIRED', '--session must name an explicit isolated browser session');
  }
  let page = providedPage, connecting;
  async function ready() {
    if (page) return page;
    if (!connecting) connecting = (async () => {
      page = await connectBrowser({ origin, session, profile, tab: tab || env.OPENCLI_TAB, env });
      return page;
    })();
    try { return await connecting; }
    catch (error) {
      if (error.code) throw new ContractError(error.code, error.code);
      throw new ContractError('BROWSER_UNAVAILABLE', 'browser connection failed; check OpenCLI doctor and selected profile/tab');
    }
  }
  return {
    origin,
    async send(method, requestPath, body) {
      const operation = operationFor(method, requestPath);
      if (operation.auth === 'mcp') throw new ContractError('INVALID_AUTH', 'MCP operations require their separate API key');
      if (operation.effect === 'authentication') {
        throw new ContractError('LOGIN_WORKFLOW_REQUIRED', 'use official login/remote login or explicit logout workflow');
      }
      if (operation.workflow === 'publish') throw new ContractError('PROTECTED_CI_REQUIRED', 'browser sessions cannot publish production releases');
      if (operation.workflow && !workflow) {
        throw new ContractError('TRACKING_WORKFLOW_REQUIRED', `use ${operation.workflow} with its contract gates`);
      }
      const target = await ready();
      if (target.supportsWrites === false && operation.effect !== 'read') throw new ContractError('READ_ONLY_BROWSER', 'use direct CDP for single-attempt writes');
      let result;
      try {
        // Use one CDP evaluation, avoiding evaluate()'s navigation retry for writes.
        const expression = `(${browserFetch.toString()})(${JSON.stringify(origin)},${JSON.stringify(method)},${JSON.stringify(requestPath)},${body === undefined ? 'undefined' : JSON.stringify(body)})`;
        const response = await target.cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
        if (response.exceptionDetails) throw new Error('evaluation failed');
        result = response.result?.value;
      } catch {
        throw new ContractError(operation.effect === 'read' ? 'API_REQUEST_FAILED' : 'API_RESULT_UNKNOWN',
          'browser request has no reliable response; read state before repeating a write');
      }
      if (result?.originMismatch) throw new ContractError('BROWSER_ORIGIN_MISMATCH', 'selected tab is not at the reviewed platform origin');
      return { ok: result?.ok === true, status: result?.status || 0, json: async () => result?.payload };
    },
    async request(method, requestPath, body) {
      const response = await this.send(method, requestPath, body);
      const payload = await response.json();
      const authCode = response.status === 401 || Number(payload?.code) === 401 ? 'AUTH_REQUIRED'
        : response.status === 403 || Number(payload?.code) === 403 ? 'API_PERMISSION_DENIED'
        : response.status >= 500 ? 'AUTH_DEPENDENCY_FAILED' : null;
      if (authCode) throw new ContractError(authCode, 'platform authentication or permission check failed');
      if (!response.ok || payload?.code !== 200 || payload?.success === false) {
        const operation = operationFor(method, requestPath);
        throw new ContractError(operation.effect === 'read' ? 'API_REQUEST_FAILED' : 'API_RESULT_UNKNOWN',
          'platform did not acknowledge the browser request; check login and read back state');
      }
      return payload.data;
    },
    async close() {
      // Only disconnect; never close a user-owned CDP tab or shared daemon.
      if (page) await page.close?.().catch(() => {});
    },
  };
}

module.exports = { browserFetch, operationFor, loopbackEndpoint, createBrowserTransport };

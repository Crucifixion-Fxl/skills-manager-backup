'use strict';
const { AccessError, fail, httpsOrigin, redact } = require('./gateway');

async function responseJSON(response) {
  if ([401, 403].includes(response.status)) fail(response.status === 401 ? 'AUTH_REQUIRED' : 'API_PERMISSION_DENIED');
  if (response.status === 404) fail('RESOURCE_NOT_FOUND');
  if (response.status >= 500) fail('AUTH_DEPENDENCY_FAILED');
  if (!response.ok || response.status >= 300) fail('API_REQUEST_FAILED');
  const bytes = []; let size = 0;
  for await (const chunk of response.body) { size += chunk.length; if (size > 16 * 1024 * 1024) fail('OUTPUT_LIMIT'); bytes.push(chunk); }
  try { return JSON.parse(Buffer.concat(bytes).toString()); } catch { fail('API_REQUEST_FAILED'); }
}
function pathURL(origin, path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || /[\\\x00-\x20]/.test(path)) fail('INVALID_ENDPOINT');
  const url = new URL(path, origin);
  if (url.origin !== origin || url.hash) fail('INVALID_ENDPOINT');
  return url.href;
}
function bearerProvider({ origin: value, tokenEnv, env = process.env, fetchImpl = global.fetch, getToken, scheme = 'bearer' } = {}) {
  const origin = httpsOrigin(value);
  if (!['bearer', 'raw'].includes(scheme)) fail('INVALID_PROVIDER');
  if (!getToken && !/^[A-Z][A-Z0-9_]+$/.test(tokenEnv || '')) fail('INVALID_PROVIDER');
  return {
    async request(method, path, body, { effect } = {}) {
      const token = getToken ? await getToken() : env[tokenEnv];
      if (typeof token !== 'string' || !token || /[\r\n]/.test(token)) fail('AUTH_REQUIRED');
      const mutating = effect ? effect === 'write' : method !== 'GET';
      const endpoint = pathURL(origin, path);
      let response;
      try { response = await fetchImpl(endpoint, { method, redirect: 'manual', signal: AbortSignal.timeout(30000),
        headers: { Authorization: scheme === 'raw' ? token : `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); }
      catch { fail(mutating ? 'RESULT_UNKNOWN' : 'API_REQUEST_FAILED'); }
      try { return redact(await responseJSON(response), [token]); }
      catch (error) {
        if (mutating && !['AUTH_REQUIRED', 'API_PERMISSION_DENIED', 'RESOURCE_NOT_FOUND'].includes(error.code)) fail('RESULT_UNKNOWN');
        throw error;
      }
    },
    close: async () => {},
  };
}

// No persistent password or refresh cache: existing credential providers retain custody.
function supersetProvider({ origin, env = process.env, fetchImpl = global.fetch } = {}) {
  origin = httpsOrigin(origin); let token;
  async function login() {
    if (env.SUPERSET_ACCOUNT_TYPE !== 'service' || !env.SUPERSET_USERNAME || !env.SUPERSET_PASSWORD) fail('AUTH_REQUIRED');
    let response;
    try { response = await fetchImpl(pathURL(origin, '/api/v1/security/login'), { method: 'POST', redirect: 'manual',
      signal: AbortSignal.timeout(30000), headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: env.SUPERSET_USERNAME, password: env.SUPERSET_PASSWORD, provider: 'db', refresh: false }) }); }
    catch { fail('AUTH_DEPENDENCY_FAILED'); }
    token = (await responseJSON(response)).access_token;
    if (!token || typeof token !== 'string') fail('AUTH_REQUIRED');
    return token;
  }
  const bearer = bearerProvider({ origin, getToken: async () => token || env.SUPERSET_TOKEN || login(), fetchImpl });
  return { async request(method, path, body) {
    try { return redact(await bearer.request(method, path, body), [env.SUPERSET_PASSWORD]); }
    catch (error) {
      // Refresh only a read after an explicit auth rejection; never replay uncertain writes.
      if (error.code !== 'AUTH_REQUIRED' || method !== 'GET' || env.SUPERSET_ACCOUNT_TYPE !== 'service') throw error;
      token = undefined; await login(); return redact(await bearer.request(method, path, body), [env.SUPERSET_PASSWORD]);
    }
  }, async close() { token = undefined; } };
}

function recoveryLeaksCode(uri, code) {
  let value = uri.href;
  if (/%(?![0-9a-f]{2})/i.test(value)) return true;
  // Includes path/query/fragment and nested encoding; cap decoding work and
  // reject still-encoded data rather than exporting an unexamined private code.
  for (let i = 0; i < 16; i++) {
    if (value.includes(code)) return true;
    if (!/%[0-9a-f]{2}/i.test(value)) return false;
    try { value = decodeURIComponent(value); } catch { return true; }
  }
  return true;
}

function deviceProvider({ issuer: issuerValue, origin, clientId, devicePath, tokenPath,
  scope, verificationOrigins, fetchImpl = global.fetch, now = Date.now } = {}) {
  const issuer = httpsOrigin(issuerValue); httpsOrigin(origin);
  if (!clientId || typeof clientId !== 'string' || /[\r\n]/.test(clientId)) fail('INVALID_PROVIDER');
  pathURL(issuer, devicePath); pathURL(issuer, tokenPath);
  const allowed = (verificationOrigins || [issuer]).map(httpsOrigin);
  let pending, token, terminal;
  async function form(path, fields) {
    let response;
    try { response = await fetchImpl(pathURL(issuer, path), { method: 'POST', redirect: 'manual', signal: AbortSignal.timeout(30000),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(fields).toString() }); }
    catch { fail('AUTH_DEPENDENCY_FAILED'); }
    // OAuth pending/slow_down are protocol replies, often HTTP 400.
    if (response.status >= 500 || response.status >= 300 && response.status < 400) fail('AUTH_DEPENDENCY_FAILED');
    const text = await response.text(); if (text.length > 65536) fail('OUTPUT_LIMIT');
    let data; try { data = JSON.parse(text); } catch { fail('AUTH_DEPENDENCY_FAILED'); }
    if (!response.ok && !data.error) fail('AUTH_DEPENDENCY_FAILED');
    return data;
  }
  return {
    async begin() {
      if (terminal) return { state: terminal };
      if (token) return { state: 'VERIFYING' };
      if (pending && now() < pending.expiresAt) return pending.public;
      const data = await form(devicePath, { client_id: clientId, ...(scope ? { scope } : {}) });
      let uri; try { uri = new URL(data.verification_uri); } catch { fail('UNSUPPORTED_DEVICE_GRANT'); }
      if (uri.protocol !== 'https:' || uri.username || uri.password || !allowed.includes(uri.origin)
          || typeof data.device_code !== 'string' || !data.device_code || data.device_code.length > 4096
          || recoveryLeaksCode(uri, data.device_code)
          || typeof data.user_code !== 'string' || !/^[a-zA-Z0-9 -]{1,64}$/.test(data.user_code)
          || !Number.isFinite(data.expires_in) || data.expires_in <= 0 || data.expires_in > 86400
          || data.interval !== undefined && (!Number.isFinite(data.interval) || data.interval < 1 || data.interval > 600)) fail('UNSUPPORTED_DEVICE_GRANT');
      const interval = (data.interval || 5) * 1000;
      const expiresAt = now() + data.expires_in * 1000;
      const visible = { state: 'NEEDS_USER', verificationUri: uri.href, userCode: data.user_code, expiresAt, pollAfterMs: interval };
      pending = { code: data.device_code, interval, nextPoll: now() + interval, expiresAt, public: visible };
      return visible;
    },
    async poll() {
      if (terminal) return { state: terminal };
      if (token) return { state: 'VERIFYING' };
      if (!pending) return this.begin();
      if (now() >= pending.expiresAt) { pending = undefined; terminal = 'EXPIRED'; return { state: terminal }; }
      if (now() < pending.nextPoll) return { ...pending.public, pollAfterMs: pending.nextPoll - now() };
      const data = await form(tokenPath, { client_id: clientId, device_code: pending.code, grant_type: 'urn:ietf:params:oauth:grant-type:device_code' });
      if (data.error === 'slow_down') pending.interval += 5000;
      if (['authorization_pending', 'slow_down'].includes(data.error)) {
        pending.nextPoll = now() + pending.interval; return { ...pending.public, pollAfterMs: pending.interval };
      }
      if (data.error) { pending = undefined; terminal = data.error === 'access_denied' ? 'REVOKED' : data.error === 'expired_token' ? 'EXPIRED' : 'UNSUPPORTED'; return { state: terminal }; }
      if (typeof data.access_token !== 'string' || !data.access_token || String(data.token_type).toLowerCase() !== 'bearer') fail('AUTH_REQUIRED');
      token = data.access_token; pending = undefined; return { state: 'VERIFYING' };
    },
    request: bearerProvider({ origin, getToken: () => token, fetchImpl }).request,
    async close() { pending = undefined; token = undefined; terminal = 'REVOKED'; },
  };
}
module.exports = { responseJSON, pathURL, bearerProvider, supersetProvider, deviceProvider };

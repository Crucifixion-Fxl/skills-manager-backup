'use strict';
const { connectBrowser } = require('./browser');
const { fail, httpsOrigin, redact } = require('./gateway');
const { pathURL } = require('./providers');

// Runs inside the caller-selected origin. Cookies stay in Chrome; sanitization
// occurs before the CDP result is serialized, including JWTs in ordinary fields.
async function browserRead(origin, apiOrigin, method, path, body, authentication, sanitize, mutating) {
  if (location.origin !== origin) return { code: 'BROWSER_ORIGIN_MISMATCH' };
  try {
    const headers = {};
    let storageToken;
    if (authentication.type === 'storage') {
      storageToken = localStorage.getItem(authentication.key);
      if (!storageToken || /[\r\n]/.test(storageToken)) return { code: 'AUTH_REQUIRED' };
      headers.Authorization = authentication.scheme === 'raw' ? storageToken : 'Bearer ' + storageToken;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(apiOrigin + path, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }), credentials: 'include', redirect: 'manual', signal: AbortSignal.timeout(30000) });
    if (response.status === 401) return { code: 'AUTH_REQUIRED' };
    if (response.status === 403) return { code: 'API_PERMISSION_DENIED' };
    if (response.status === 404) return { code: 'RESOURCE_NOT_FOUND' };
    if (response.status >= 500) return { code: mutating ? 'RESULT_UNKNOWN' : 'AUTH_DEPENDENCY_FAILED' };
    if (!response.ok) return { code: 'API_REQUEST_FAILED' };
    const reader = response.body.getReader(); let length = 0; const chunks = [];
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      length += value.length; if (length > 16 * 1024 * 1024) { await reader.cancel(); return { code: mutating ? 'RESULT_UNKNOWN' : 'OUTPUT_LIMIT' }; }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    const readableCookies = document.cookie.split(';').map(item => item.trim().split('=').slice(1).join('=')).filter(Boolean);
    // redact's only helper is plain, supplied in the same isolated expression.
    return { data: sanitize(payload, [...readableCookies, storageToken].filter(Boolean)) };
  } catch { return { code: mutating ? 'RESULT_UNKNOWN' : 'API_REQUEST_FAILED' }; }
}

function browserProvider({ origin: value, apiOrigin: apiValue, authentication = { type: 'cookie' }, allowWrites = false, readPostPaths = [], session, profile, tab, env = process.env, page: suppliedPage } = {}) {
  const origin = httpsOrigin(value), apiOrigin = httpsOrigin(apiValue || value);
  if (!['cookie', 'storage'].includes(authentication.type) || authentication.type === 'storage'
    && (!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(authentication.key || '') || !['raw', 'bearer'].includes(authentication.scheme))) fail('INVALID_PROFILE');
  let page = suppliedPage, connecting, closed = false;
  return {
    async request(method, path, body, { effect } = {}) {
      if (closed) fail('REVOKED');
      pathURL(apiOrigin, path);
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) fail('INVALID_INPUT');
      const mutating = effect ? effect === 'write' : method !== 'GET' && !(method === 'POST' && readPostPaths.includes(path));
      if (mutating && !allowWrites) fail('READ_ONLY_PROVIDER');
      if (!page) {
        connecting ||= connectBrowser({ origin, session, profile, tab, env });
        try { page = await connecting; } catch (error) { connecting = undefined; throw error; }
        if (closed) { await page.close?.(); fail('REVOKED'); }
      }
      if (mutating && page.supportsWrites !== true) fail('READ_ONLY_PROVIDER');
      const expression = `(async () => { const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value); return (${browserRead.toString()})(${JSON.stringify(origin)},${JSON.stringify(apiOrigin)},${JSON.stringify(method)},${JSON.stringify(path)},${JSON.stringify(body)},${JSON.stringify(authentication)},${redact.toString()},${JSON.stringify(mutating)}); })()`;
      let response;
      try { response = await page.cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); }
      catch { fail(mutating ? 'RESULT_UNKNOWN' : 'BROWSER_UNAVAILABLE'); }
      if (response.exceptionDetails) fail(mutating ? 'RESULT_UNKNOWN' : 'API_REQUEST_FAILED');
      const result = response.result?.value;
      const codes = new Set(['AUTH_REQUIRED', 'API_PERMISSION_DENIED', 'AUTH_DEPENDENCY_FAILED', 'API_REQUEST_FAILED', 'BROWSER_ORIGIN_MISMATCH', 'OUTPUT_LIMIT', 'RESOURCE_NOT_FOUND', 'RESULT_UNKNOWN']);
      if (result?.code) fail(codes.has(result.code) ? result.code : 'API_REQUEST_FAILED');
      if (!result || !Object.hasOwn(result, 'data')) fail(mutating ? 'RESULT_UNKNOWN' : 'API_REQUEST_FAILED');
      return redact(result.data);
    },
    async close() { closed = true; if (page) await page.close?.(); },
  };
}
module.exports = { browserProvider };

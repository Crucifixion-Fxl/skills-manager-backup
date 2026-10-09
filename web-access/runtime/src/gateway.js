'use strict';
const http = require('node:http');
const crypto = require('node:crypto');

class AccessError extends Error {
  constructor(code, message = code) { super(message); this.code = code; }
}
const fail = code => { throw new AccessError(code); };
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function httpsOrigin(value) {
  let url; try { url = new URL(value); } catch { fail('INVALID_ENDPOINT'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') fail('INVALID_ENDPOINT');
  return url.origin;
}
function redact(value, secrets = [], depth = 0) {
  if (depth > 20) return '[redacted-depth]';
  if (Array.isArray(value)) return value.map(v => redact(v, secrets, depth + 1));
  if (plain(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k,
    /(?:token|cookie|secret|password|authorization|device_code|oauth.?code|state)/i.test(k) && k !== 'state'
      ? '[redacted]' : redact(v, secrets, depth + 1)]));
  if (typeof value === 'string') {
    for (const secret of secrets) if (secret) value = value.split(secret).join('[redacted]');
    return value.replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '[redacted-jwt]');
  }
  return value;
}
async function jsonBody(request) {
  if (!/^application\/json(?:;|$)/i.test(request.headers['content-type'] || '')) fail('INVALID_INPUT');
  const chunks = []; let size = 0;
  for await (const chunk of request) {
    size += chunk.length; if (size > 2 * 1024 * 1024) fail('INPUT_LIMIT'); chunks.push(chunk);
  }
  let value; try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { fail('INVALID_INPUT'); }
  if (!plain(value)) fail('INVALID_INPUT');
  return value;
}

// Adapters are trusted local code. The remote peer supplies only declared operations and data.
async function createGateway({ origin: value, adapter, expectedSubject, scope = {}, allowWrites = false,
  port = 19826, key = crypto.randomBytes(32).toString('hex'), ttlMs = 30 * 60 * 1000, now = Date.now } = {}) {
  const origin = httpsOrigin(value);
  if (!/^[a-f0-9]{64}$/.test(key || '') || !Number.isInteger(port) || port < 0 || port > 65535
      || !Number.isInteger(ttlMs) || ttlMs < 1000 || ttlMs > 8 * 60 * 60 * 1000
      || !String(expectedSubject || '').trim() || typeof adapter?.verify !== 'function' || typeof adapter?.prepare !== 'function') fail('INVALID_COMPANION_CONFIG');
  const expiresAt = now() + ttlMs, leaseId = crypto.randomUUID();
  let state = 'NEEDS_USER', subject, busy = false, revoked = false, recovery;
  let adapterClosing;
  const closeAdapter = () => adapterClosing ||= Promise.resolve().then(() => adapter.close?.());
  function status() {
    if (revoked) state = 'REVOKED'; else if (now() >= expiresAt) state = 'EXPIRED';
    if (['REVOKED', 'EXPIRED'].includes(state)) closeAdapter().catch(() => {});
    return { state, leaseId, origin, subject, ...scope, expiresAt, writesEnabled: allowWrites,
      ...(state === 'NEEDS_USER' && recovery ? { recovery } : {}) };
  }
  async function ensure() {
    if (['REVOKED', 'EXPIRED'].includes(status().state)) return status();
    state = 'VERIFYING'; subject = undefined; recovery = undefined;
    try {
      const identity = await adapter.verify();
      if (!identity || !['string', 'number'].includes(typeof identity.subject)) fail('IDENTITY_UNVERIFIED');
      if (String(identity.subject) !== String(expectedSubject)) { state = 'BLOCKED_PERMISSION'; fail('AUTH_IDENTITY_MISMATCH'); }
      subject = String(identity.subject); state = 'READY'; recovery = undefined;
    } catch (error) {
      if (error.code === 'AUTH_REQUIRED') { state = 'NEEDS_USER'; recovery = error.publicRecovery; }
      else if (error.code === 'API_PERMISSION_DENIED') state = 'BLOCKED_PERMISSION';
      else if (error.code === 'AUTH_DEPENDENCY_FAILED') state = 'FAILED_DEPENDENCY';
      else if (['REVOKED', 'EXPIRED', 'UNSUPPORTED'].includes(error.code)) { state = error.code; if (state === 'REVOKED') revoked = true; }
      else { state = error.code === 'AUTH_IDENTITY_MISMATCH' ? 'BLOCKED_PERMISSION' : 'FAILED_DEPENDENCY'; throw error; }
    }
    return status();
  }
  async function handle(route, data) {
    if (data.origin !== origin) fail('BRIDGE_ORIGIN_MISMATCH');
    if (['/v1/status', '/v1/ensure', '/v1/disconnect'].includes(route)
        && Object.keys(data).some(k => k !== 'origin')) fail('INVALID_INPUT');
    if (route === '/v1/status') return status();
    if (route === '/v1/disconnect') { revoked = true; await closeAdapter(); return status(); }
    if (['EXPIRED', 'REVOKED'].includes(status().state)) fail(state);
    if (busy) fail('BRIDGE_BUSY');
    busy = true;
    try {
      if (route === '/v1/ensure') return await ensure();
      // Validate effects, bindings and input before any authentication or platform I/O.
      const invoke = adapter.prepare(route, data, { scope, allowWrites });
      if (typeof invoke !== 'function') fail('UNKNOWN_OPERATION');
      const verified = await ensure();
      if (verified.state !== 'READY') fail(verified.state === 'NEEDS_USER' ? 'AUTH_REQUIRED'
        : verified.state === 'BLOCKED_PERMISSION' ? 'API_PERMISSION_DENIED' : verified.state);
      let output;
      try { output = await invoke(); }
      catch (error) {
        if (error.code === 'AUTH_REQUIRED') { state = 'NEEDS_USER'; subject = undefined; recovery = undefined; }
        else if (['API_PERMISSION_DENIED', 'AUTH_IDENTITY_MISMATCH'].includes(error.code)) state = 'BLOCKED_PERMISSION';
        else if (error.code === 'AUTH_DEPENDENCY_FAILED') state = 'FAILED_DEPENDENCY';
        throw error;
      }
      if (['EXPIRED', 'REVOKED'].includes(status().state)) fail('RESULT_UNKNOWN');
      return output;
    } finally { busy = false; }
  }
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json'); response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    try {
      const received = Buffer.from(request.headers.authorization || ''), expected = Buffer.from(`Bearer ${key}`);
      if (received.length !== expected.length || !crypto.timingSafeEqual(received, expected)) {
        response.writeHead(401); response.end('{"ok":false,"code":"UNAUTHORIZED"}'); return;
      }
      if (request.headers.origin || request.method !== 'POST'
          || !/^127\.0\.0\.1:[0-9]{1,5}$/.test(request.headers.host || '') || Number(request.headers.host?.split(':')[1]) > 65535 || Number(request.headers.host?.split(':')[1]) < 1) fail('INVALID_INPUT');
      const result = await handle(request.url, await jsonBody(request));
      response.end(JSON.stringify({ ok: true, data: redact(result, [key]) }));
    } catch (error) {
      const code = /^[A-Z][A-Z0-9_]{1,70}$/.test(error.code || '') ? error.code : 'RESULT_UNKNOWN';
      response.writeHead(code === 'BRIDGE_BUSY' ? 409 : 400); response.end(JSON.stringify({ ok: false, code }));
    }
  });
  server.requestTimeout = 30000; server.headersTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  const leaseTimer = setTimeout(() => status(), ttlMs); leaseTimer.unref();
  let closing;
  return { url: `http://127.0.0.1:${server.address().port}`, key, origin, expiresAt, leaseId, server, status, ensure,
    close() { return closing ||= (async () => { revoked = true; clearTimeout(leaseTimer); server.closeIdleConnections(); await new Promise(resolve => server.close(resolve)); await closeAdapter(); })(); } };
}
module.exports = { AccessError, fail, plain, httpsOrigin, redact, createGateway };

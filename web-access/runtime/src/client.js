'use strict';
const { fail, httpsOrigin } = require('./gateway');
const { loopback } = require('./browser');
const { readCredentials } = require('./custody');

function createClient({ file, origin: value, fetchImpl = global.fetch }) {
  const stored = readCredentials(file), origin = httpsOrigin(value || stored.origin);
  if (stored.origin !== origin) fail('BRIDGE_ORIGIN_MISMATCH');
  const endpoint = loopback(stored.url);
  async function call(route, input = {}) {
    if (!['/v1/status', '/v1/ensure', '/v1/disconnect', '/v1/request', '/v1/post', '/v1/create-workorder'].includes(route)) fail('UNKNOWN_OPERATION');
    let response, result;
    try {
      response = await fetchImpl(endpoint + route, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(300000),
        headers: { Authorization: `Bearer ${stored.key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ ...input, origin }) });
      result = await response.json();
    } catch { fail('BRIDGE_RESULT_UNKNOWN'); }
    if (!response.ok || result?.ok !== true) fail(/^[A-Z][A-Z0-9_]{1,70}$/.test(result?.code || '') ? result.code : 'BRIDGE_REQUEST_FAILED');
    return result.data;
  }
  return { call, ensure: () => call('/v1/ensure'), status: () => call('/v1/status'), disconnect: () => call('/v1/disconnect') };
}
module.exports = { createClient };

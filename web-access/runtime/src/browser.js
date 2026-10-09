'use strict';
const { execFile } = require('node:child_process');
const { AccessError, fail, httpsOrigin } = require('./gateway');

function loopback(value) {
  let u; try { u = new URL(value); } catch { fail('INVALID_CDP_ENDPOINT'); }
  if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || u.pathname !== '/' || u.username || u.password || u.search || u.hash) fail('INVALID_CDP_ENDPOINT');
  return u.origin;
}
function opencliEntry() {
  try { return require.resolve('@jackwener/opencli'); } catch { fail('OPENCLI_UNAVAILABLE'); }
}
function runOpenCLI(argv, { env = process.env, timeout = 35000 } = {}) {
  return new Promise((resolve, reject) => {
    execFile(process.execPath, [opencliEntry(), ...argv], { env, timeout, maxBuffer: 16 * 1024 * 1024 }, (error, stdout) => {
      let data; try { data = JSON.parse(stdout); } catch { /* never propagate raw stdout/stderr */ }
      if (error || data?.error) return reject(new AccessError('BROWSER_UNAVAILABLE'));
      resolve(data);
    });
  });
}

// Direct CDP attaches only to an exact caller-selected tab. Closing disconnects this socket only.
async function connectCDP({ endpoint, targetUrl, targetId, origin, fetchImpl = global.fetch }) {
  const base = loopback(endpoint);
  let wanted; try { wanted = new URL(targetUrl); } catch { fail('BROWSER_TARGET_REQUIRED'); }
  if (wanted.origin !== origin || wanted.username || wanted.password) fail('BROWSER_TARGET_REQUIRED');
  let targets;
  try {
    const response = await fetchImpl(base + '/json/list', { redirect: 'error', signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(); targets = await response.json();
  } catch { fail('BROWSER_UNAVAILABLE'); }
  const matches = targets.filter(t => t.type === 'page' && t.url === targetUrl && (!targetId || t.id === targetId));
  if (matches.length !== 1) fail('BROWSER_TARGET_REQUIRED');
  const wsUrl = new URL(matches[0].webSocketDebuggerUrl);
  if (wsUrl.protocol !== 'ws:' || wsUrl.host !== new URL(base).host || wsUrl.username || wsUrl.password || wsUrl.search || wsUrl.hash) fail('INVALID_CDP_ENDPOINT');
  const WebSocket = require('ws');
  const socket = new WebSocket(wsUrl.href, { handshakeTimeout: 10000, maxPayload: 16 * 1024 * 1024 });
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', () => reject(new AccessError('BROWSER_UNAVAILABLE'))); });
  let sequence = 0;
  const pending = new Map();
  const rejectAll = () => { for (const call of pending.values()) { clearTimeout(call.timer); call.reject(new AccessError('BROWSER_UNAVAILABLE')); } pending.clear(); };
  socket.on('error', rejectAll); socket.on('close', rejectAll);
  socket.on('message', message => {
    let value; try { value = JSON.parse(message.toString()); } catch { return; }
    const call = pending.get(value.id); if (!call) return;
    pending.delete(value.id); clearTimeout(call.timer);
    value.error ? call.reject(new AccessError('BROWSER_UNAVAILABLE')) : call.resolve(value.result);
  });
  return { supportsWrites: true,
    cdp(method, params) {
      if (method !== 'Runtime.evaluate') fail('UNKNOWN_OPERATION');
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => { pending.delete(id); reject(new AccessError('RESULT_UNKNOWN')); }, 35000);
        pending.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ id, method, params }), error => { if (error) { clearTimeout(timer); pending.delete(id); reject(new AccessError('BROWSER_UNAVAILABLE')); } });
      });
    },
    async close() { rejectAll(); socket.close(); },
  };
}

async function connectBrowser({ origin: value, session, profile, env = process.env, tab } = {}) {
  const origin = httpsOrigin(value);
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(session || '')) fail('BROWSER_SESSION_REQUIRED');
  if (env.OPENCLI_CDP_ENDPOINT) return connectCDP({ endpoint: env.OPENCLI_CDP_ENDPOINT,
    targetUrl: env.OPENCLI_CDP_TARGET, targetId: env.OPENCLI_CDP_TARGET_ID, origin });
  if (!profile || !tab) fail('BROWSER_TARGET_REQUIRED');
  // Public CLI only. Browser eval may retry navigation; this backend is therefore read-only.
  return { supportsWrites: false,
    async cdp(method, params) {
      if (method !== 'Runtime.evaluate') fail('UNKNOWN_OPERATION');
      const data = await runOpenCLI(['--profile', profile, 'browser', session, 'eval', params.expression, '--tab', tab], { env });
      return { result: { value: data } };
    },
    async close() {},
  };
}
module.exports = { loopback, runOpenCLI, connectCDP, connectBrowser };

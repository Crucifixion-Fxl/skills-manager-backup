'use strict';
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { WebSocketServer } = require('ws');
const { fail } = require('./gateway');

const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>浏览器画面</title><style>body{margin:0;background:#141820;color:white;font:16px system-ui}header{padding:12px}#screen{height:calc(100vh - 52px)}button{font:inherit;padding:4px 16px}</style>
<header><span id="status">正在连接浏览器画面…</span> <button id="connect" hidden>重新连接</button></header><main id="screen"></main>
<script type="module" src="/viewer.js"></script></html>`;
const script = `import RFB from '/novnc/core/rfb.js';
const status=document.querySelector('#status'),button=document.querySelector('#connect');let rfb;
function connect(){button.hidden=true;status.textContent='正在连接浏览器画面…';
const proof=sessionStorage.getItem('web_view_proof');if(!proof){status.textContent='画面授权已失效，请重新启动本次连接。';return;}
rfb=new RFB(document.querySelector('#screen'),'ws://'+location.host+'/websockify',{wsProtocols:['web-view-v1',proof]});rfb.scaleViewport=true;rfb.resizeSession=false;
rfb.addEventListener('connect',()=>status.textContent='请在画面中完成登录或验证，完成后回到对话回复“完成”。');
rfb.addEventListener('disconnect',()=>{status.textContent='画面连接已断开';button.hidden=false;});
rfb.addEventListener('credentialsrequired',()=>{status.textContent='该画面需要额外连接配置，请联系启动此会话的人员';rfb.disconnect();});}
button.onclick=connect;connect();`;

const bootstrapHtml = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>浏览器画面</title>
<p id="status">正在连接浏览器画面…</p><script src="/bootstrap.js"></script></html>`;
const bootstrapScript = `const fragment=location.hash.slice(1);history.replaceState(null,'','/bootstrap');
(async()=>{try{const capability=JSON.parse(decodeURIComponent(fragment));
const response=await fetch('/bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(capability)});
if(!response.ok)throw new Error();const session=await response.json();
sessionStorage.setItem('web_view_proof','web-view-proof.'+session.session+'.'+session.proof);location.replace('/');}catch{document.querySelector('#status').textContent='画面授权已失效，请重新启动本次连接。';}})();`;

function privateDirectory(root) {
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  const info = fs.lstatSync(root);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o077)) fail('INVALID_VIEWER_CAPABILITY_FILE');
}
function capabilityPath(port) {
  return path.join(os.homedir(), '.cache', 'addx-web-view', `viewer-${port}.json`);
}
function storeCapability(file, capability) {
  privateDirectory(path.dirname(file));
  const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(capability)); } finally { fs.closeSync(fd); }
}
function readCapability(file) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const info = fs.fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o077) || info.size > 2048) fail('INVALID_VIEWER_CAPABILITY_FILE');
    return validateCapability(JSON.parse(fs.readFileSync(fd, 'utf8')));
  } finally { fs.closeSync(fd); }
}
function validateCapability(value) {
  if (!value || !/^[a-f0-9]{32}$/.test(value.session || '') || !/^[a-f0-9]{64}$/.test(value.token || '') ||
      !Number.isSafeInteger(value.expiresAt) || value.expiresAt <= Date.now()) fail('INVALID_VIEWER_CAPABILITY');
  return { session: value.session, token: value.token, expiresAt: value.expiresAt };
}
function launchBootstrap(url, capability, { open = true, spawnImpl = spawn } = {}) {
  validateCapability(capability);
  const location = new URL(url);
  if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1' || location.pathname !== '/' || location.search || location.hash || location.username || location.password) fail('INVALID_VIEWER_CONFIG');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'web-view-launch-')); fs.chmodSync(root, 0o700);
  const file = path.join(root, 'open.html');
  const target = url + 'bootstrap#' + encodeURIComponent(JSON.stringify(capability));
  // The one-use fragment lives only in a private file, never in a process argv.
  fs.writeFileSync(file, `<!doctype html><meta charset="utf-8"><script>location.replace(${JSON.stringify(target)});</script>`, { mode: 0o600, flag: 'wx' });
  const close = () => fs.rmSync(root, { recursive: true, force: true });
  let opened = Promise.resolve();
  if (open) {
    opened = new Promise((resolve, reject) => {
      const failed = () => { close(); const error = new Error('VIEWER_BROWSER_OPEN_FAILED'); error.code = 'VIEWER_BROWSER_OPEN_FAILED'; reject(error); };
      try {
        const child = spawnImpl(process.platform === 'darwin' ? 'open' : 'xdg-open', [file], { stdio: 'ignore' });
        child.once('error', failed);
        child.once('exit', code => code === 0 ? resolve() : failed());
      } catch { failed(); }
    });
    // Callers await this result; also prevent discarded API handles leaking rejections.
    opened.catch(() => {});
  }
  return { file, close, opened };
}
function equalSecret(left, right) {
  if (typeof left !== 'string') return false;
  const a = Buffer.from(left), b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function localHost(host) {
  const match = /^127\.0\.0\.1:([0-9]{1,5})$/.exec(host || '');
  return !!match && Number(match[1]) >= 1 && Number(match[1]) <= 65535;
}

async function createViewer({ port = 19827, vncPort, ttlMs = 30 * 60 * 1000, capabilityFile } = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535 || !Number.isInteger(vncPort) || vncPort < 1 || vncPort > 65535
      || !Number.isInteger(ttlMs) || ttlMs < 1000 || ttlMs > 8 * 60 * 60 * 1000) fail('INVALID_VIEWER_CONFIG');
  const assets = fs.realpathSync(path.resolve(path.dirname(require.resolve('@novnc/novnc')), '..'));
  const sockets = new Set();
  const capability = { session: crypto.randomBytes(16).toString('hex'), token: crypto.randomBytes(32).toString('hex'), expiresAt: Date.now() + ttlMs };
  const cookieName = 'web_view_' + capability.session;
  let bootstrapUsed = false, cookieSecret, wsProof, revoked = false, ownsCapabilityFile = false;
  const authorized = req => !revoked && Date.now() < capability.expiresAt && cookieSecret &&
    (req.headers.cookie || '').split(';').some(item => {
      const separator = item.indexOf('=');
      return item.slice(0, separator).trim() === cookieName && equalSecret(item.slice(separator + 1), cookieSecret);
    });
  const server = http.createServer((req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:*; frame-ancestors 'none'");
    if (!localHost(req.headers.host) || req.headers.origin && req.headers.origin !== `http://${req.headers.host}`) { res.writeHead(403); res.end(); return; }
    res.setHeader('Referrer-Policy', 'no-referrer');
    if (req.url === '/bootstrap' && req.method === 'POST') {
      if (req.headers.origin !== `http://${req.headers.host}` || bootstrapUsed || revoked || Date.now() >= capability.expiresAt) { res.writeHead(403); res.end(); return; }
      let body = '';
      req.on('data', chunk => { body += chunk; if (body.length > 2048) req.destroy(); });
      req.on('end', () => {
        try {
          const supplied = JSON.parse(body);
          if (bootstrapUsed || revoked || Date.now() >= capability.expiresAt || supplied.session !== capability.session || supplied.expiresAt !== capability.expiresAt || !equalSecret(supplied.token, capability.token)) throw new Error();
          bootstrapUsed = true; cookieSecret = crypto.randomBytes(32).toString('hex'); wsProof = crypto.randomBytes(32).toString('hex');
          if (ownsCapabilityFile) { fs.rmSync(capabilityFile, { force: true }); ownsCapabilityFile = false; }
          res.setHeader('Set-Cookie', `${cookieName}=${cookieSecret}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.max(1, Math.floor((capability.expiresAt - Date.now()) / 1000))}`);
          res.setHeader('Content-Type', 'application/json');
          res.writeHead(200); res.end(JSON.stringify({ session: capability.session, proof: wsProof }));
        } catch { res.writeHead(403); res.end(); }
      });
      return;
    }
    if (req.method === 'GET' && req.url === '/bootstrap') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(bootstrapHtml); return; }
    if (req.method === 'GET' && req.url === '/bootstrap.js') { res.setHeader('Content-Type', 'application/javascript'); res.end(bootstrapScript); return; }
    if (req.method !== 'GET' || !authorized(req)) { res.writeHead(403); res.end(); return; }
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(html); return; }
    if (req.url === '/viewer.js') { res.setHeader('Content-Type', 'application/javascript'); res.end(script); return; }
    if (!/^\/novnc\/(?:core|vendor)\/[a-zA-Z0-9_/-]+\.js$/.test(req.url || '') || req.url.includes('..')) { res.writeHead(404); res.end(); return; }
    const file = path.join(assets, req.url.slice('/novnc/'.length));
    try {
      if (!fs.realpathSync(file).startsWith(assets + path.sep)) throw new Error();
      res.setHeader('Content-Type', 'application/javascript'); res.end(fs.readFileSync(file));
    } catch { res.writeHead(404); res.end(); }
  });
  const ws = new WebSocketServer({ noServer: true, maxPayload: 2 * 1024 * 1024,
    handleProtocols: protocols => protocols.has('web-view-v1') ? 'web-view-v1' : false });
  server.on('upgrade', (req, socket, head) => {
    const host = req.headers.host;
    const protocols = (req.headers['sec-websocket-protocol'] || '').split(',').map(item => item.trim());
    const proven = wsProof && protocols.length === 2 && protocols[0] === 'web-view-v1' &&
      equalSecret(protocols[1], 'web-view-proof.' + capability.session + '.' + wsProof);
    if (req.url !== '/websockify' || !localHost(host) || req.headers.origin !== `http://${host}` || !authorized(req) || !proven) { socket.destroy(); return; }
    ws.handleUpgrade(req, socket, head, peer => {
      const tcp = net.connect({ host: '127.0.0.1', port: vncPort });
      sockets.add(tcp); sockets.add(peer);
      const close = () => { tcp.destroy(); peer.terminate(); sockets.delete(tcp); sockets.delete(peer); };
      tcp.on('data', data => { if (peer.readyState === 1) { tcp.pause(); peer.send(data, error => error ? close() : tcp.resume()); } });
      peer.on('message', (data, binary) => { if (!binary) return close(); if (!tcp.write(data)) peer.pause(); });
      tcp.on('drain', () => peer.resume());
      tcp.on('error', close); tcp.on('close', close); peer.on('error', close); peer.on('close', close);
    });
  });
  server.requestTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  let closing;
  const close = () => closing ||= (async () => {
    revoked = true; cookieSecret = undefined; wsProof = undefined;
    if (ownsCapabilityFile) { fs.rmSync(capabilityFile, { force: true }); ownsCapabilityFile = false; }
    clearTimeout(timer); for (const socket of sockets) socket.destroy?.() || socket.terminate?.();
    ws.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  })();
  const timer = setTimeout(close, ttlMs); timer.unref();
  if (capabilityFile) {
    try {
      if (capabilityFile === true) capabilityFile = capabilityPath(server.address().port);
      storeCapability(capabilityFile, capability); ownsCapabilityFile = true;
    }
    catch { await close(); fail('INVALID_VIEWER_CAPABILITY_FILE'); }
  }
  return { url: `http://127.0.0.1:${server.address().port}/`, close, server, capability, capabilityFile };
}
module.exports = { createViewer, capabilityPath, storeCapability, readCapability, validateCapability, launchBootstrap };

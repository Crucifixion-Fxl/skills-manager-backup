// NIP-42 认证后发布事件。签名委托给同目录的 nostr_sign.py
//（challenge 是运行时才收到的，必须现签）。
//
// 用法：BUZZ_OWNER_SECKEY=<hex> node publish_event.mjs <relay_wss> <event.json>
//
// 私钥只从环境变量读，**不走命令行参数** —— Linux 上 /proc/<pid>/cmdline 全局可读
//（-r--r--r--），私钥放 argv 等于同机任何用户 `ps aux` 就能拿走；
// /proc/<pid>/environ 是 0400，仅属主可读。
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const [relay, evFile] = process.argv.slice(2);
const seckey = process.env.BUZZ_OWNER_SECKEY;
if (!seckey) {
  console.error('BUZZ_OWNER_SECKEY 未设置。用法：BUZZ_OWNER_SECKEY=<hex> node publish_event.mjs <relay_wss> <event.json>');
  process.exit(64);
}
if (!/^[0-9a-fA-F]{64}$/.test(seckey)) {
  console.error('BUZZ_OWNER_SECKEY 必须是 64 位 hex');
  process.exit(64);
}
if (!relay || !evFile) {
  console.error('用法：BUZZ_OWNER_SECKEY=<hex> node publish_event.mjs <relay_wss> <event.json>');
  process.exit(64);
}
const sign = (o) => JSON.parse(execFileSync('python3', [join(HERE, 'nostr_sign.py')],
  { input: JSON.stringify({ seckey, ...o }), encoding: 'utf8' }));

const target = JSON.parse(readFileSync(evFile, 'utf8'));
const ws = new WebSocket(relay);
let authed = false, sentId = null;
const done = (code, msg) => { console.log(msg); try { ws.close(); } catch {} process.exit(code); };
setTimeout(() => done(2, 'TIMEOUT: relay 30s 无响应'), 30000);

ws.onopen = () => console.log('ws connected');
ws.onerror = (e) => done(2, 'ws error: ' + (e.message || e.type));
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg[0] === 'AUTH' && !authed) {
    const ev = sign({ kind: 22242, tags: [['relay', relay], ['challenge', msg[1]]], content: '' });
    ws.send(JSON.stringify(['AUTH', ev]));
    authed = ev.id;
    return;
  }
  if (msg[0] === 'OK' && msg[1] === authed) {
    if (!msg[2]) return done(3, 'AUTH 被拒: ' + (msg[3] || ''));
    console.log('auth ok');
    const ev = sign(target);
    sentId = ev.id;
    ws.send(JSON.stringify(['EVENT', ev]));
    return;
  }
  if (msg[0] === 'OK' && msg[1] === sentId) {
    return done(msg[2] ? 0 : 4, msg[2] ? `published ${sentId}` : `REJECTED: ${msg[3] || ''}`);
  }
  if (msg[0] === 'NOTICE') console.log('notice:', msg[1]);
};

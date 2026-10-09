#!/usr/bin/env node
'use strict';
const { createViewer, launchBootstrap } = require('../src/viewer');
async function main() {
  const args = {};
  for (const arg of process.argv.slice(2)) {
    if (arg === '--open' && !args.open) { args.open = true; continue; }
    if (arg === '--no-open' && !args.noOpen) { args.noOpen = true; continue; }
    if (arg.startsWith('--capability-file=') && !args.capabilityFile) { args.capabilityFile = arg.slice('--capability-file='.length); if (!args.capabilityFile.startsWith('/') || /[\r\n\0]/.test(args.capabilityFile)) throw new Error(); continue; }
    const target = /^--ssh-target=([a-zA-Z0-9][a-zA-Z0-9_.@-]{0,199})$/.exec(arg);
    if (target && !args.target) { args.target = target[1]; continue; }
    const match = /^--(port|vnc-port|ttl-seconds)=([0-9]+)$/.exec(arg);
    if (!match || Object.hasOwn(args, match[1])) throw new Error(); args[match[1]] = Number(match[2]);
  }
  if (args.target) {
    const tunnel = await require('../src/viewer-tunnel').viewerTunnel({ target: args.target, port: args.port,
      ttlMs: args['ttl-seconds'] ? args['ttl-seconds'] * 1000 : undefined,
      capabilityFile: args.capabilityFile, openBrowser: !args.noOpen });
    process.stdout.write(JSON.stringify({ state: 'VIEWER_READY', url: tunnel.url, prompt: '请打开画面完成登录或验证，完成后回到对话回复“完成”。' }) + '\n');
    try { if (await tunnel.exit) throw new Error(); } finally { tunnel.close(); }
    return;
  }
  const viewer = await createViewer({ port: args.port, vncPort: args['vnc-port'], ttlMs: args['ttl-seconds'] ? args['ttl-seconds'] * 1000 : undefined, capabilityFile: args.capabilityFile || true });
  const launcher = args.open ? launchBootstrap(viewer.url, viewer.capability) : undefined;
  viewer.server.once('close', () => launcher?.close());
  try { await launcher?.opened; } catch (error) { await viewer.close(); throw error; }
  process.stdout.write(JSON.stringify({ state: 'VIEWER_LISTENING', url: viewer.url, capabilityFile: viewer.capabilityFile }) + '\n');
  const stop = () => { launcher?.close(); return viewer.close(); }; process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
main().catch(error => {
  const code = /^[A-Z][A-Z0-9_]{1,70}$/.test(error.code || '') ? error.code : 'VIEWER_SETUP_FAILED';
  process.stderr.write(code + '\n'); process.exitCode = 1;
});

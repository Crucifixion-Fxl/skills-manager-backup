#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const { serve, loadProfile, createClient } = require('../src');
const { fail, redact } = require('../src/gateway');
const { saveCredentials } = require('../src/custody');
const { sshSpec, serveTunnel } = require('../src/ssh');
const allowed = new Set(['tool', 'expected-subject', 'port', 'ttl-seconds', 'session', 'browser-profile', 'tab',
  'application-id', 'tenant-id', 'transport', 'allow-writes', 'credentials-file', 'ssh-target', 'remote-credentials', 'remote-port', 'remote-node', 'operation', 'input-file', 'operations-file', 'url', 'agent-location', 'browser-location', 'retention', 'issuer', 'secret-file']);
function parse(argv) {
  const [command, ...rest] = argv, args = {};
  for (const arg of rest) {
    const match = /^--([a-z-]+)=(.+)$/.exec(arg);
    if (!match || !allowed.has(match[1]) || Object.hasOwn(args, match[1])) fail('INVALID_INPUT');
    args[match[1]] = match[2];
  }
  return { command, args };
}
const output = value => process.stdout.write(JSON.stringify(redact(value), null, 2) + '\n');
async function main(argv, env = process.env) {
  const { command, args } = parse(argv);
  if (command === 'plan') return output(require('../src/access-plan').planAccess({ tool: args.tool, url: args.url,
    agentLocation: args['agent-location'], browserLocation: args['browser-location'], sshTarget: args['ssh-target'], retention: args.retention, skillsRoot: env.SAAS_SKILLS_ROOT }));
  if (['issue-token', 'revoke-token'].includes(command)) {
    const profile = loadProfile(args.tool);
    const options = { profile, issuer: args.issuer, expectedSubject: args['expected-subject'], secretFile: args['secret-file'],
      retention: args.retention, browser: { env, session: args.session, profile: args['browser-profile'], tab: args.tab } };
    const issuer = require('../src/token-issuer');
    if (command === 'revoke-token') return output(await issuer.revokeToken(options));
    if (!args['input-file']) fail('INVALID_INPUT');
    options.input = JSON.parse(fs.readFileSync(args['input-file'], 'utf8'));
    return output(await issuer.issueToken(options));
  }
  if (command === 'describe') return output(loadProfile(args.tool));
  if (['ensure', 'status', 'disconnect', 'call'].includes(command)) {
    const file = args['credentials-file'] || env.SAAS_AUTH_CREDENTIALS_FILE;
    const client = createClient({ file });
    if (command !== 'call') return output(await client[command]());
    const input = args['input-file'] ? JSON.parse(fs.readFileSync(args['input-file'], 'utf8')) : {};
    return output(await client.call('/v1/request', { operation: args.operation, input }));
  }
  if (!['serve', 'tunnel'].includes(command)) fail('UNKNOWN_COMMAND');
  if (args['allow-writes'] && !['true', 'false'].includes(args['allow-writes'])) fail('INVALID_INPUT');
  if (command === 'tunnel') sshSpec(args, Number(args.port || 19826));
  else if (!args['credentials-file']) fail('PRIVATE_OUTPUT_REQUIRED');
  const bridge = await serve({ tool: args.tool, expectedSubject: args['expected-subject'] || env.SAAS_AUTH_EXPECTED_SUBJECT,
    port: Number(args.port || 19826), ttlMs: args['ttl-seconds'] ? Number(args['ttl-seconds']) * 1000 : undefined,
    session: args.session, profile: args['browser-profile'], tab: args.tab,
    allowedOperations: args['operations-file'] ? JSON.parse(fs.readFileSync(args['operations-file'], 'utf8')) : [],
    applicationId: args['application-id'], tenantId: args['tenant-id'], transport: args.transport, allowWrites: args['allow-writes'] === 'true', env });
  if (command === 'tunnel') return serveTunnel(args, bridge, output);
  let cleanup;
  try {
    cleanup = saveCredentials(args['credentials-file'], { url: bridge.url, key: bridge.key, origin: bridge.origin, expiresAt: bridge.expiresAt });
    output({ state: 'COMPANION_LISTENING', origin: bridge.origin, expiresAt: bridge.expiresAt, credentialSaved: true });
    await new Promise(resolve => {
      const stop = () => { clearTimeout(timer); process.off('SIGINT', stop); process.off('SIGTERM', stop); resolve(); };
      const timer = setTimeout(stop, Math.max(0, bridge.expiresAt - Date.now()));
      process.once('SIGINT', stop); process.once('SIGTERM', stop);
    });
  } finally { cleanup?.(); await bridge.close(); }
}
if (require.main === module) main(process.argv.slice(2)).catch(error => {
  output({ ok: false, code: /^[A-Z][A-Z0-9_]{1,70}$/.test(error.code || '') ? error.code : 'INVALID_INPUT', ...(error.publicRecovery ? { recovery: error.publicRecovery } : {}) }); process.exitCode = 1;
});
module.exports = { parse, main };

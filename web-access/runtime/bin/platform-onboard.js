#!/usr/bin/env node
'use strict';
const { loadProfile } = require('../src');
const { installAdapter } = require('../src/opencli-install');
const { fail, redact } = require('../src/gateway');
async function main(argv) {
  const [command, ...rest] = argv, args = {};
  for (const value of rest) {
    const parsed = /^--(tool|root|replace)=(.+)$/.exec(value);
    if (!parsed || Object.hasOwn(args, parsed[1])) fail('INVALID_INPUT');
    args[parsed[1]] = parsed[2];
  }
  if (command === 'agent-reach-doctor') {
    if (Object.keys(args).length) fail('INVALID_INPUT');
    const result = await require('../src/agent-reach').agentReachDoctor();
    process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return;
  }
  if (command !== 'opencli-install' || args.replace && !['true', 'false'].includes(args.replace)) fail('INVALID_INPUT');
  const profile = loadProfile(args.tool);
  if (!['bearer-env', 'superset-service', 'device'].includes(profile.provider)) fail('PLATFORM_ADAPTER_REQUIRED');
  const result = installAdapter({ profile, ...(args.root ? { root: args.root } : {}), replace: args.replace === 'true' });
  process.stdout.write(JSON.stringify(redact(result), null, 2) + '\n');
}
if (require.main === module) main(process.argv.slice(2)).catch(error => {
  process.stdout.write(JSON.stringify({ ok: false, code: /^[A-Z][A-Z0-9_]{1,70}$/.test(error.code || '') ? error.code : 'INVALID_INPUT' }) + '\n'); process.exitCode = 1;
});
module.exports = { main };

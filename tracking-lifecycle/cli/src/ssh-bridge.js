'use strict';
const { webAccessRuntimeModule } = require('./web-access-runtime');
const shared = webAccessRuntimeModule('ssh.js');
const { startBridge } = require('./remote-bridge');
async function serveTunnel(args, env, writeOutput) {
  shared.sshSpec(args, Number(args.port || 19826));
  const bridge = await startBridge({ origin: env.TRACKING_PLATFORM_BASE_URL,
    port: Number(args.port || 19826), session: args.session, profile: args.profile, tab: args.tab,
    allowWrites: args['allow-writes'] === 'true', env,
    expectedSubject: args['expected-subject'] || env.SAAS_AUTH_EXPECTED_SUBJECT,
    applicationId: args['application-id'] || env.TRACKING_APPLICATION_ID,
    ttlMs: args['ttl-seconds'] ? Number(args['ttl-seconds']) * 1000 : undefined });
  return shared.serveTunnel(args, bridge, writeOutput);
}
module.exports = { ...shared, serveTunnel };

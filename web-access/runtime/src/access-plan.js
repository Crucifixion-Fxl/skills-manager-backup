'use strict';
const { loadProfile } = require('./index');
const { fail, httpsOrigin } = require('./gateway');
function planAccess({ tool, url, agentLocation = 'local', browserLocation = 'local', sshTarget, retention = 'once', skillsRoot,
  allowLegacyCatalog = skillsRoot !== undefined || process.env.SAAS_SKILLS_ROOT !== undefined } = {}) {
  if (!['local', 'remote'].includes(agentLocation) || !['local', 'remote'].includes(browserLocation) || !['once', 'save'].includes(retention)) fail('INVALID_INPUT');
  if (agentLocation === 'local' && browserLocation === 'remote') fail('INVALID_INPUT');
  if (agentLocation === 'remote' && !sshTarget) fail('SSH_TARGET_REQUIRED');
  if (sshTarget && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$/.test(sshTarget)) fail('INVALID_SSH_TARGET');
  let parsed;
  if (url) { try { parsed = new URL(url); } catch { fail('INVALID_ENDPOINT'); } if (parsed.username || parsed.password) fail('INVALID_ENDPOINT'); httpsOrigin(parsed.origin); }
  const profile = tool ? loadProfile(tool, { skillsRoot, allowLegacyCatalog }) : undefined;
  const origin = profile?.origin || parsed?.origin;
  if (!origin) fail(tool ? 'PLATFORM_URL_REQUIRED' : 'INVALID_ENDPOINT');
  httpsOrigin(origin);
  if (profile?.origin && parsed && parsed.origin !== profile.origin) fail('BRIDGE_ORIGIN_MISMATCH');
  const connection = agentLocation === 'local' ? 'visible-local-browser' : browserLocation === 'local' ? 'companion-ssh-reverse' : 'novnc-ssh-local';
  return { state: 'PLANNED', ...(tool ? { tool, owner: profile.owner } : {}), origin, connection,
    ...(sshTarget ? { sshTarget } : {}), userInterface: connection === 'novnc-ssh-local' ? 'vnc' : 'local-browser',
    retention, platformContract: profile?.verification || 'unknown', authenticationVerified: false,
    credentialStrategy: profile && profile.provider !== 'delegated' ? profile.provider : 'browser-session-until-contract-verified',
    verification: profile?.identity ? 'owner-identity-and-permission-probes' : 'caller-content-check; identity and token capabilities remain unverified' };
}
module.exports = { planAccess };

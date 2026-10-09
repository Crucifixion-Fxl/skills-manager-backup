'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { fail, createGateway } = require('./gateway');
const { nativeAdapter } = require('./native-adapter');
const { bearerProvider, supersetProvider, deviceProvider } = require('./providers');
const { browserProvider } = require('./browser-provider');
const { createClient } = require('./client');
// Resolve paths from the distribution's shared skills directory. The runtime
// lives under skills/agent-harness/web-access, while owner profiles remain
// registered relative to the skills tree.
const defaultSkillsRoot = path.resolve(__dirname, '../../../..');
function resolveSkillsRoot(value = process.env.SAAS_SKILLS_ROOT) {
  if (value && !path.isAbsolute(value)) fail('INVALID_SKILLS_ROOT');
  const root = value || defaultSkillsRoot;
  try { return fs.realpathSync(root); } catch { fail('PROFILE_CATALOG_REQUIRED'); }
}

function loadProfile(tool, { skillsRoot: suppliedRoot, allowLegacyCatalog } = {}) {
  const skillsRoot = resolveSkillsRoot(suppliedRoot);
  // Keep explicit-root fixture/standalone compatibility, while allowing callers
  // that derived a root internally (such as serve) to opt out of legacy probing.
  const legacyCatalogOptIn = allowLegacyCatalog ?? (suppliedRoot !== undefined || process.env.SAAS_SKILLS_ROOT !== undefined);
  if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(tool || '')) fail('INVALID_TOOL');
  let catalog;
  const catalogPaths = [path.join(skillsRoot, 'agent-harness/platform-onboarding/references/tools.json')];
  // Older standalone installations and explicit test fixtures place the
  // catalog directly under SAAS_SKILLS_ROOT. Keep that opt-in layout support
  // without probing alternate roots in the default installation.
  if (legacyCatalogOptIn) catalogPaths.push(path.join(skillsRoot, 'platform-onboarding/references/tools.json'));
  try {
    const catalogPath = catalogPaths.find(candidate => fs.existsSync(candidate));
    if (!catalogPath) fail('PROFILE_CATALOG_REQUIRED');
    catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
  }
  catch { fail('PROFILE_CATALOG_REQUIRED'); }
  if (!Array.isArray(catalog.tools)) fail('INVALID_PROVIDER');
  const record = catalog.tools.find(item => item.id === tool);
  if (!record) fail('UNKNOWN_TOOL');
  if (!record.accessProfile) fail('PROFILE_NOT_VERIFIED');
  const file = path.resolve(skillsRoot, '..', record.accessProfile);
  if (!file.startsWith(skillsRoot + path.sep)) fail('INVALID_PROVIDER');
  let canonical;
  try { canonical = fs.realpathSync(file); } catch { fail('PROFILE_NOT_VERIFIED'); }
  if (!canonical.startsWith(skillsRoot + path.sep)) fail('INVALID_PROVIDER');
  const profile = JSON.parse(fs.readFileSync(canonical, 'utf8'));
  if (profile.tool !== tool || profile.owner !== record.owner || profile.schemaVersion !== 1) fail('INVALID_PROVIDER');
  return profile;
}
function resolveTrackingBridgePath(skillsRoot, allowLegacyRoot) {
  const canonicalRoot = path.join(skillsRoot, 'observability/tracking-lifecycle/cli/src');
  const legacyRoot = path.join(skillsRoot, 'tracking-lifecycle/cli/src');
  // A present categorized tree is authoritative, even when its bridge is
  // incomplete. Only an explicitly selected legacy installation may fall back.
  const root = fs.existsSync(canonicalRoot) ? canonicalRoot : allowLegacyRoot ? legacyRoot : undefined;
  if (!root) fail('PROFILE_NOT_VERIFIED');
  try {
    const realSkillsRoot = fs.realpathSync(skillsRoot);
    const realRoot = fs.realpathSync(root);
    const realModule = fs.realpathSync(path.join(root, 'remote-bridge.js'));
    if (!realRoot.startsWith(realSkillsRoot + path.sep)
      || !realModule.startsWith(realRoot + path.sep)) throw new Error('tracking bridge escaped skills root');
    return realModule;
  } catch { fail('PROFILE_NOT_VERIFIED'); }
}
async function serve({ tool, expectedSubject, env = process.env, port = 19826, ttlMs,
  session, profile: browserProfile, tab, applicationId, tenantId, transport = 'native', allowWrites = false, allowedOperations = [] } = {}) {
  const skillsRoot = resolveSkillsRoot(env.SAAS_SKILLS_ROOT);
  const profile = loadProfile(tool, { skillsRoot, allowLegacyCatalog: env.SAAS_SKILLS_ROOT !== undefined });
  if (!['native', 'browser'].includes(transport)) fail('INVALID_PROVIDER');
  if (profile.provider === 'tracking-session') {
    const bridgePath = resolveTrackingBridgePath(skillsRoot, env.SAAS_SKILLS_ROOT !== undefined);
    const { startBridge } = require(bridgePath);
    return startBridge({ origin: profile.origin, env, port, ttlMs, expectedSubject, applicationId,
      session, profile: browserProfile, tab, allowWrites });
  }
  let provider;
  if (transport === 'browser') {
    if (!['cookie', 'storage'].includes(profile.browserAuthentication?.type)) fail('BROWSER_CONTRACT_REQUIRED');
    provider = browserProvider({ origin: profile.origin, apiOrigin: profile.apiOrigin, authentication: profile.browserAuthentication, allowWrites,
      readPostPaths: [...Object.values(profile.operations).filter(op => op.effect === 'read' && op.method === 'POST').map(op => op.path), ...(profile.identity.method === 'POST' ? [profile.identity.path] : [])],
      session, profile: browserProfile, tab, env });
  } else if (profile.provider === 'superset-service') provider = supersetProvider({ origin: profile.origin, env });
  else if (profile.provider === 'bearer-env') provider = bearerProvider({ origin: profile.apiOrigin || profile.origin, tokenEnv: profile.tokenEnv, scheme: profile.tokenScheme || 'bearer', env });
  else if (profile.provider === 'device') {
    // An approved platform profile must supply discovered endpoints and target token acceptance evidence.
    if (!profile.device?.targetTokenAcceptance || !env[profile.device.clientIdEnv]) fail('UNSUPPORTED_DEVICE_GRANT');
    provider = deviceProvider({ ...profile.device, origin: profile.origin, clientId: env[profile.device.clientIdEnv] });
  } else fail('PROFILE_NOT_VERIFIED');
  if (!Array.isArray(allowedOperations) || allowedOperations.some(name => typeof name !== 'string' || !Object.hasOwn(profile.operations, name))
    || allowWrites && !allowedOperations.length) fail('INVALID_WRITE_SCOPE');
  return createGateway({ origin: profile.origin, expectedSubject, port, ttlMs,
    allowWrites, scope: { ...(tenantId ? { tenantId } : {}), allowedOperations }, adapter: nativeAdapter({ provider, profile }) });
}
module.exports = { loadProfile, serve, createGateway, createClient, nativeAdapter,
  bearerProvider, supersetProvider, deviceProvider, resolveTrackingBridgePath };

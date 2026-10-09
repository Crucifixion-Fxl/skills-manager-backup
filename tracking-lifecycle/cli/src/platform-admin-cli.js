'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { ContractError } = require('./contracts');
const { sourceCommit, operations, planOperation, publicPlan, AdministrativeClient } = require('./platform-admin');
const { webAccessRuntimeModule } = require('./web-access-runtime');

function privateSink(file) {
  // Reserve the file before network I/O. Never overwrite an existing secret.
  let parent; const directory = path.dirname(path.resolve(file));
  try { parent = fs.lstatSync(path.dirname(path.resolve(file))); }
  catch { throw new ContractError('INVALID_PRIVATE_OUTPUT', 'private response needs an existing owner-only directory'); }
  if (!parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== process.getuid?.() || fs.realpathSync(directory) !== directory || (parent.mode & 0o077)) {
    throw new ContractError('INVALID_PRIVATE_OUTPUT', 'private response needs an existing owner-only directory');
  }
  let descriptor;
  try { descriptor = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600); }
  catch { throw new ContractError('INVALID_PRIVATE_OUTPUT', 'private destination must be a new regular file'); }
  let saved = false;
  return {
    save(data) { fs.writeFileSync(descriptor, JSON.stringify(data)); fs.fsyncSync(descriptor); saved = true; },
    close() {
      let failed = false;
      try { fs.closeSync(descriptor); } catch { failed = true; }
      if (!saved) { try { fs.unlinkSync(file); } catch { failed = true; } }
      if (failed) throw new ContractError('PRIVATE_OUTPUT_CLEANUP_FAILED',
        'private response cleanup failed; inspect local custody and API state before repeating');
    },
  };
}

async function runAdministrative(command, args, env, writeOutput, browserTransport) {
  if (command === 'api-catalog') {
    writeOutput({ sourceCommit, operations }, args);
    return;
  }
  let input;
  try { input = JSON.parse(fs.readFileSync(args.request, 'utf8')); }
  catch { throw new ContractError('INVALID_API_INPUT', '--request must name a readable JSON request file'); }
  const plan = planOperation(args.operation, input);
  if (command === 'api-plan') { writeOutput(publicPlan(plan), args); return; }
  if (command !== 'api-call') throw new ContractError('UNKNOWN_COMMAND', 'unknown administrative command');
  if (args['token-file']) {
    const { loadProfile } = webAccessRuntimeModule('index.js');
    const { credentialEnvironment } = webAccessRuntimeModule('token-consumer.js');
    const loaded = credentialEnvironment({ profile: loadProfile('tracking', { skillsRoot: env.SAAS_SKILLS_ROOT }), secretFile: args['token-file'],
      expectedSubject: args['expected-subject'], applicationId: args['application-id'] });
    const auth = loaded.issuer === 'project' ? 'project' : 'pat';
    if (args.auth && args.auth !== auth || env.TRACKING_PLATFORM_BASE_URL && env.TRACKING_PLATFORM_BASE_URL !== loaded.origin)
      throw new ContractError('TOKEN_SCOPE_MISMATCH', 'credential origin and authentication type must match');
    if (loaded.issuer === 'project') {
      if (plan.method !== 'GET') throw new ContractError('PROJECT_TOKEN_READ_ONLY', 'project token permits GET requests only');
      // The fixed backend stores project.appId but does not enforce it on all routes.
      // Bind the actual operation and response using the existing bridge contract.
      const { bearerProvider } = webAccessRuntimeModule('providers.js');
      const { createTrackingAdapter } = require('./auth-adapter');
      const provider = bearerProvider({ origin: loaded.origin, getToken: () => loaded.environment.TMT_TOKEN });
      const adapter = createTrackingAdapter({ applicationId: loaded.resource, transport: {
        request: async (method, route, body) => {
          const payload = await provider.request(method, route, body);
          if (payload?.success !== true) throw new ContractError('API_REQUEST_FAILED', 'platform request failed');
          return payload.data;
        },
      } });
      const execute = adapter.prepare('/v1/request', { operation: args.operation, input }, { allowWrites: false });
      const probe = await provider.request('GET', '/api/role/current');
      if (probe?.success !== true || String(probe.data?.userId) !== args['expected-subject'])
        throw new ContractError('IDENTITY_CHANGED', 'platform token identity changed');
      writeOutput({ sourceCommit, operation: args.operation, data: await execute() }, args);
      return;
    }
    args = { ...args, auth };
    env = { ...env, ...loaded.environment, TRACKING_PLATFORM_BASE_URL: loaded.origin, TMT_SESSION_COOKIE: undefined, TMT_MCP_API_KEY: undefined };
  }
  let sink, result, failure;
  try {
    const client = new AdministrativeClient({ baseUrl: env.TRACKING_PLATFORM_BASE_URL,
      auth: args.auth || 'project', token: env.TMT_TOKEN,
      cookie: env.TMT_SESSION_COOKIE, apiKey: env.TMT_MCP_API_KEY, browserTransport });
    // Validate auth/workflow first; avoid touching files for denied operations.
    if (plan.secretResponse && args['private-output']) {
      sink = privateSink(args['private-output']);
      client.secretSink = sink.save;
    }
    result = await client.call(args.operation, input);
  } catch (error) { failure = error; throw error; }
  finally {
    if (sink) {
      try { sink.close(); }
      catch (error) { if (!failure) throw error; }
    }
  }
  writeOutput(result, args);
}

module.exports = { runAdministrative, privateSink };

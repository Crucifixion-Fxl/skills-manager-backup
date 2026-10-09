'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { fail, plain, httpsOrigin } = require('./gateway');

function adapterSource(profile) {
  if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(profile.tool || '') || !plain(profile.operations)) fail('INVALID_PROFILE');
  httpsOrigin(profile.origin);
  const definitions = Object.entries(profile.operations).filter(([, op]) => ['read', 'write'].includes(op.effect) && (op.effect === 'read' || op.writeContract?.kind === 'crud')
    && !op.workflow && !op.secretResponse).map(([name, operation]) => {
    if (!/^[a-z][a-z0-9-]{0,79}$/.test(name)) fail('INVALID_PROFILE');
    return { name, operation, description: `${profile.tool} ${name}: ${operation.method || 'GET'} ${operation.path}` };
  });
  if (!definitions.length) fail('NO_REGISTERED_READS');
  const clientPath = path.join(__dirname, 'client.js');
  const nativePath = path.join(__dirname, 'native-adapter.js');
  return `// Generated from the SaaS owner profile; reinstall after source or Skill location changes.
import { cli, Strategy } from '@jackwener/opencli/registry';
import { CliError } from '@jackwener/opencli/errors';
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(import.meta.url);
const { createClient } = require(${JSON.stringify(clientPath)});
const { nativeAdapter } = require(${JSON.stringify(nativePath)});
const profile = ${JSON.stringify(profile)};
const definitions = ${JSON.stringify(definitions)};
for (const def of definitions) cli({
  site: profile.tool, name: def.name, description: def.description,
  strategy: Strategy.LOCAL, browser: false, access: def.operation.effect, defaultFormat: 'json',
  args: [
    { name: 'input-file', type: 'string', default: '', help: 'JSON object with registered input values; no credentials' },
    { name: 'credentials-file', type: 'string', default: '', help: 'Private bridge lease file; defaults to SAAS_AUTH_CREDENTIALS_FILE' },
  ], columns: ['command', 'result'],
  func: async args => {
    try {
      let input = {};
      if (args['input-file']) { try { input = JSON.parse(fs.readFileSync(args['input-file'], 'utf8')); } catch { throw { code: 'INVALID_INPUT' }; } }
      const client = createClient({ file: args['credentials-file'] || process.env.SAAS_AUTH_CREDENTIALS_FILE, origin: profile.origin });
      // Validate against this adapter's source snapshot as well as the live server profile.
      const status = await client.status();
      nativeAdapter({ profile, provider: {} }).prepare('/v1/request', { operation: def.name, input }, { scope: status, allowWrites: status.writesEnabled });
      const result = await client.call('/v1/request', { operation: def.name, input });
      return [{ command: def.name, result }];
    } catch (error) {
      const code = /^[A-Z][A-Z0-9_]{1,70}$/.test(error.code || '') ? error.code : 'SAAS_EXECUTION_FAILED';
      throw new CliError(code, 'SaaS command failed; check input, login and platform permissions');
    }
  },
});
`;
}

function installAdapter({ profile, root = path.join(os.homedir(), '.opencli/clis'), replace = false } = {}) {
  if (!path.isAbsolute(root)) fail('INVALID_INPUT');
  const source = adapterSource(profile);
  const directory = path.join(root, profile.tool);
  fs.mkdirSync(directory, { recursive: true });
  if (fs.realpathSync(directory) !== directory) fail('INVALID_ADAPTER_PATH');
  const file = path.join(directory, profile.tool + '.js');
  const exists = fs.existsSync(file);
  if (exists && fs.lstatSync(file).isSymbolicLink()) fail('INVALID_ADAPTER_PATH');
  if (exists && fs.readFileSync(file, 'utf8') !== source && !replace) fail('ADAPTER_EXISTS');
  if (!exists || fs.readFileSync(file, 'utf8') !== source) fs.writeFileSync(file, source, { flag: exists ? 'w' : 'wx', mode: 0o600 });
  // OpenCLI dynamically imports JS adapters; scoped ESM metadata avoids depending on parent package mode.
  const metadata = path.join(directory, 'package.json');
  if (!fs.existsSync(metadata)) fs.writeFileSync(metadata, '{"private":true,"type":"module"}\n', { flag: 'wx', mode: 0o600 });
  else {
    if (fs.lstatSync(metadata).isSymbolicLink()) fail('INVALID_ADAPTER_PATH');
    try { if (JSON.parse(fs.readFileSync(metadata, 'utf8')).type !== 'module') fail('INVALID_ADAPTER_PATH'); }
    catch { fail('INVALID_ADAPTER_PATH'); }
  }
  return { file, tool: profile.tool, operations: Object.values(profile.operations).filter(op => ['read', 'write'].includes(op.effect) && (op.effect === 'read' || op.writeContract?.kind === 'crud') && !op.workflow && !op.secretResponse).length };
}
module.exports = { installAdapter, adapterSource };

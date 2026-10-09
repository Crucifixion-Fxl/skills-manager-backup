'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { ContractError } = require('./contracts');

async function runHook(config, name, root) {
  if (!['usage', 'local', 'sandbox', 'staging', 'artifact', 'metric'].includes(name)) {
    throw new ContractError('INVALID_HOOK', 'unknown or privileged hook');
  }
  const hook = config?.hooks?.[name];
  if (config?.schemaVersion !== 1 || !hook || !Array.isArray(hook.argv) || !hook.argv.length ||
      !hook.argv.every(arg => typeof arg === 'string' && arg.length) ||
      !Array.isArray(hook.outputs) || !hook.outputs.length) {
    throw new ContractError('INVALID_HOOK', `${name} needs argv and outputs`);
  }
  const resolvedRoot = fs.realpathSync(root);
  const outputs = hook.outputs.map(output => {
    if (typeof output !== 'string' || path.isAbsolute(output)) throw new ContractError('INVALID_HOOK', 'output must be relative');
    const resolved = path.resolve(resolvedRoot, output);
    if (!resolved.startsWith(`${resolvedRoot}${path.sep}`)) throw new ContractError('INVALID_HOOK', 'output escapes project root');
    return resolved;
  });
  const status = await new Promise((resolve, reject) => {
    const child = spawn(hook.argv[0], hook.argv.slice(1), { cwd: resolvedRoot, stdio: 'inherit', shell: false });
    child.on('error', reject);
    child.on('exit', (code, signal) => resolve({ code, signal }));
  });
  if (status.code !== 0) throw new ContractError('HOOK_FAILED', `${name}: exit ${status.code}, signal ${status.signal || 'none'}`);
  for (const output of outputs) {
    if (!fs.existsSync(output) || !fs.statSync(output).isFile() || fs.statSync(output).size === 0) {
      throw new ContractError('HOOK_OUTPUT_MISSING', `${name}: ${output}`);
    }
  }
  return { status: 'PASS', hook: name, outputs: outputs.map(output => path.relative(resolvedRoot, output)) };
}

module.exports = { runHook };

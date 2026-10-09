'use strict';
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const path = require('node:path');
const { fail, plain } = require('./gateway');
const run = promisify(execFile);
async function agentReachDoctor({ executable = process.env.AGENT_REACH_BIN || 'agent-reach', runner = run } = {}) {
  if (executable !== 'agent-reach' && (!path.isAbsolute(executable) || /[\x00-\x1f]/.test(executable))) fail('INVALID_TOOL_PATH');
  let output;
  try { output = await runner(executable, ['doctor', '--json'], { timeout: 45000, maxBuffer: 2 * 1024 * 1024, encoding: 'utf8' }); }
  catch (error) { if (error.code === 'ENOENT') return { state: 'NOT_INSTALLED', project: 'Panniantong/Agent-Reach', channels: [], taskVerification: 'pending' }; fail('AGENT_REACH_DIAGNOSTIC_FAILED'); }
  let parsed; try { parsed = JSON.parse(output.stdout); } catch { fail('AGENT_REACH_DIAGNOSTIC_FAILED'); }
  if (!plain(parsed) || Object.keys(parsed).length > 100) fail('AGENT_REACH_DIAGNOSTIC_FAILED');
  const backend = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(value) ? value : null;
  const channels = Object.entries(parsed).map(([name, item]) => {
    if (!/^[a-z][a-z0-9_-]{0,79}$/.test(name) || !plain(item) || !['ok', 'warn', 'off', 'error', 'unavailable', 'disabled'].includes(item.status)) fail('AGENT_REACH_DIAGNOSTIC_FAILED');
    return { name, status: item.status, activeBackend: backend(item.active_backend), backends: Array.isArray(item.backends) ? item.backends.map(backend).filter(Boolean) : [] };
  });
  // Messages, diagnostics, credential paths and arbitrary upstream fields never enter the model result.
  return { state: 'DIAGNOSED', project: 'Panniantong/Agent-Reach', channels, taskVerification: 'pending' };
}
module.exports = { agentReachDoctor };

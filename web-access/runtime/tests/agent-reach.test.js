'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { agentReachDoctor } = require('../src/agent-reach');
test('Agent Reach diagnostic consumes its actual JSON shape and exposes only safe channel metadata', async () => {
  const calls = [];
  const result = await agentReachDoctor({ runner: async (file, args) => { calls.push({ file, args }); return { stdout: JSON.stringify({ reddit: { status: 'ok', name: 'Reddit', message: 'secret-cookie-value', tier: 2, active_backend: 'opencli', backends: ['opencli', 'rdt-cli'] } }) }; } });
  assert.deepEqual(calls[0].args, ['doctor', '--json']);
  assert.equal(result.channels[0].activeBackend, 'opencli');
  assert.equal(result.taskVerification, 'pending');
  assert.equal(JSON.stringify(result).includes('secret-cookie-value'), false);
});
test('missing, malformed and failed Agent Reach diagnostics never leak raw tool output', async () => {
  assert.equal((await agentReachDoctor({ runner: async () => { throw { code: 'ENOENT' }; } })).state, 'NOT_INSTALLED');
  await assert.rejects(agentReachDoctor({ runner: async () => ({ stdout: 'opaque-secret' }) }), e => e.code === 'AGENT_REACH_DIAGNOSTIC_FAILED');
  await assert.rejects(agentReachDoctor({ runner: async () => { throw new Error('opaque-secret'); } }), e => e.code === 'AGENT_REACH_DIAGNOSTIC_FAILED' && !e.message.includes('opaque-secret'));
});

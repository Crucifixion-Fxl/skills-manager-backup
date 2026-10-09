'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { AdministrativeClient } = require('../cli/src/platform-admin');
const { ContractError } = require('../cli/src/contracts');
test('public bridge API preserves recovery and scope refusals while sanitizing error text', async () => {
  for (const code of ['AUTH_REQUIRED', 'API_PERMISSION_DENIED', 'AUTH_IDENTITY_MISMATCH', 'RESOURCE_MISMATCH', 'UNSCOPED_OPERATION', 'EXPIRED', 'REVOKED', 'BRIDGE_BUSY']) {
    const client = new AdministrativeClient({ baseUrl: 'https://fixture.example', auth: 'bridge', browserTransport: { origin: 'https://fixture.example', send: async () => { throw new ContractError(code, 'untrusted-private-diagnostic'); } } });
    await assert.rejects(client.call('context.list', { query: { applicationId: 14 } }), e => e.code === code && !e.message.includes('untrusted-private-diagnostic'));
  }
  const client = new AdministrativeClient({ baseUrl: 'https://fixture.example', auth: 'bridge', browserTransport: { origin: 'https://fixture.example', send: async () => { throw Error('untrusted-private-diagnostic'); } } });
  await assert.rejects(client.call('context.list', { query: { applicationId: 14 } }), e => e.code === 'API_REQUEST_FAILED' && !e.message.includes('untrusted-private-diagnostic'));
});

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { planAccess } = require('../src/access-plan');
const { profileRoot, readOwnerProfile } = require('./profile-fixtures');
test('unknown websites share local and SSH handoff planning without inventing platform authentication', () => {
  const local = planAccess({ url: 'https://unknown.example/challenge?token=hidden', retention: 'once' });
  assert.equal(local.state, 'PLANNED'); assert.equal(local.platformContract, 'unknown');
  assert.equal(local.connection, 'visible-local-browser');
  assert.equal(JSON.stringify(local).includes('hidden'), false);
  const remote = planAccess({ url: 'https://unknown.example/', agentLocation: 'remote', browserLocation: 'local', sshTarget: 'naturehood' });
  assert.equal(remote.connection, 'companion-ssh-reverse');
  const view = planAccess({ url: 'https://unknown.example/', agentLocation: 'remote', browserLocation: 'remote', sshTarget: 'naturehood' });
  assert.equal(view.connection, 'novnc-ssh-local'); assert.equal(view.userInterface, 'vnc');
  assert.equal(view.authenticationVerified, false);
});
test('known owner origin is fixed and missing remote host or mismatched origin fails', t => {
  const skillsRoot = profileRoot(t, [readOwnerProfile('skills/business-operations/addx-console-admin/references/auth-profile.json')]);
  const known = planAccess({ tool: 'console', skillsRoot });
  assert.equal(known.origin, 'https://console.addx.live'); assert.equal(known.platformContract, 'source-verified');
  assert.throws(() => planAccess({ tool: 'console', url: 'https://foreign.example/', skillsRoot }), e => e.code === 'BRIDGE_ORIGIN_MISMATCH');
  assert.throws(() => planAccess({ url: 'https://unknown.example/', agentLocation: 'remote' }), e => e.code === 'SSH_TARGET_REQUIRED');
  assert.throws(() => planAccess({ url: 'https://user:password@unknown.example/' }), e => e.code === 'INVALID_ENDPOINT');
});

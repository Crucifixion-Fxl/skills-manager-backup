'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const repo = path.resolve(__dirname, '../../../../..');
test('canonical access CLI resolves installed owner profiles without obsolete Skill or command aliases', () => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-entrypoint-'));
  const skillsRoot = path.join(fixture, 'skills');
  fs.mkdirSync(path.join(skillsRoot, 'platform-onboarding/references'), { recursive: true });
  fs.mkdirSync(path.join(skillsRoot, 'fixture/references'), { recursive: true });
  fs.writeFileSync(path.join(skillsRoot, 'fixture/references/auth-profile.json'), JSON.stringify({
    schemaVersion: 1, tool: 'fixture', owner: 'fixture', provider: 'bearer-env', origin: 'https://fixture.invalid',
    tokenEnv: 'FIXTURE_TOKEN', identity: { path: '/me', subjectPath: 'id' }, operations: {},
  }));
  fs.writeFileSync(path.join(skillsRoot, 'platform-onboarding/references/tools.json'), JSON.stringify({
    tools: [{ id: 'fixture', owner: 'fixture', accessProfile: 'skills/fixture/references/auth-profile.json' }],
  }));
  const bin = path.join(repo, 'skills/agent-harness/web-access/runtime/bin');
  const result = spawnSync(process.execPath, [path.join(bin, 'web-access.js'), 'describe', '--tool=fixture'], { encoding: 'utf8', env: { ...process.env, SAAS_SKILLS_ROOT: skillsRoot } });
  fs.rmSync(fixture, { recursive: true, force: true });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).tool, 'fixture');
  for (const name of ['saas-auth.js', 'saas-view.js', 'saas-onboard.js']) assert.equal(fs.existsSync(path.join(bin, name)), false);
  for (const name of ['saas-auth', 'remote-web-session', 'saas-opencli-onboarding']) assert.equal(fs.existsSync(path.join(repo, 'skills', name, 'SKILL.md')), false);
  assert.deepEqual(Object.keys(require('../package.json').bin).sort(), ['platform-onboard', 'web-access', 'web-view']);
});

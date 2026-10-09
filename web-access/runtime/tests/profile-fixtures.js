'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function profileRoot(t, profiles) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-profiles-'));
  const skillsRoot = path.join(temp, 'skills');
  const catalogRoot = path.join(skillsRoot, 'agent-harness/platform-onboarding/references');
  fs.mkdirSync(catalogRoot, { recursive: true });
  const tools = [];
  for (const profile of profiles) {
    const file = path.join(skillsRoot, profile.owner, 'references/auth-profile.json');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(profile, null, 2));
    tools.push({ id: profile.tool, owner: profile.owner, accessProfile: `skills/${profile.owner}/references/auth-profile.json` });
  }
  fs.writeFileSync(path.join(catalogRoot, 'tools.json'), JSON.stringify({ tools }, null, 2));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  return skillsRoot;
}

function readOwnerProfile(relativeFile) {
  return JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../..', relativeFile), 'utf8'));
}

function cmsDraftProfile(origin = 'https://cms.fixture.invalid') {
  const get = { effect: 'read', path: '/api/color-categories/{id}', parameters: { id: { type: 'string', required: true } },
    query: { draft: { type: 'boolean', default: false } } };
  return { schemaVersion: 1, tool: 'marketing-cms', owner: 'marketing-cms', provider: 'bearer-env', origin,
    tokenEnv: 'MARKETING_CMS_TOKEN', identity: { path: '/api/users/me', subjectPath: 'user.email' }, permissionProbe: '/api/access',
    operations: {
      me: { effect: 'read', path: '/api/users/me' }, access: { effect: 'read', path: '/api/access' },
      'color-categories-get': get,
      'color-categories-draft-create': { effect: 'write', method: 'POST', path: '/api/color-categories',
        query: { draft: { type: 'boolean', default: true } },
        body: { color_class: { type: 'string', required: true }, display_name: { type: 'string', required: true }, _status: { type: 'string', enum: ['draft'], default: 'draft' } },
        writeContract: { kind: 'crud', resultProbe: { operation: 'color-categories-get', idPath: 'doc.id', inputName: 'id', matches: { display_name: 'body.display_name' } } } },
      'color-categories-draft-update': { effect: 'write', method: 'PATCH', path: '/api/color-categories/{id}',
        parameters: { id: { type: 'string', required: true } }, query: { draft: { type: 'boolean', default: true } },
        body: { display_name: { type: 'string', required: true }, _status: { type: 'string', enum: ['draft'] } },
        writeContract: { kind: 'crud', preflight: { operation: 'color-categories-get', input: { id: 'id' }, expected: { _status: 'draft' } },
          resultProbe: { operation: 'color-categories-get', input: { id: 'id' }, matches: { display_name: 'body.display_name' } } } },
      'color-categories-unpublished-delete': { effect: 'write', method: 'DELETE', path: '/api/color-categories/{id}',
        parameters: { id: { type: 'string', required: true } }, workflow: 'CMS lifecycle must verify both draft and published records' },
    } };
}

module.exports = { profileRoot, readOwnerProfile, cmsDraftProfile };

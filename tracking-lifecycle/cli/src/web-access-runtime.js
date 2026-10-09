'use strict';

const fs = require('node:fs');
const path = require('node:path');

function webAccessRuntimeModule(name) {
  if (!/^[a-z][a-z0-9-]*\.js$/.test(name || '')) {
    const error = new Error('invalid runtime module'); error.code = 'WEB_ACCESS_RUNTIME_UNAVAILABLE'; throw error;
  }
  const skillsRoot = findSkillsRoot();
  const canonicalRoot = path.join(skillsRoot, 'agent-harness/web-access/runtime/src');
  const legacyRoot = path.join(skillsRoot, 'web-access/runtime/src');

  // Once the categorized runtime directory exists, it is authoritative. A
  // missing or escaped module must fail closed rather than silently falling
  // back to a different installation tree.
  const root = fs.existsSync(canonicalRoot) ? canonicalRoot : legacyRoot;
  try {
    const realSkillsRoot = fs.realpathSync(skillsRoot);
    const realRoot = fs.realpathSync(root);
    const realModule = fs.realpathSync(path.join(root, name));
    if (!realRoot.startsWith(realSkillsRoot + path.sep)
      || !realModule.startsWith(realRoot + path.sep)) throw new Error('runtime path escaped its skills root');
    return require(realModule);
  } catch {
    const error = new Error('the installed web-access runtime is unavailable');
    error.code = 'WEB_ACCESS_RUNTIME_UNAVAILABLE';
    throw error;
  }
}

function findSkillsRoot() {
  // Current categorized callers are four levels below skills/; legacy flat
  // callers are three. Accept only those fixed ancestor positions.
  for (const levels of [4, 3]) {
    const candidate = path.resolve(__dirname, '../'.repeat(levels));
    try {
      if (path.basename(candidate) === 'skills' && fs.statSync(candidate).isDirectory()) return candidate;
    } catch { /* try the other known physical layout */ }
  }
  const error = new Error('tracking lifecycle must be installed beneath a skills directory');
  error.code = 'WEB_ACCESS_RUNTIME_UNAVAILABLE';
  throw error;
}

module.exports = { webAccessRuntimeModule };

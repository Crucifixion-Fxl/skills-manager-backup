const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const YAML = require('yaml');

test('GitLab template protects API release and retains the same artifact chain', () => {
  const source = fs.readFileSync(path.join(__dirname, '../ci/gitlab/tracking.yml'), 'utf8');
  const doc = YAML.parseDocument(source, { uniqueKeys: true });
  assert.deepEqual(doc.errors, []);
  const jobs = doc.toJS();
  const names = ['tracking:local', 'tracking:platform-readback', 'tracking:sandbox',
    'tracking:staging', 'tracking:publish'];
  for (let i = 1; i < names.length; i++) {
    assert.equal(jobs[names[i]].needs[0].job, names[i - 1]);
  }
  for (const name of names.slice(1)) {
    assert.match(jobs[name].rules[0].if, /CI_COMMIT_REF_PROTECTED/);
    assert.match(jobs[name].rules[0].if, /merge_request_event/);
    assert.match(jobs[name].rules[0].if, /CI_COMMIT_BRANCH == \$TRACKING_RELEASE_REF/);
  }
  assert.equal(jobs['tracking:publish'].environment.name, 'tracking-api');
  assert.equal(jobs['tracking:platform-readback'].environment.name, 'tracking-api');
  assert.equal(jobs['tracking:local'].environment, undefined);
  assert.equal(jobs['tracking:sandbox'].environment, undefined);
  assert.equal(jobs['tracking:staging'].environment, undefined);
  assert.equal(jobs['tracking:metric'].needs[0].job, 'tracking:staging');
  assert.equal(jobs['tracking:metric'].environment.name, 'tracking-metric');
  assert.match(jobs['tracking:metric'].rules[0].if, /TRACKING_M2_ENABLED/);
  assert.equal(jobs['tracking:publish'].needs[1].job, 'tracking:metric');
  assert.equal(jobs['tracking:publish'].needs[1].optional, true);
  assert.match(jobs['tracking:publish'].script.join(' '), /publish .*--artifact-file=/);
  assert.doesNotMatch(jobs['tracking:publish'].script.join(' '), /run-hook/);
  assert.match(jobs['tracking:platform-readback'].script.join(' '), /readback-platform/);
  assert.doesNotMatch(jobs['tracking:platform-readback'].script.join(' '), / post /);
  assert.match(source, /TRACKING_SKILL_REF/);
});

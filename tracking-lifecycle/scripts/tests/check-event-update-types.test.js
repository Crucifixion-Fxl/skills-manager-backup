const assert = require('node:assert/strict');
const { test } = require('node:test');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { diffUpdate } = require('../check-event-update');
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'test-event-update', name), 'utf8'));

test('corrected full update passes and dropped parameters remain blocked', () => {
  const current = fixture('current.json');
  const good = diffUpdate(current, fixture('payload-good.json'));
  assert.deepEqual(good.droppedParams, []);
  assert.deepEqual(good.invalidTypeParams, []);
  assert.equal(good.idMismatch, false);
  assert.equal(diffUpdate(current, { ...fixture('payload-good.json'), id: 999 }).idMismatch, true);
  assert.equal(diffUpdate(current, { ...fixture('payload-good.json'), id: undefined }).idMismatch, true);
  assert.deepEqual(diffUpdate(current, fixture('payload-bad.json')).droppedParams, ['source', 'amount']);
});

test('all canonical names pass without conversion', () => {
  const payload = { id: 1, parameters: ['null', 'boolean', 'object', 'array', 'number', 'string', 'integer']
    .map(valueType => ({ name: valueType, valueType })) };
  assert.deepEqual(diffUpdate({ id: 1 }, payload).invalidTypeParams, []);
});

test('digit codes, float, case, whitespace and missing types are blocked', () => {
  for (const valueType of ['0', '1', '2', '3', '4', '5', '6', 5, 'float', 'STRING', ' string ', 'date', '', null, undefined]) {
    const result = diffUpdate({ id: 1 }, { id: 1, parameters: [{ name: 'sample', valueType }] });
    assert.deepEqual(result.invalidTypeParams, [{ index: 0, name: 'sample' }]);
  }
  assert.deepEqual(diffUpdate({ id: 1 }, { id: 1, parameters: [null] }).invalidTypeParams,
    [{ index: 0, name: null }]);
});

test('CLI refuses a full request with digit types and still accepts correct fixture', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tracker-type-test-'));
  try {
    const payload = fixture('payload-good.json');
    payload.parameters[0].valueType = '5';
    const invalid = path.join(directory, 'invalid.json');
    fs.writeFileSync(invalid, JSON.stringify(payload));
    const command = path.join(__dirname, '../check-event-update.js');
    const current = path.join(__dirname, 'test-event-update/current.json');
    const failure = spawnSync(process.execPath, [command, `--current=${current}`, `--payload=${invalid}`], { encoding: 'utf8' });
    assert.equal(failure.status, 1);
    assert.equal(JSON.parse(failure.stdout).result, 'FAIL');
    assert.deepEqual(JSON.parse(failure.stdout).invalidTypeParams, [{ index: 0, name: 'source' }]);
    const success = spawnSync(process.execPath, [command, `--current=${current}`,
      `--payload=${path.join(__dirname, 'test-event-update/payload-good.json')}`], { encoding: 'utf8' });
    assert.equal(success.status, 0);
    assert.equal(JSON.parse(success.stdout).result, 'PASS');
  } finally {
    fs.rmSync(directory, { recursive: true });
  }
});

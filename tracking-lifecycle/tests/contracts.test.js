const test = require('node:test');
const assert = require('node:assert/strict');
const {
  parseContract,
  eventKey,
  digest,
  project,
  planAgainstCurrent,
  ContractError,
} = require('../cli/src/contracts');

const baseEvent = {
  type: 'COMPONENT', trackerType: 'CLK',
  spm: ['nature_feeds', 'ntb_naming', 'submit'],
  point: 'submit',
  parameters: [{ name: 'source', valueType: 'string', isRequired: true }],
};
const baseline = {
  schemaVersion: 1, kind: 'baseline', host: 'vicohome',
  application: 'smart_camera', applicationId: 14,
  release: { id: 77, version: '1-0-6', status: 3 }, events: [baseEvent],
};
const identity = { type: 'COMPONENT', trackerType: 'CLK', spm: [...baseEvent.spm] };
const change = (issue, field, after) => ({
  schemaVersion: 1, kind: 'change', issue: String(issue), host: 'vicohome',
  applicationId: 14, baseDigest: digest(baseline.events),
  operations: [{ event: identity, field, before: { exists: false }, after }],
});

test('strict YAML rejects duplicate keys and implicit date conversion', () => {
  assert.throws(() => parseContract('kind: change\nkind: baseline\n'), ContractError);
  assert.equal(parseContract('version: 2026-10-02\n').version, '2026-10-02');
});

test('identity includes application, full SPM, type and tracker type', () => {
  assert.notEqual(eventKey('smart_camera', baseEvent), eventKey('smart_camera', {
    ...baseEvent, spm: ['other_page', 'ntb_naming', 'submit'],
  }));
  assert.notEqual(eventKey('smart_camera', baseEvent), eventKey('other_app', baseEvent));
  assert.notEqual(eventKey('smart_camera', baseEvent), eventKey('smart_camera', {
    ...baseEvent, trackerType: 'EXP',
  }));
});

test('new event must have a platform-writable identity before projection', () => {
  const cases = [
    { point: 'other' },
    { spm: ['home', 'submit'], point: 'submit' },
    { type: 'PAGE', trackerType: 'CLK', spm: ['home'], point: 'home' },
    { type: 'MODULE', trackerType: 'BASE', spm: ['home', 'card'], point: 'card' },
    { type: 'COMPONENT', trackerType: 'CLK', spm: ['home', 'card'], point: 'card' },
    { spm: ['Home', 'card', 'submit'] },
    { type: 'SELF_DEFINE', trackerType: 'BASE', spm: ['custom'], point: 'custom', eventName: 'other' },
  ];
  for (const patch of cases) {
    const added = { ...baseEvent, spm: ['home', 'card', 'click'], point: 'click', ...patch };
    const operation = { event: { type: added.type, trackerType: added.trackerType,
      spm: added.spm, ...(added.eventName ? { eventName: added.eventName } : {}) },
    field: '$event', before: { exists: false }, after: added };
    const task = { ...change(101, '$event', added), operations: [operation] };
    assert.throws(() => project(baseline, [task]), e => e.code === 'INVALID_EVENT_IDENTITY',
      JSON.stringify(patch));
  }
});

test('digest ignores ordering and YAML formatting but keeps field semantics', () => {
  const reordered = [{ ...baseEvent, parameters: [...baseEvent.parameters].reverse() }];
  assert.equal(digest(baseline.events), digest(reordered));
  assert.notEqual(digest(baseline.events), digest([{ ...baseEvent, parameters: [{
    name: 'source', valueType: 'number', isRequired: true,
  }] }]));
  assert.notEqual(digest(baseline.events), digest([{ ...baseEvent, parameters: [{
    name: 'source', valueType: 'integer', isRequired: true,
  }] }]));
  assert.notEqual(digest(baseline.events), digest([{ ...baseEvent, parameters: [{
    name: 'source', valueType: 'null', isRequired: true,
  }] }]));
});

test('an omitted base-schema list and an empty platform readback list have the same projection', () => {
  assert.equal(digest([{ ...baseEvent, baseSchemas: [] }]), digest([baseEvent]));
});

test('independent fields from two issues compose regardless of order', () => {
  const a = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: false });
  const b = change(102, 'parameters.entry_surface', { valueType: 'string', isRequired: true });
  const left = project(baseline, [a, b]);
  const right = project(baseline, [b, a]);
  assert.equal(digest(left.events), digest(right.events));
  assert.deepEqual(left.events[0].parameters.map(p => p.name).sort(), ['entry_surface', 'naming_type', 'source']);
});

test('different target values on the same field fail, identical targets are idempotent', () => {
  const a = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: false });
  const b = change(102, 'parameters.naming_type', { valueType: 'integer', isRequired: false });
  assert.throws(() => project(baseline, [a, b]), e => e.code === 'CONTRACT_CONFLICT');
  assert.equal(project(baseline, [a, change(103, a.operations[0].field, a.operations[0].after)]).events[0].parameters.length, 2);
});

test('stale baseline and undeclared platform fields fail closed', () => {
  const a = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: false });
  assert.throws(() => project(baseline, [{ ...a, baseDigest: 'wrong' }]), e => e.code === 'BASELINE_DRIFT');
  const current = { ...baseline, events: [{ ...baseEvent, parameters: [
    ...baseEvent.parameters, { name: 'other_issue', valueType: 'string', isRequired: false },
  ] }] };
  assert.throws(() => planAgainstCurrent(baseline, current, [a]), e => e.code === 'UNOWNED_PLATFORM_CHANGE');
});

test('unchanged platform tree is a valid pending plan and full target is complete', () => {
  const a = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: false });
  assert.equal(planAgainstCurrent(baseline, baseline, [a]).complete, false);
  assert.equal(planAgainstCurrent(baseline, project(baseline, [a]), [a]).complete, true);
});

test('sandbox correction accepts only the exact previous lock and selected field values', () => {
  const first = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: false });
  const revised = change(101, 'parameters.naming_type', { valueType: 'string', isRequired: true });
  const previous = project(baseline, [first]);
  const updated = planAgainstCurrent(baseline, previous, [revised], previous);
  assert.equal(updated.complete, false);
  const foreign = { ...previous, events: [{ ...previous.events[0], parameters: [
    ...previous.events[0].parameters, { name: 'foreign', valueType: 'string', isRequired: false },
  ] }] };
  assert.throws(() => planAgainstCurrent(baseline, foreign, [revised], previous),
    e => e.code === 'PREVIOUS_LOCK_DRIFT');
});

test('released parameter type cannot be changed in a task contract', () => {
  const a = {
    ...change(101, 'parameters.source', { valueType: 'integer', isRequired: true }),
    operations: [{ event: identity, field: 'parameters.source',
      before: { valueType: 'string', isRequired: true },
      after: { valueType: 'integer', isRequired: true } }],
  };
  assert.throws(() => project(baseline, [a]), e => e.code === 'IMMUTABLE_FIELD');
});

test('released event identity and unsupported metadata deletion fail before platform writes', () => {
  for (const [field, before, after] of [
    ['spm', baseEvent.spm, ['other', 'ntb_naming', 'submit']],
    ['point', 'submit', 'renamed'],
    ['type', 'COMPONENT', 'MODULE'],
    ['trackerType', 'CLK', 'EXP'],
  ]) {
    const operation = { event: identity, field, before, after };
    assert.throws(() => project(baseline, [{ ...change(101, field, after), operations: [operation] }]),
      e => e.code === 'IMMUTABLE_FIELD');
  }
  const withDescription = { ...baseline, events: [{ ...baseEvent, description: 'Old' }] };
  const remove = { ...change(101, 'description', { exists: false }),
    baseDigest: digest(withDescription.events),
    operations: [{ event: identity, field: 'description', before: 'Old', after: { exists: false } }] };
  assert.throws(() => project(withDescription, [remove]), e => e.code === 'UNSUPPORTED_FIELD');
});

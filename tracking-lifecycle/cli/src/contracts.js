'use strict';

const crypto = require('node:crypto');
const YAML = require('yaml');

class ContractError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'ContractError';
    this.code = code;
    this.details = details;
  }
}

const clone = value => structuredClone(value);
const stable = value => {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  }
  return value;
};
const equal = (a, b) => JSON.stringify(stable(a)) === JSON.stringify(stable(b));

function parseContract(source) {
  const document = YAML.parseDocument(source, { uniqueKeys: true, version: '1.2', schema: 'core' });
  if (document.errors.length) {
    throw new ContractError('INVALID_YAML', document.errors.map(e => e.message).join('; '));
  }
  const result = document.toJS();
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    throw new ContractError('INVALID_YAML', 'Contract root must be a mapping');
  }
  return result;
}

function eventKey(application, event) {
  if (!application || !event || !['PAGE', 'MODULE', 'COMPONENT', 'SELF_DEFINE'].includes(event.type) ||
      !['BASE', 'CLK', 'EXP'].includes(event.trackerType) || !Array.isArray(event.spm) ||
      !event.spm.every(x => typeof x === 'string' && /^[a-z][a-z0-9_]*$/.test(x))) {
    throw new ContractError('INVALID_EVENT_IDENTITY', 'application, type, trackerType and full spm are required');
  }
  const validHierarchy = (event.type === 'PAGE' && event.trackerType === 'BASE' && event.spm.length === 1) ||
    (event.type === 'MODULE' && ['CLK', 'EXP'].includes(event.trackerType) && event.spm.length === 2) ||
    (event.type === 'COMPONENT' && ['CLK', 'EXP'].includes(event.trackerType) && event.spm.length === 3) ||
    (event.type === 'SELF_DEFINE' && event.trackerType === 'BASE' && event.spm.length === 1 &&
      typeof event.eventName === 'string' && /^[a-z][a-z0-9_]*$/.test(event.eventName));
  if (!validHierarchy) {
    throw new ContractError('INVALID_EVENT_IDENTITY', 'event hierarchy or tracker type does not match platform schema');
  }
  return JSON.stringify([application, event.type, event.trackerType, event.spm, event.eventName || '']);
}

function normalizeEvent(event) {
  if (!event || typeof event !== 'object') throw new ContractError('INVALID_EVENT', 'event must be an object');
  eventKey('_', event);
  if (event.point !== event.spm.at(-1) ||
      (event.type === 'SELF_DEFINE' && event.eventName !== event.point)) {
    throw new ContractError('INVALID_EVENT_IDENTITY', 'point and SELF_DEFINE eventName must match the event path');
  }
  const parameters = event.parameters || [];
  if (!Array.isArray(parameters)) throw new ContractError('INVALID_EVENT', 'parameters must be a list');
  const baseSchemas = event.baseSchemas ?? [];
  if (!Array.isArray(baseSchemas)) throw new ContractError('INVALID_EVENT', 'baseSchemas must be a list');
  const names = new Set();
  const normalized = parameters.map(p => {
    if (!p || typeof p.name !== 'string' || !p.name ||
        !['null', 'boolean', 'object', 'array', 'number', 'string', 'integer'].includes(p.valueType) ||
        typeof p.isRequired !== 'boolean' || names.has(p.name)) {
      throw new ContractError('INVALID_PARAMETER', 'parameter requires unique name, valueType, isRequired');
    }
    names.add(p.name);
    return stable(Object.fromEntries(['name', 'valueType', 'isRequired', 'description', 'trackerType']
      .filter(k => p[k] !== undefined).map(k => [k, p[k]])));
  }).sort((a, b) => a.name.localeCompare(b.name));
  return stable(Object.fromEntries(['type', 'trackerType', 'spm', 'eventName', 'point', 'name', 'description', 'alias', 'category']
    .filter(k => event[k] !== undefined).map(k => [k, clone(event[k])]).concat([
      ['baseSchemas', [...baseSchemas].sort((a, b) => Number(a) - Number(b))], ['parameters', normalized],
    ])));
}

function normalizeEvents(events, application = '_') {
  if (!Array.isArray(events)) throw new ContractError('INVALID_EVENTS', 'events must be a list');
  const seen = new Set();
  return events.map(event => {
    const normalized = normalizeEvent(event);
    const key = eventKey(application, normalized);
    if (seen.has(key)) throw new ContractError('DUPLICATE_EVENT', `duplicate event ${key}`);
    seen.add(key);
    return normalized;
  }).sort((a, b) => eventKey(application, a).localeCompare(eventKey(application, b)));
}

function digest(events) {
  return crypto.createHash('sha256').update(JSON.stringify(normalizeEvents(events))).digest('hex');
}

function assertBaseline(baseline) {
  const released = baseline?.kind === 'baseline' && baseline.release?.status === 3;
  const bootstrap = baseline?.kind === 'bootstrapBaseline' && baseline.release?.status === 0 &&
    Number.isInteger(baseline.release?.id) && baseline.release.id > 0 &&
    /^\d+-\d+-\d+$/.test(baseline.release?.version || '') &&
    Array.isArray(baseline.events) && baseline.events.length === 0;
  if (baseline?.schemaVersion !== 1 || !baseline.host || !baseline.application ||
      !baseline.applicationId || (!released && !bootstrap)) {
    throw new ContractError('INVALID_BASELINE', 'baseline needs a released snapshot or an empty first-release workorder boundary');
  }
  normalizeEvents(baseline.events, baseline.application);
}

function getField(event, field) {
  if (!event) return { exists: false };
  if (field === '$event') return clone(event);
  if (field.startsWith('parameters.')) {
    const name = field.slice('parameters.'.length);
    if (!name || name.includes('.')) throw new ContractError('INVALID_FIELD', field);
    return clone(event.parameters.find(p => p.name === name) || { exists: false });
  }
  if (['type', 'trackerType', 'spm', 'eventName', 'description', 'alias', 'category', 'name', 'point', 'baseSchemas'].includes(field)) {
    return event[field] === undefined ? { exists: false } : clone(event[field]);
  }
  throw new ContractError('INVALID_FIELD', field);
}

function setField(event, field, after) {
  if (field.startsWith('parameters.')) {
    const name = field.slice('parameters.'.length);
    event.parameters = event.parameters.filter(p => p.name !== name);
    if (after?.exists !== false) event.parameters.push({ name, ...clone(after) });
  } else if (after?.exists === false) {
    delete event[field];
  } else {
    event[field] = clone(after);
  }
}

function validateOperation(baseEvent, operation) {
  if (!operation || !operation.event || !operation.field || operation.before === undefined || operation.after === undefined) {
    throw new ContractError('INVALID_CHANGE', 'each operation needs event, field, before and after');
  }
  if (operation.field === 'contexts') {
    throw new ContractError('UNSUPPORTED_FIELD', 'per-event contexts are not represented by the current platform point API');
  }
  const baseValue = getField(baseEvent, operation.field);
  const requestedBefore = operation.field.startsWith('parameters.') && operation.before?.exists !== false
    ? { name: operation.field.slice('parameters.'.length), ...operation.before }
    : operation.before;
  if (!equal(baseValue, requestedBefore)) {
    throw new ContractError('BASELINE_DRIFT', `before differs from baseline: ${operation.field}`);
  }
  if (baseEvent && ['type', 'trackerType', 'spm', 'eventName', 'point'].includes(operation.field) &&
      !equal(baseValue, operation.after)) {
    throw new ContractError('IMMUTABLE_FIELD', 'released event identity cannot be changed');
  }
  if (baseEvent && !operation.field.startsWith('parameters.') && operation.field !== '$event' &&
      operation.after?.exists === false) {
    throw new ContractError('UNSUPPORTED_FIELD', 'platform update cannot remove event metadata');
  }
  if (baseEvent && operation.field.startsWith('parameters.') && baseValue.exists !== false &&
      (operation.after?.exists === false || operation.after?.valueType !== baseValue.valueType)) {
    throw new ContractError('IMMUTABLE_FIELD', 'released parameter name and type cannot be changed or removed');
  }
}

function project(baseline, changes) {
  assertBaseline(baseline);
  const baseDigest = digest(baseline.events);
  const byKey = new Map(normalizeEvents(baseline.events, baseline.application)
    .map(event => [eventKey(baseline.application, event), event]));
  const baselineByKey = new Map([...byKey].map(([key, event]) => [key, clone(event)]));
  const intents = new Map();
  for (const change of changes) {
    if (change?.schemaVersion !== 1 || change.kind !== 'change' || change.host !== baseline.host ||
        String(change.applicationId) !== String(baseline.applicationId) || change.baseDigest !== baseDigest ||
        !Array.isArray(change.operations)) {
      throw new ContractError('BASELINE_DRIFT', `invalid change scope or baseDigest for issue ${change?.issue}`);
    }
    for (const operation of change.operations) {
      const key = eventKey(baseline.application, operation.event);
      const baseEvent = baselineByKey.get(key);
      validateOperation(baseEvent, operation);
      const fieldKey = `${key}:${operation.field}`;
      if (intents.has(fieldKey) && !equal(intents.get(fieldKey), operation.after)) {
        throw new ContractError('CONTRACT_CONFLICT', `different targets for ${fieldKey}`);
      }
      intents.set(fieldKey, clone(operation.after));
      if (operation.field === '$event') {
        if (baseEvent) throw new ContractError('IMMUTABLE_FIELD', 'existing event cannot be replaced');
        const added = normalizeEvent(operation.after);
        if (eventKey(baseline.application, added) !== key) throw new ContractError('INVALID_EVENT_IDENTITY', 'new event identity mismatch');
        byKey.set(key, added);
      } else {
        const current = byKey.get(key);
        if (!current) throw new ContractError('UNKNOWN_EVENT', key);
        setField(current, operation.field, operation.after);
      }
    }
  }
  return { ...baseline, kind: 'candidate', selectedIssues: changes.map(c => String(c.issue)),
    baseDigest, events: normalizeEvents([...byKey.values()], baseline.application) };
}

function planAgainstCurrent(baseline, current, changes, previous = null, requireExactPrevious = true) {
  const target = project(baseline, changes);
  if (current.application !== baseline.application || String(current.applicationId) !== String(baseline.applicationId)) {
    throw new ContractError('APPLICATION_MISMATCH', 'platform tree belongs to another application');
  }
  const base = new Map(normalizeEvents(baseline.events, baseline.application).map(e => [eventKey(baseline.application, e), e]));
  const goal = new Map(target.events.map(e => [eventKey(baseline.application, e), e]));
  const live = new Map(normalizeEvents(current.events, baseline.application).map(e => [eventKey(baseline.application, e), e]));
  if (previous) {
    if (previous.application !== baseline.application || String(previous.applicationId) !== String(baseline.applicationId) ||
        (requireExactPrevious && digest(previous.events) !== digest(current.events))) {
      throw new ContractError('PREVIOUS_LOCK_DRIFT', 'live full tree differs from previous platform lock');
    }
  }
  const old = previous ? new Map(normalizeEvents(previous.events, baseline.application)
    .map(e => [eventKey(baseline.application, e), e])) : new Map();
  const selectedFields = new Set(changes.flatMap(change => change.operations.map(operation =>
    `${eventKey(baseline.application, operation.event)}:${operation.field}`)));
  for (const [key, event] of live) {
    if (!base.has(key) && !goal.has(key)) throw new ContractError('UNOWNED_PLATFORM_CHANGE', `unknown platform event ${key}`);
    const b = base.get(key);
    const g = goal.get(key);
    const p = old.get(key);
    if (!b && !equal(event, g)) throw new ContractError('UNOWNED_PLATFORM_CHANGE', `unexpected new platform event ${key}`);
    if (b && g) {
      const fields = new Set([...Object.keys(b), ...Object.keys(g), ...Object.keys(event)]);
      for (const field of fields) {
        if (field === 'parameters') {
          const names = new Set([...b.parameters, ...g.parameters, ...event.parameters].map(p => p.name));
          for (const name of names) {
            const path = `parameters.${name}`;
            if (!equal(getField(event, path), getField(b, path)) && !equal(getField(event, path), getField(g, path)) &&
                !(selectedFields.has(`${key}:${path}`) && equal(getField(event, path), getField(p, path)))) {
              throw new ContractError('UNOWNED_PLATFORM_CHANGE', `${key}:${path}`);
            }
          }
        } else if (!equal(getField(event, field), getField(b, field)) && !equal(getField(event, field), getField(g, field)) &&
            !(selectedFields.has(`${key}:${field}`) && equal(getField(event, field), getField(p, field)))) {
          throw new ContractError('UNOWNED_PLATFORM_CHANGE', `${key}:${field}`);
        }
      }
    }
  }
  for (const key of base.keys()) {
    if (!live.has(key)) throw new ContractError('UNOWNED_PLATFORM_CHANGE', `missing released event ${key}`);
  }
  return { target, currentDigest: digest(current.events), targetDigest: digest(target.events),
    complete: digest(current.events) === digest(target.events) };
}

module.exports = { ContractError, parseContract, eventKey, normalizeEvent, normalizeEvents,
  digest, equal, project, planAgainstCurrent, assertBaseline };

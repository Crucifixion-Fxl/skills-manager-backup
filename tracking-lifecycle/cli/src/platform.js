'use strict';

const { ContractError, eventKey, normalizeEvents, digest, planAgainstCurrent, equal } = require('./contracts');
const { reviewedHttpsEndpoint } = require('./endpoint');

const EVENT_TYPES = { 1: 'PAGE', 2: 'MODULE', 3: 'COMPONENT', 4: 'SELF_DEFINE' };
const EVENT_CODES = { PAGE: 1, MODULE: 2, COMPONENT: 3, SELF_DEFINE: 4 };
const TRACKER_CODES = { BASE: 0, CLK: 1, EXP: 2 };
const TRACKER_TYPES = { 0: 'BASE', 1: 'CLK', 2: 'EXP' };
const VALUE_TYPES = new Set(['null', 'boolean', 'object', 'array', 'number', 'string', 'integer']);

function required(value, label) {
  if (value === undefined || value === null || value === '') {
    throw new ContractError('PLATFORM_SHAPE', `${label} missing from platform response`);
  }
  return value;
}

function normalizeParameter(raw) {
  const valueType = raw.valueType;
  if (!VALUE_TYPES.has(valueType) || TRACKER_TYPES[Number(raw.trackerType)] === undefined) {
    throw new ContractError('PLATFORM_SHAPE', `unknown parameter type: ${raw.name}`);
  }
  const result = { name: required(raw.name, 'parameter.name'), valueType, isRequired: Number(raw.isRequired) === 1 };
  if (raw.description) result.description = raw.description;
  return result;
}

function detailToEvents(raw, spm) {
  const type = EVENT_TYPES[Number(raw.type)];
  if (!type) throw new ContractError('PLATFORM_SHAPE', `unknown event type ${raw.type}`);
  const params = raw.parameters || raw.parameterList;
  if (!Array.isArray(params)) throw new ContractError('PLATFORM_SHAPE', `event ${raw.id} has no parameter list`);
  const trackerTypes = type === 'MODULE' || type === 'COMPONENT' ? ['CLK', 'EXP'] : ['BASE'];
  return trackerTypes.map(trackerType => {
    const event = { type, trackerType, spm, point: required(raw.point, 'event.point'),
      name: required(raw.name, 'event.name'),
      parameters: params.filter(p => Number(p.trackerType) === TRACKER_CODES[trackerType]).map(normalizeParameter),
      platformId: required(raw.id, 'event.id') };
    if (raw.description) event.description = raw.description;
    if (raw.alias) event.alias = raw.alias;
    if (raw.category) event.category = raw.category;
    if (raw.baseSchemas) {
      if (!Array.isArray(raw.baseSchemas)) throw new ContractError('PLATFORM_SHAPE', 'baseSchemas must be a list');
      event.baseSchemas = raw.baseSchemas.map(x => typeof x === 'object' ? x.id : x).sort((a, b) => Number(a) - Number(b));
    }
    if (type === 'SELF_DEFINE') event.eventName = raw.point;
    return event;
  });
}

function walkTree(nodes, ancestry = [], out = []) {
  if (!Array.isArray(nodes)) throw new ContractError('PLATFORM_SHAPE', 'search result is not a tree');
  for (const node of nodes) {
    if (!node || !EVENT_TYPES[Number(node.type)]) throw new ContractError('PLATFORM_SHAPE', 'unexpected event in search tree');
    const spm = [...ancestry, required(node.point, 'tree.point')];
    out.push({ id: required(node.id, 'tree.id'), spm });
    if (node.children) walkTree(node.children, spm, out);
  }
  return out;
}

async function fetchTransport(baseUrl, token, method, path, body) {
  if (!token) throw new ContractError('MISSING_TOKEN', 'TMT_TOKEN is required');
  let response;
  try {
    response = await fetch(new URL(path, baseUrl), {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
  } catch (error) {
    throw new ContractError('PLATFORM_NETWORK',
      `${new URL(baseUrl).origin}: ${error.cause?.code || error.name || 'network error'}`);
  }
  let payload;
  try { payload = await response.json(); } catch { throw new ContractError('PLATFORM_HTTP', `non-JSON HTTP ${response.status}`); }
  if (!response.ok || payload.code !== 200 || payload.success === false) {
    throw new ContractError('PLATFORM_HTTP', `HTTP ${response.status}, API ${payload.code}: ${payload.errorMessage || 'request failed'}`);
  }
  return payload.data;
}

class PlatformClient {
  constructor({ baseUrl, token, request } = {}) {
    const endpoint = request ? null : reviewedHttpsEndpoint(baseUrl);
    this.request = request || ((method, path, body) => fetchTransport(endpoint, token, method, path, body));
  }

  listReleases(applicationId) {
    return this.request('GET', `/api/release/getAllRelease?applicationId=${encodeURIComponent(applicationId)}`);
  }

  getDetail(eventId) {
    return this.request('GET', `/api/info/getEventDetail?eventId=${encodeURIComponent(eventId)}`);
  }

  async readTree({ application, applicationId }) {
    const roots = [];
    const pageSize = 1000;
    for (let pageNum = 1; pageNum <= 100; pageNum++) {
      const response = await this.request('GET', `/api/info/search?applicationId=${encodeURIComponent(applicationId)}&pageNum=${pageNum}&pageSize=${pageSize}`);
      const page = Array.isArray(response) ? response : response?.records || response?.list;
      if (!Array.isArray(page)) throw new ContractError('PLATFORM_SHAPE', 'unrecognized search response');
      roots.push(...page);
      if (page.length < pageSize) break;
      if (pageNum === 100) throw new ContractError('PLATFORM_TRUNCATED', 'search exceeded 100 pages');
    }
    const nodes = walkTree(roots);
    const ids = new Set();
    const events = [];
    for (const node of nodes) {
      if (ids.has(String(node.id))) throw new ContractError('PLATFORM_SHAPE', `duplicate event id ${node.id}`);
      ids.add(String(node.id));
      const raw = await this.getDetail(node.id);
      if (String(raw?.id) !== String(node.id) || String(raw.parentApplicationId) !== String(applicationId)) {
        throw new ContractError('PLATFORM_SHAPE', `event ${node.id} detail belongs to another application`);
      }
      events.push(...detailToEvents(raw, node.spm));
    }
    normalizeEvents(events, application);
    return { application, applicationId, events };
  }

  saveEvent(payload) { return this.request('POST', '/api/info/saveOrUpdateEventInfo', payload); }
  createEvent(payload) { return this.request('POST', '/api/info/batchCreateEvents', payload); }
  createRelease(payload) { return this.request('POST', '/api/release/addRelease', payload); }
  getUnpassed(releaseId, applicationId) { return this.request('POST', '/api/release/getUnpassedEvents', { id: releaseId, applicationId }); }
  validate(releaseId, applicationId) { return this.request('POST', '/api/release/validate', { id: releaseId, applicationId }); }
  onlineRelease(releaseId, applicationId) { return this.request('POST', '/api/release/onlineRelease', { id: releaseId, applicationId }); }
}

function prepareUpdate(raw, desiredEvents) {
  if (!raw?.id || !Array.isArray(desiredEvents) || !desiredEvents.length) throw new ContractError('INVALID_UPDATE', 'event detail and targets required');
  const oldParams = raw.parameters || raw.parameterList;
  if (!Array.isArray(oldParams)) throw new ContractError('PLATFORM_SHAPE', 'missing complete parameter list');
  if (oldParams.some(param => !VALUE_TYPES.has(param?.valueType))) {
    throw new ContractError('PLATFORM_SHAPE', 'existing parameter has an ambiguous or unsupported valueType');
  }
  const metadata = ['name', 'description', 'alias', 'category', 'baseSchemas'];
  for (const desired of desiredEvents.slice(1)) {
    for (const field of metadata) {
      if (!equal(desired[field], desiredEvents[0][field])) {
        throw new ContractError('INVALID_UPDATE', `tracker types disagree on ${field}`);
      }
    }
  }
  const result = { ...raw, baseSchemas: (raw.baseSchemas || []).map(x => typeof x === 'object' ? x.id : x), parameters: oldParams.map(p => ({ ...p })) };
  delete result.parameterList;
  delete result.children;
  for (const field of metadata) {
    if (desiredEvents[0][field] !== undefined) result[field] = structuredClone(desiredEvents[0][field]);
  }
  for (const desired of desiredEvents) {
    const trackerType = TRACKER_CODES[desired.trackerType];
    if (trackerType === undefined || EVENT_CODES[desired.type] !== Number(raw.type) || desired.point !== raw.point) {
      throw new ContractError('INVALID_UPDATE', 'target differs from platform event identity');
    }
    const oldForTracker = result.parameters.filter(p => Number(p.trackerType) === trackerType);
    result.parameters = result.parameters.filter(p => Number(p.trackerType) !== trackerType);
    for (const param of desired.parameters || []) {
      if (!VALUE_TYPES.has(param.valueType)) {
        throw new ContractError('INVALID_PARAMETER', `unsupported valueType ${param.valueType}`);
      }
      const old = oldForTracker.find(p => p.name === param.name);
      const next = { ...old, eventId: raw.id, trackerType, name: param.name, valueType: param.valueType,
        isRequired: param.isRequired ? 1 : 0 };
      if (param.description !== undefined) next.description = param.description;
      else delete next.description;
      result.parameters.push(next);
    }
  }
  return result;
}

function physicalKey(event) { return JSON.stringify([event.type, event.spm, event.eventName || '']); }

async function createWorkorder(client, schemaReader, baseline, { name, version }) {
  const { verifyBaseline } = require('./release');
  if (baseline.kind === 'bootstrapBaseline') {
    throw new ContractError('WORKORDER_EXISTS', 'first-release bootstrap is bound to an existing active workorder');
  }
  await verifyBaseline(client, schemaReader, baseline);
  if (!name || !/^\d+-\d+-\d+$/.test(version)) {
    throw new ContractError('INVALID_WORKORDER', 'name and X-X-X version required');
  }
  await client.createRelease({ name, version, applicationId: baseline.applicationId });
  const releases = await client.listReleases(baseline.applicationId);
  const active = (Array.isArray(releases) ? releases : releases?.records || [])
    .filter(r => ![3, 4].includes(Number(r.releaseStatus)));
  if (active.length !== 1 || active[0].version !== version ||
      String(active[0].applicationId) !== String(baseline.applicationId)) {
    throw new ContractError('WORKORDER_MISMATCH', 'created workorder readback differs from request');
  }
  const current = await client.readTree(baseline);
  if (digest(current.events) !== digest(baseline.events)) {
    throw new ContractError('UNOWNED_PLATFORM_CHANGE', 'platform tree changed during workorder creation');
  }
  return active[0];
}

async function postChanges(client, baseline, changes, releaseId, previousLock = null) {
  const { assertPublishedBoundary } = require('./release');
  const releases = await client.listReleases(baseline.applicationId);
  assertPublishedBoundary(releases, baseline);
  const active = (Array.isArray(releases) ? releases : releases?.records || [])
    .filter(r => ![3, 4].includes(Number(r.releaseStatus)));
  if (active.length !== 1 || String(active[0].id) !== String(releaseId) ||
      (active[0].applicationId != null && String(active[0].applicationId) !== String(baseline.applicationId))) {
    throw new ContractError('WORKORDER_MISMATCH', 'exactly the selected active workorder is required');
  }
  if (previousLock && (previousLock.kind !== 'platformLock' || previousLock.host !== baseline.host ||
      previousLock.application !== baseline.application || String(previousLock.applicationId) !== String(baseline.applicationId) ||
      String(previousLock.releaseId) !== String(releaseId) || previousLock.version !== active[0].version ||
      previousLock.baseDigest !== digest(baseline.events) || previousLock.digest !== digest(previousLock.events) ||
      JSON.stringify(previousLock.selectedIssues) !== JSON.stringify(changes.map(c => String(c.issue))))) {
    throw new ContractError('PREVIOUS_LOCK_DRIFT', 'previous lock scope or digest differs from selected workorder');
  }
  let current = await client.readTree(baseline);
  const plan = planAgainstCurrent(baseline, current, changes, previousLock);
  if (plan.complete) return { releaseId, version: active[0].version, events: current.events, digest: plan.targetDigest, writes: 0 };
  const targetGroups = new Map();
  for (const event of plan.target.events) {
    const key = physicalKey(event);
    if (!targetGroups.has(key)) targetGroups.set(key, []);
    targetGroups.get(key).push(event);
  }
  const currentByKey = new Map(current.events.map(e => [eventKey(baseline.application, e), e]));
  let writes = 0;
  for (const events of [...targetGroups.values()].sort((a, b) => a[0].spm.length - b[0].spm.length)) {
    if (events.every(e => equal(e, currentByKey.get(eventKey(baseline.application, e))))) continue;
    const existing = events.map(e => currentByKey.get(eventKey(baseline.application, e))).find(Boolean);
    if (!existing) {
      const first = events[0];
      for (const other of events.slice(1)) {
        for (const field of ['name', 'description', 'alias', 'category', 'baseSchemas']) {
          if (!equal(first[field], other[field])) {
            throw new ContractError('INVALID_UPDATE', `tracker types disagree on ${field}`);
          }
        }
      }
      const event = { name: first.name || first.point, type: first.type, trackerType: first.trackerType,
        point: first.point, parameters: (first.parameters || []).map(p => ({ ...p })) };
      for (const field of ['description', 'alias', 'category', 'baseSchemas']) {
        if (first[field] !== undefined) event[field] = structuredClone(first[field]);
      }
      if (first.spm.length > 1) event.parentPage = first.spm[0];
      if (first.spm.length > 2) event.parentModule = first.spm[1];
      await client.createEvent({ applicationId: baseline.applicationId, events: [event] });
      if (events.length > 1 && events.slice(1).some(e => e.parameters.length)) {
        const afterCreate = await client.readTree(baseline);
        const justCreated = afterCreate.events.find(e => physicalKey(e) === physicalKey(first));
        if (!justCreated?.platformId) throw new ContractError('PLATFORM_READBACK_MISMATCH', 'new event ID not found');
        const raw = await client.getDetail(justCreated.platformId);
        await client.saveEvent(prepareUpdate(raw, events));
      }
    } else {
      const raw = await client.getDetail(existing.platformId);
      if (String(raw?.parentApplicationId) !== String(baseline.applicationId) ||
          digest(detailToEvents(raw, existing.spm)) !== digest(current.events.filter(e => physicalKey(e) === physicalKey(existing)))) {
        throw new ContractError('PLATFORM_DRIFT_BEFORE_WRITE', 'fresh event detail differs from prior full-tree read');
      }
      await client.saveEvent(prepareUpdate(raw, events));
    }
    writes++;
    current = await client.readTree(baseline);
    planAgainstCurrent(baseline, current, changes, previousLock, false);
  }
  if (digest(current.events) !== plan.targetDigest) throw new ContractError('PLATFORM_READBACK_MISMATCH', 'posted tree differs from selected projection');
  return { releaseId, version: active[0].version, events: current.events, digest: plan.targetDigest, writes };
}

module.exports = { PlatformClient, detailToEvents, prepareUpdate, createWorkorder, postChanges, fetchTransport, walkTree };

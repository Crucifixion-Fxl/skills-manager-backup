'use strict';

const crypto = require('node:crypto');
const { ContractError, digest, normalizeEvents } = require('./contracts');
const { schemaPath } = require('./release');
const PATH = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\/[a-z0-9][a-z0-9_-]*\/jsonschema\/\d+-\d+-\d+$/;
const fail = (code, message, details) => { throw new ContractError(code, message, details); };

// Only object keys are reordered. Array order remains part of the frozen schema.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const encode = value => JSON.stringify(canonical(value));
const hash = value => crypto.createHash('sha256').update(encode(value)).digest('hex');

class ProductionSchemaReader {
  constructor({ baseUrls, fetchImpl = fetch } = {}) {
    if (!Array.isArray(baseUrls) || !baseUrls.length) fail('INVALID_ENDPOINT', 'explicit production schema HTTPS base URLs required');
    this.baseUrls = baseUrls.map(value => {
      let url;
      try { url = new URL(value); } catch { fail('INVALID_ENDPOINT', 'invalid production schema endpoint'); }
      if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
          /%|\\/.test(url.pathname)) fail('INVALID_ENDPOINT', 'production schema endpoint must be explicit HTTPS without credentials or query');
      return url.href.replace(/\/$/, '');
    });
    if (new Set(this.baseUrls).size !== this.baseUrls.length) fail('INVALID_ENDPOINT', 'duplicate production schema endpoint');
    this.fetch = fetchImpl;
  }

  async read(path) {
    if (!PATH.test(path)) fail('INVALID_SCHEMA_ID', 'invalid production schema path');
    const results = [];
    for (const baseUrl of this.baseUrls) {
      let response;
      try {
        response = await this.fetch(`${baseUrl}/${path}`, { redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(30000) });
      } catch { fail('PROD_SCHEMA_NETWORK', `production schema read failed: ${path}`); }
      if (!response.ok || response.redirected) fail('PROD_SCHEMA_MISSING', `production schema unavailable: ${path}`);
      try { results.push(await response.json()); }
      catch { fail('PROD_SCHEMA_INVALID', `production schema is not JSON: ${path}`); }
    }
    return results;
  }
}

function pointer(root, fragment) {
  if (fragment === '' || fragment === '#') return root;
  if (!fragment.startsWith('#/')) fail('UNRESOLVED_SCHEMA_REFERENCE', 'only JSON Pointer schema fragments are supported');
  let value = root;
  for (const encoded of fragment.slice(2).split('/')) {
    let key;
    try { key = decodeURIComponent(encoded).replace(/~1/g, '/').replace(/~0/g, '~'); }
    catch { fail('UNRESOLVED_SCHEMA_REFERENCE', 'invalid schema reference fragment'); }
    if (!value || typeof value !== 'object' || !Object.hasOwn(value, key)) fail('UNRESOLVED_SCHEMA_REFERENCE', 'schema reference target missing');
    value = value[key];
  }
  return value;
}

function schemaClosure(schemas, roots) {
  const visited = new Set();
  const walkers = new Map();
  const maps = ['properties', 'patternProperties', 'definitions', '$defs', 'dependentSchemas'];
  const arrays = ['allOf', 'anyOf', 'oneOf', 'prefixItems'];
  const singles = ['additionalItems', 'additionalProperties', 'not', 'contains', 'propertyNames',
    'if', 'then', 'else', 'unevaluatedItems', 'unevaluatedProperties', 'contentSchema'];
  // These values are annotations or assertions, never child Schema locations.
  const scalar = ['$schema', '$comment', 'self', 'title', 'description', 'default', 'examples',
    'enum', 'const', 'type', 'required', 'format', 'readOnly', 'writeOnly', 'deprecated',
    'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
    'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'uniqueItems',
    'minProperties', 'maxProperties', 'minContains', 'maxContains', 'dependentRequired',
    'contentEncoding', 'contentMediaType'];
  const supported = new Set([...maps, ...arrays, ...singles, ...scalar,
    'items', 'dependencies', '$ref']);
  function visit(path, fragment = '') {
    if (visited.has(path)) {
      walkers.get(path)(pointer(schemas.get(path), fragment));
      return;
    }
    if (!schemas.has(path)) fail('UNRESOLVED_SCHEMA_REFERENCE', `historical schema missing: ${path}`);
    visited.add(path);
    const root = schemas.get(path);
    const [vendor, name, format, version] = path.split('/');
    if (!root || typeof root !== 'object' || Array.isArray(root) || root.self?.vendor !== vendor ||
        root.self?.name !== name || root.self?.format !== format || root.self?.version !== version) {
      fail('PUBLISHED_SCHEMA_SHAPE', `historical schema identity differs: ${path}`);
    }
    const seen = new WeakSet();
    function walk(value) {
      if (!value || typeof value !== 'object' || seen.has(value)) return;
      seen.add(value);
      // Remote/nested resolution scopes are not silently interpreted as local.
      if (Object.hasOwn(value, '$id') || Object.hasOwn(value, 'id') ||
          Object.hasOwn(value, '$anchor') || Object.hasOwn(value, '$dynamicAnchor') ||
          Object.hasOwn(value, '$recursiveAnchor') || Object.hasOwn(value, '$vocabulary') ||
          Object.hasOwn(value, '$dynamicRef') || Object.hasOwn(value, '$recursiveRef')) {
        fail('UNRESOLVED_SCHEMA_REFERENCE', 'unsupported schema reference or resolution scope');
      }
      for (const key of Object.keys(value)) {
        if (!supported.has(key)) fail('UNRESOLVED_SCHEMA_REFERENCE', `unsupported schema keyword: ${key}`);
      }
      if (Object.hasOwn(value, '$ref')) {
        const ref = value.$ref;
        if (typeof ref !== 'string') fail('UNRESOLVED_SCHEMA_REFERENCE', 'invalid schema reference');
        if (ref.startsWith('#')) walk(pointer(root, ref));
        else {
          const match = /^iglu:([^#]+)(#.*)?$/.exec(ref);
          if (!match || !PATH.test(match[1])) fail('UNRESOLVED_SCHEMA_REFERENCE', 'only frozen iglu schema references are supported');
          visit(match[1], match[2] || '');
        }
      }
      // Instance annotations may legitimately contain id/$ref keys: only schema
      // locations participate in reference resolution, not default/enum/examples.
      for (const key of maps) {
        if (value[key] && typeof value[key] === 'object') Object.values(value[key]).forEach(walk);
      }
      for (const key of arrays) {
        if (Array.isArray(value[key])) value[key].forEach(walk);
      }
      if (Array.isArray(value.items)) value.items.forEach(walk);
      else walk(value.items);
      for (const key of singles) walk(value[key]);
      if (value.dependencies && typeof value.dependencies === 'object') {
        Object.values(value.dependencies).filter(child => !Array.isArray(child)).forEach(walk);
      }
    }
    walkers.set(path, walk);
    walk(root);
    if (fragment) walk(pointer(root, fragment));
  }
  roots.forEach(path => visit(path));
  if (visited.size !== schemas.size) fail('PUBLISHED_SCHEMA_SET', 'historical schema set contains unrelated schemas');
}

async function verifyPublishedSnapshot({ expected, snapshot, schemaReader }) {
  if (!expected || !Number.isSafeInteger(expected.applicationId) || expected.applicationId <= 0 ||
      !Number.isSafeInteger(expected.releaseId) || expected.releaseId <= 0 || !/^[a-z][a-z0-9_]*$/.test(expected.application) ||
      !/^\d+-\d+-\d+$/.test(expected.version) || !/^[a-f0-9]{64}$/.test(expected.eventDigest)) {
    fail('INVALID_PUBLISHED_CONTRACT', 'complete specified release contract required');
  }
  if (!snapshot || snapshot.schemaVersion !== 1 || snapshot.releaseStatus !== 3 ||
      ['application', 'applicationId', 'releaseId', 'version'].some(key => snapshot[key] !== expected[key])) {
    fail('PUBLISHED_RELEASE_MISMATCH', 'historical snapshot differs from specified published release');
  }
  const events = normalizeEvents(snapshot.events, expected.application);
  const actualDigest = digest(events);
  if (actualDigest !== expected.eventDigest) fail('PUBLISHED_EVENT_DRIFT', 'historical event tree differs from specified release',
    { expectedDigest: expected.eventDigest, actualDigest, eventCount: events.length });
  if (!events.length || !Array.isArray(snapshot.schemas) || !snapshot.schemas.length) fail('PUBLISHED_SCHEMA_SET', 'complete nonempty historical schema set required');
  const schemas = new Map();
  for (const entry of snapshot.schemas) {
    if (!entry || !PATH.test(entry.path) || schemas.has(entry.path)) fail('PUBLISHED_SCHEMA_SET', 'invalid or duplicate historical schema path');
    schemas.set(entry.path, entry.schema);
  }
  if (expected.schemaDependencies !== undefined && (!Array.isArray(expected.schemaDependencies) || expected.schemaDependencies.length)) {
    fail('UNSUPPORTED_SCHEMA_DEPENDENCIES', 'independent context dependencies require a separately frozen complete contract');
  }
  const roots = events.map(event => schemaPath(expected.application, event, expected.version));
  if (new Set(roots).size !== roots.length) fail('PUBLISHED_SCHEMA_SET', 'events map to duplicate schema identities');
  schemaClosure(schemas, roots);
  if (!schemaReader || typeof schemaReader.read !== 'function') fail('PROD_SCHEMA_READER_REQUIRED', 'complete production schema reader required');
  const checked = [];
  for (const path of [...schemas.keys()].sort()) {
    const responses = await schemaReader.read(path);
    if (!Array.isArray(responses) || !responses.length) fail('PROD_SCHEMA_MISSING', `production schema missing: ${path}`);
    for (const schema of responses) {
      if (encode(schema) !== encode(schemas.get(path))) fail('PROD_SCHEMA_DRIFT', `production schema differs from historical snapshot: ${path}`,
        { path, expectedDigest: hash(schemas.get(path)), actualDigest: hash(schema) });
    }
    checked.push({ path, schema: schemas.get(path) });
  }
  return { status: 'PASS', releaseId: expected.releaseId, eventDigest: actualDigest, prodSchemaDigest: hash(checked),
    eventCount: events.length, schemaCount: checked.length };
}

module.exports = { verifyPublishedSnapshot, ProductionSchemaReader };

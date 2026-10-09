'use strict';
const { fail, plain } = require('./gateway');

function valueFor(name, rule, input, scope) {
  if (!plain(rule) || !['integer', 'string', 'boolean'].includes(rule.type)) fail('INVALID_PROFILE');
  let value = input[name];
  if (rule.binding) {
    if (!Object.hasOwn(scope, rule.binding) || scope[rule.binding] === undefined || scope[rule.binding] === '') fail('RESOURCE_SCOPE_REQUIRED');
    if (value !== undefined && String(value) !== String(scope[rule.binding])) fail('RESOURCE_MISMATCH');
    value = scope[rule.binding];
  }
  if (value === undefined) value = rule.default;
  if (value === undefined) { if (rule.required) fail('INVALID_INPUT'); return undefined; }
  if (rule.type === 'integer') {
    if (!Number.isSafeInteger(value) || value < (rule.minimum ?? 0) || value > (rule.maximum ?? 1000000)) fail('INVALID_INPUT');
  } else if (rule.type === 'string') {
    if (typeof value !== 'string' || value.length > (rule.maxLength ?? 256) || /[\x00-\x1f\x7f]/.test(value)) fail('INVALID_INPUT');
    if (rule.pattern) {
      let matcher; try { matcher = new RegExp(rule.pattern); } catch { fail('INVALID_PROFILE'); }
      if (!matcher.test(value)) fail('INVALID_INPUT');
    }
  } else if (typeof value !== 'boolean') fail('INVALID_INPUT');
  if (rule.enum && (!Array.isArray(rule.enum) || !rule.enum.includes(value))) fail('INVALID_INPUT');
  return value;
}

// The registry owns endpoint names, query names and schemas. The caller supplies
// values only; immutable lease bindings cannot be overridden by a request.
function operationPath(operation, input = {}, scope = {}) {
  if (Object.hasOwn(input, 'body')) { input = { ...input }; delete input.body; }
  if (!plain(input) || !plain(scope)) fail('INVALID_INPUT');
  const parameters = operation.parameters || {}, query = operation.query || {};
  if (!plain(parameters) || !plain(query)) fail('INVALID_PROFILE');
  if (Object.keys(parameters).some(k => Object.hasOwn(query, k))) fail('INVALID_PROFILE');
  if (Object.keys(input).some(k => !Object.hasOwn(parameters, k) && !Object.hasOwn(query, k))) fail('INVALID_INPUT');
  let requestPath = operation.path;
  if (typeof requestPath !== 'string' || !requestPath.startsWith('/') || requestPath.startsWith('//') || /[\\\x00-\x20#]/.test(requestPath)) fail('INVALID_PROFILE');
  for (const [name, rule] of Object.entries(parameters)) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || !requestPath.includes('{' + name + '}')) fail('INVALID_PROFILE');
    const value = valueFor(name, rule, input, scope);
    if (value === undefined || !/^[a-zA-Z0-9_-]{1,256}$/.test(String(value))) fail('INVALID_INPUT');
    requestPath = requestPath.split('{' + name + '}').join(encodeURIComponent(String(value)));
  }
  if (/[{}]/.test(requestPath)) fail('INVALID_PROFILE');
  const url = new URL(requestPath, 'https://registry.invalid');
  if (url.origin !== 'https://registry.invalid' || url.pathname !== requestPath.split('?')[0]) fail('INVALID_PROFILE');
  for (const [name, rule] of Object.entries(query)) {
    const value = valueFor(name, rule, input, scope);
    const key = rule.queryName || name;
    if (typeof key !== 'string' || !key || /[\x00-\x1f]/.test(key)) fail('INVALID_PROFILE');
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  return url.pathname + url.search;
}
function operationRequest(operation, input = {}, scope = {}) {
  const path = operationPath(operation, input, scope);
  const method = operation.method || 'GET';
  if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) fail('INVALID_PROFILE');
  if (!operation.body) {
    if (Object.hasOwn(input, 'body')) fail('INVALID_INPUT');
    return { method, path };
  }
  if (method === 'GET' || !plain(operation.body) || !plain(input.body || {})) fail('INVALID_INPUT');
  if (Object.keys(input.body || {}).some(key => !Object.hasOwn(operation.body, key))) fail('INVALID_INPUT');
  const body = {};
  for (const [name, rule] of Object.entries(operation.body)) {
    const value = valueFor(name, rule, input.body || {}, scope);
    if (value !== undefined) body[name] = value;
  }
  return { method, path, body };
}
module.exports = { operationPath, operationRequest };

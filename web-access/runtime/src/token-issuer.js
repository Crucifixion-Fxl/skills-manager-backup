'use strict';
const { fail, httpsOrigin, AccessError } = require('./gateway');
const { operationRequest } = require('./operation-input');
const { bearerProvider } = require('./providers');
const { connectBrowser } = require('./browser');
const { reserveToken, readToken, removeToken } = require('./token-custody');
const get = (value, path) => path.split('.').reduce((v, k) => v?.[k], value);
// Private protocol: this is called in-process through direct, non-retrying CDP.
// Raw issuance replies never go through the business gateway or CLI output.
async function browserJSON(origin, method, path, body) {
  if (location.origin !== origin) return { error: 'BROWSER_ORIGIN_MISMATCH' };
  try {
    const response = await fetch(origin + path, { method, credentials: 'include', redirect: 'manual',
      signal: AbortSignal.timeout(30000), headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (response.status === 401) return { error: 'AUTH_REQUIRED' };
    if (response.status === 403) return { error: 'API_PERMISSION_DENIED' };
    if (!response.ok) return { error: method === 'GET' ? 'API_REQUEST_FAILED' : 'RESULT_UNKNOWN' };
    const text = await response.text();
    if (text.length > 1048576) return { error: method === 'GET' ? 'OUTPUT_LIMIT' : 'RESULT_UNKNOWN' };
    return { data: JSON.parse(text) };
  } catch { return { error: method === 'GET' ? 'API_REQUEST_FAILED' : 'RESULT_UNKNOWN' }; }
}
async function request(page, origin, method, path, body) {
  if (method !== 'GET' && page.supportsWrites !== true) fail('READ_ONLY_PROVIDER');
  const expression = `(${browserJSON.toString()})(${JSON.stringify(origin)},${JSON.stringify(method)},${JSON.stringify(path)},${JSON.stringify(body)})`;
  let response;
  try { response = await page.cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); }
  catch { fail(method === 'GET' ? 'BROWSER_UNAVAILABLE' : 'RESULT_UNKNOWN'); }
  const value = response.result?.value;
  if (response.exceptionDetails || !value) fail(method === 'GET' ? 'API_REQUEST_FAILED' : 'RESULT_UNKNOWN');
  if (value.error) fail(['AUTH_REQUIRED','API_PERMISSION_DENIED','BROWSER_ORIGIN_MISMATCH','OUTPUT_LIMIT','RESULT_UNKNOWN'].includes(value.error) ? value.error : 'API_REQUEST_FAILED');
  return value.data;
}
function contract(profile, issuer) {
  const origin = httpsOrigin(profile.origin);
  const rule = profile.tokenIssuers?.[issuer];
  if (!rule || rule.authentication !== 'interactive-cookie' || rule.create?.method !== 'POST'
    || !rule.listPath || !rule.revoke || !profile.identity?.subjectPath) fail('TOKEN_ISSUER_NOT_REGISTERED');
  operationRequest(rule.revoke, { id: '1' });
  operationRequest({ path: rule.listPath });
  return { origin, rule };
}
function checkEnvelope(value, profile) {
  const reply = profile.tokenResponse;
  if (!reply || get(value, reply.successPath) !== reply.successValue) fail('API_REQUEST_FAILED');
}
async function issueToken({ profile, issuer, input, expectedSubject, secretFile, retention, page: suppliedPage, browser = {}, now = Date.now, fetchImpl = global.fetch } = {}) {
  if (!['once','save'].includes(retention) || !expectedSubject || typeof expectedSubject !== 'string') fail('INVALID_INPUT');
  const { origin, rule } = contract(profile, issuer);
  const create = operationRequest(rule.create, input);
  const storage = reserveToken(secretFile); // Before login or issuance: never overwrite, and fail without upstream I/O.
  let page = suppliedPage, created = false, saved = false, recovery;
  try {
    page ||= await connectBrowser({ ...browser, origin });
    if (page.supportsWrites !== true) fail('READ_ONLY_PROVIDER');
    const identity = await request(page, origin, 'GET', profile.identity.path); checkEnvelope(identity, profile);
    if (String(get(identity, profile.identity.subjectPath)) !== expectedSubject) fail('IDENTITY_CHANGED');
    const reply = await request(page, origin, create.method, create.path, create.body);
    checkEnvelope(reply, profile); created = true;
    const token = get(reply, rule.secretPath), id = get(reply, rule.idPath), expiresAt = get(reply, rule.expiresAtPath);
    if (typeof token !== 'string' || !new RegExp(rule.secretPattern).test(token) || /[\r\n]/.test(token)
      || !/^[0-9]{1,20}$/.test(String(id)) || typeof id === 'number' && !Number.isSafeInteger(id) || !Number.isSafeInteger(expiresAt) || expiresAt <= now()) fail('RESULT_UNKNOWN');
    const record = { schemaVersion: 1, state: 'UNVERIFIED', tool: profile.tool, issuer, origin, subject: expectedSubject,
      id: String(id), expiresAt, retention, secret: token, resource: rule.resourceField ? create.body[rule.resourceField] : null };
    recovery = { origin, issuer, recordId: record.id, retention, action: 'reconcile or revoke the newly issued record before retrying' };
    storage.write(record); saved = true;
    const listed = await request(page, origin, 'GET', rule.listPath); checkEnvelope(listed, profile);
    const items = get(listed, rule.listItemsPath);
    const item = Array.isArray(items) && items.find(x => String(x.id) === record.id);
    if (!item || item.status !== 0 || item.expiresAt !== expiresAt
      || rule.resourceField && String(item[rule.resourceField]) !== String(record.resource)) fail('TOKEN_SAVED_UNVERIFIED');
    const probe = rule.targetIdentity;
    if (!probe?.path || !probe.subjectPath) fail('TOKEN_SAVED_UNVERIFIED');
    operationRequest({ path: probe.path });
    const tokenProvider = bearerProvider({ origin, getToken: () => token, fetchImpl });
    const authenticated = await tokenProvider.request('GET', probe.path);
    checkEnvelope(authenticated, profile);
    if (String(get(authenticated, probe.subjectPath)) !== expectedSubject) fail('TOKEN_SAVED_UNVERIFIED');
    record.state = 'VERIFIED'; storage.write(record);
    return { state: 'TOKEN_VERIFIED', tool: profile.tool, issuer, origin, subject: expectedSubject,
      recordId: record.id, expiresAt, retention, resource: record.resource, authority: rule.authority, credentialSaved: true };
  } catch (error) {
    if (created && recovery && !saved) {
      const failure = new AccessError('TOKEN_ISSUANCE_STORAGE_FAILED'); failure.publicRecovery = recovery; throw failure;
    }
    if (created && saved && !['RESULT_UNKNOWN','TOKEN_SAVED_UNVERIFIED','PRIVATE_STORAGE_CHANGED'].includes(error.code)) {
      const failure = new AccessError('TOKEN_SAVED_UNVERIFIED'); failure.publicRecovery = recovery; throw failure;
    }
    if (recovery) error.publicRecovery = recovery;
    throw error;
  } finally {
    storage.close(); storage.discardEmpty();
    if (!suppliedPage) await page?.close?.();
  }
}
async function revokeToken({ profile, secretFile, expectedSubject, page: suppliedPage, browser = {} } = {}) {
  const { record, inode } = readToken(secretFile);
  const { origin, rule } = contract(profile, record.issuer);
  if (record.origin !== origin || record.tool !== profile.tool || !expectedSubject || record.subject !== expectedSubject) fail('IDENTITY_CHANGED');
  const revoke = operationRequest(rule.revoke, { id: record.id });
  let page = suppliedPage;
  try {
    page ||= await connectBrowser({ ...browser, origin });
    if (page.supportsWrites !== true) fail('READ_ONLY_PROVIDER');
    const identity = await request(page, origin, 'GET', profile.identity.path); checkEnvelope(identity, profile);
    if (String(get(identity, profile.identity.subjectPath)) !== expectedSubject) fail('IDENTITY_CHANGED');
    const before = await request(page, origin, 'GET', rule.listPath); checkEnvelope(before, profile);
    const items = get(before, rule.listItemsPath); if (!Array.isArray(items)) fail('TOKEN_REVOKE_UNVERIFIED');
    if (items.some(x => String(x.id) === record.id)) {
      checkEnvelope(await request(page, origin, revoke.method, revoke.path), profile);
      const after = await request(page, origin, 'GET', rule.listPath); checkEnvelope(after, profile);
      const remaining = get(after, rule.listItemsPath);
      if (!Array.isArray(remaining) || remaining.some(x => String(x.id) === record.id)) fail('TOKEN_REVOKE_UNVERIFIED');
    }
    removeToken(secretFile, inode);
    return { state: 'REVOKED', tool: record.tool, recordId: record.id, credentialRemoved: true };
  } finally { if (!suppliedPage) await page?.close?.(); }
}
module.exports = { issueToken, revokeToken };

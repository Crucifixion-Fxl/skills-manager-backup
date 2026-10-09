'use strict';
const { fail, httpsOrigin } = require('./gateway');
const { readToken } = require('./token-custody');
function credentialEnvironment({ profile, secretFile, expectedSubject, applicationId, now = Date.now } = {}) {
  const { record } = readToken(secretFile);
  if (record.tool !== profile.tool || record.origin !== httpsOrigin(profile.origin)
    || !expectedSubject || record.subject !== expectedSubject) fail('IDENTITY_CHANGED');
  const rule = profile.tokenIssuers?.[record.issuer];
  if (!rule || !/^[A-Z][A-Z0-9_]+$/.test(profile.tokenEnv || '')
    || typeof rule.secretPattern !== 'string' || !new RegExp(rule.secretPattern).test(record.secret)) fail('TOKEN_ISSUER_NOT_REGISTERED');
  if (record.state !== 'VERIFIED') fail('TOKEN_UNVERIFIED');
  if (!Number.isSafeInteger(record.expiresAt) || record.expiresAt <= now()) fail('EXPIRED');
  if (rule.resourceField && (applicationId === undefined || String(applicationId) !== String(record.resource))) fail('RESOURCE_MISMATCH');
  return { environment: { [profile.tokenEnv]: record.secret }, origin: record.origin, issuer: record.issuer, resource: record.resource };
}
module.exports = { credentialEnvironment };

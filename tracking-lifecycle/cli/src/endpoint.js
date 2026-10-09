'use strict';

const { ContractError } = require('./contracts');

function reviewedHttpsEndpoint(value) {
  let url;
  try { url = new URL(value); }
  catch { throw new ContractError('INVALID_ENDPOINT', 'explicit HTTPS service endpoint required'); }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password ||
      url.search || url.hash || url.pathname !== '/') {
    throw new ContractError('INVALID_ENDPOINT', 'service endpoint must be an HTTPS origin without credentials or query');
  }
  return url.origin;
}

module.exports = { reviewedHttpsEndpoint };

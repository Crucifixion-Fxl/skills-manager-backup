'use strict';
const { AccessError, fail, plain } = require('./gateway');
const { operationRequest } = require('./operation-input');
function nativeAdapter({ provider, profile }) {
  if (!profile.identity?.path || !profile.identity?.subjectPath || !plain(profile.operations)) fail('INVALID_PROFILE');
  let permissions;
  const checked = async (path, method = 'GET', body, effect = 'read') => {
    const response = await provider.request(method, path, body, { effect });
    if (profile.response) {
      const actual = profile.response.successPath?.split('.').reduce((v, k) => v?.[k], response);
      if (profile.response.authExpiredValues?.includes(actual)) fail('AUTH_REQUIRED');
      if (!profile.response.successPath || actual !== profile.response.successValue) fail('API_REQUEST_FAILED');
    }
    return response;
  };
  const adapter = {
    async verify() {
      permissions = undefined;
      let response;
      try { response = await checked(profile.identity.path, profile.identity.method || 'GET', profile.identity.body); }
      catch (error) {
        if (error.code !== 'AUTH_REQUIRED' || !provider.poll) throw error;
        const recovery = await provider.poll();
        if (recovery.state === 'VERIFYING') response = await checked(profile.identity.path, profile.identity.method || 'GET', profile.identity.body);
        else if (['REVOKED', 'EXPIRED', 'UNSUPPORTED'].includes(recovery.state)) fail(recovery.state);
        else { const needed = new AccessError('AUTH_REQUIRED'); needed.publicRecovery = recovery; throw needed; }
      }
      const subject = profile.identity.subjectPath.split('.').reduce((v, k) => v?.[k], response);
      if (!['number', 'string'].includes(typeof subject) || !String(subject)) fail('IDENTITY_UNVERIFIED');
      if (profile.permissionProbe) permissions = await checked(profile.permissionProbe);
      return { subject: String(subject) };
    },
    prepare(route, data, { scope = {}, allowWrites = false }) {
      if (route !== '/v1/request') fail('UNKNOWN_OPERATION');
      if (Object.keys(data).some(k => !['origin', 'operation', 'input'].includes(k)) || !plain(data.input || {})) fail('INVALID_INPUT');
      const op = Object.hasOwn(profile.operations, data.operation) ? profile.operations[data.operation] : undefined;
      if (!op) fail('UNKNOWN_OPERATION');
      if (!['read', 'write'].includes(op.effect) || op.secretResponse || op.workflow) fail('DOMAIN_WORKFLOW_REQUIRED');
      if (op.effect === 'write') {
        if (op.writeContract?.kind !== 'crud' || !op.writeContract.resultProbe) fail('DOMAIN_WORKFLOW_REQUIRED');
        if (!allowWrites) fail('READ_ONLY_BRIDGE');
        if (!Array.isArray(scope.allowedOperations) || !scope.allowedOperations.includes(data.operation)) fail('OPERATION_NOT_ALLOWED');
      }
      const request = operationRequest(op, data.input || {}, scope);
      if (op.effect === 'write' && (request.method === 'GET' || ['PUT', 'PATCH', 'DELETE'].includes(request.method) && !op.writeContract.preflight)) fail('INVALID_PROFILE');
      const bindings = op.responseBindings || [];
      if (!Array.isArray(bindings) || bindings.some(rule => !rule.binding || !rule.path)) fail('INVALID_PROFILE');
      for (const rule of bindings) if (!Object.hasOwn(scope, rule.binding) || !String(scope[rule.binding] || '')) fail('RESOURCE_SCOPE_REQUIRED');
      return async () => {
        if (op.permissionPath) {
          const permission = op.permissionPath.split('.').reduce((v, k) => v?.[k], permissions);
          if (permission !== true) fail('API_PERMISSION_DENIED');
        }
        const getPath = (value, path) => path.split('.').reduce((v, key) => v?.[key], value);
        const probeInput = (rules = {}) => Object.fromEntries(Object.entries(rules).map(([key, path]) => {
          const value = getPath(data.input || {}, path);
          if (value === undefined) fail('INVALID_INPUT');
          return [key, value];
        }));
        const probe = async (name, input) => {
          if (profile.operations[name]?.effect !== 'read') fail('INVALID_PROFILE');
          return adapter.prepare('/v1/request', { operation: name, input }, { scope })();
        };
        const contract = op.writeContract;
        if (contract?.preflight) {
          const current = await probe(contract.preflight.operation, probeInput(contract.preflight.input));
          for (const [field, value] of Object.entries(contract.preflight.expected || {})) {
            if (JSON.stringify(getPath(current, field)) !== JSON.stringify(value)) fail('RESOURCE_MISMATCH');
          }
        }
        const response = await checked(request.path, request.method, request.body, op.effect);
        for (const rule of bindings) {
          const visit = (value, parts) => {
            if (!parts.length) { if (String(value ?? '') !== String(scope[rule.binding])) fail('RESOURCE_MISMATCH'); return; }
            const [key, ...rest] = parts;
            if (key === '*') { if (!Array.isArray(value)) fail('RESOURCE_MISMATCH'); value.forEach(item => visit(item, rest)); }
            else { if (!plain(value) || !Object.hasOwn(value, key)) fail('RESOURCE_MISMATCH'); visit(value[key], rest); }
          };
          visit(response, rule.path.split('.'));
        }
        if (contract?.resultProbe) {
          const rule = contract.resultProbe;
          let input = probeInput(rule.input);
          if (rule.idPath) {
            const id = getPath(response, rule.idPath);
            if (id === undefined || !rule.inputName) fail('WRITE_RESULT_UNVERIFIED');
            input[rule.inputName] = String(id);
          }
          try {
            let result;
            try { result = await probe(rule.operation, input); }
            catch (error) { if (rule.absent && error.code === 'RESOURCE_NOT_FOUND') return response; throw error; }
            if (rule.absent) fail('WRITE_RESULT_UNVERIFIED');
            for (const [field, source] of Object.entries(rule.matches || {})) {
              if (JSON.stringify(getPath(result, field)) !== JSON.stringify(getPath(data.input || {}, source))) fail('WRITE_RESULT_UNVERIFIED');
            }
          } catch { fail('WRITE_RESULT_UNVERIFIED'); }
        }
        return response;
      };
    },
    close: () => provider.close?.(),
  };
  return adapter;
}
module.exports = { nativeAdapter };

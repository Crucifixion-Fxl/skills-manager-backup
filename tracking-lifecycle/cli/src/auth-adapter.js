'use strict';
const { ContractError } = require('./contracts');
const { planOperation } = require('./platform-admin');
const { PlatformClient, postChanges, createWorkorder } = require('./platform');
const { IgluReader } = require('./release');

const denied = code => { throw new ContractError(code, code); };
const accountReads = new Set(['login.getCurrentUser', 'userRole.getCurrentUserRole', 'userRole.isAdmin']);
const detailReads = new Set(['context.detail', 'baseSchema.getDetailByBaseSchemaId', 'trackerInfo.getEventDetail']);
function createTrackingAdapter({ transport, applicationId, contractHost, env = process.env } = {}) {
  const app = String(applicationId || '');
  if (app !== '*' && !/^[1-9][0-9]*$/.test(app)) denied('APPLICATION_REQUIRED');
  const client = new PlatformClient({ request: transport.request.bind(transport) });
  const match = value => app === '*' || String(value) === app;
  const resource = input => {
    const values = [input?.query?.applicationId, input?.query?.parentApplicationId,
      input?.body?.applicationId, input?.body?.parentApplicationId].filter(v => v !== undefined);
    if (values.some(v => !match(v))) denied('RESOURCE_MISMATCH');
    return values.length > 0;
  };
  return {
    async verify() {
      const user = await transport.request('GET', '/api/user/getCurrentUser');
      if (!user?.userId) denied('AUTH_REQUIRED');
      if (app !== '*') {
        const payload = await transport.request('GET', '/api/info/getAllApplication');
        const applications = Array.isArray(payload) ? payload : payload?.records;
        if (!Array.isArray(applications) || !applications.some(a => match(a.id ?? a.applicationId))) denied('API_PERMISSION_DENIED');
      }
      return { subject: String(user.userId) };
    },
    prepare(route, data, { allowWrites }) {
      if (route === '/v1/request') {
        if (Object.keys(data).some(k => !['origin', 'operation', 'input'].includes(k))) denied('INVALID_API_INPUT');
        const plan = planOperation(data.operation, data.input);
        if (plan.auth === 'mcp') denied('INVALID_AUTH');
        if (plan.effect === 'authentication') denied('LOGIN_WORKFLOW_REQUIRED');
        if (plan.workflow) denied(plan.workflow === 'publish' ? 'PROTECTED_CI_REQUIRED' : 'TRACKING_WORKFLOW_REQUIRED');
        if (plan.secretResponse) denied('PRIVATE_RESPONSE_REQUIRED');
        if (plan.effect !== 'read' && !allowWrites) denied('READ_ONLY_BRIDGE');
        // A caller-supplied App cannot prove ownership of every raw update/delete target.
        if (plan.effect !== 'read') denied('TRACKING_WORKFLOW_REQUIRED');
        const scoped = resource(data.input);
        if (app !== '*' && !scoped && !accountReads.has(plan.id) && !detailReads.has(plan.id)) denied('UNSCOPED_OPERATION');
        return async () => {
          const result = await transport.request(plan.method, plan.requestPath, plan.body);
          if (app !== '*' && detailReads.has(plan.id)
              && !match(result?.parentApplicationId ?? result?.applicationId)) denied('RESOURCE_MISMATCH');
          return result;
        };
      }
      if (!['/v1/post', '/v1/create-workorder'].includes(route)) denied('UNKNOWN_OPERATION');
      if (!allowWrites) denied('READ_ONLY_BRIDGE');
      const allowed = route === '/v1/post' ? ['origin', 'baseline', 'changes', 'releaseId', 'previousLock'] : ['origin', 'baseline', 'options'];
      if (Object.keys(data).some(k => !allowed.includes(k))) denied('INVALID_API_INPUT');
      if (!match(data.baseline?.applicationId)) denied('RESOURCE_MISMATCH');
      if (!contractHost || data.baseline?.host !== contractHost) denied('CONTRACT_HOST_MISMATCH');
      if (route === '/v1/post') return () => postChanges(client, data.baseline, data.changes, data.releaseId, data.previousLock);
      const baseUrls = (env.TRACKING_PROD_SCHEMA_BASE || '').split(',').filter(Boolean);
      if (!baseUrls.length) denied('INVALID_ENDPOINT');
      return () => createWorkorder(client, new IgluReader({ baseUrls }), data.baseline, data.options);
    },
    close: () => transport.close(),
  };
}
module.exports = { createTrackingAdapter };

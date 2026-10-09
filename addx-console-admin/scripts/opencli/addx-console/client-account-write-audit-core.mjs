// Pure source-contract intent audit. No HTTP, resource values, payload, or CLI registration.
export function auditClientAccountIntent(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('MALFORMED_INTENT');
  if (Object.keys(input).some(k=>!['action','mode','roles','state'].includes(k))) throw new Error('UNKNOWN_FIELD');
  if (input.mode !== undefined && input.mode !== 'dry-run') throw new Error('SUBMIT_DENIED');
  if (!['create','edit','freeze','enable','reset'].includes(input.action)) throw new Error('ACTION_NOT_PROVEN');
  if (input.roles !== undefined && input.roles !== 'complete-replacement') throw new Error('ROLE_SET_INTENT_NOT_COMPLETE');
  if (input.state !== undefined && !['summary','private-authoritative-declaration'].includes(input.state)) throw new Error('STATE_INTENT_NOT_PROVEN');
  const effects = input.action === 'create' ? ['ACCOUNT_INSERT','ROLE_SET_REPLACE','CREDENTIAL_EMAIL']
    : input.action === 'reset' ? ['PASSWORD_DB_UPDATE','REDIS_LOGIN_MAPPING_DELETE']
    : ['ACCOUNT_UPDATE','OPTIONAL_ROLE_SET_REPLACE','LOGIN_CACHE_CLEAR','PERMISSION_CHANGE_EVENT'];
  return Object.freeze({action:input.action, mode:'dry-run', effects,
    operation:['freeze','enable'].includes(input.action)?'SAVE_STATUS_WITH_CACHE_AND_EVENT':input.action.toUpperCase(),
    roleSemantics:'NONEMPTY_REPLACES_EMPTY_PRESERVES',
    readiness:'FULL_PRIVATE_STATE_AND_DEPLOYMENT_UNPROVEN',
    submitAllowed:false,payloadAvailable:false,liveAccepted:false,
    blockers:['SUMMARY_OMITS_CONTACT_AND_ASSOCIATIONS','NO_ATOMIC_VERSION_GUARD','DEPLOYED_PERMISSION_SCOPE_UNPROVEN',
      'DECLARATION_IS_NOT_AUTHORIZATION','EMAIL_RESULT_NOT_DELIVERY_PROOF','UPDATE_AFFECTED_COUNT_UNCHECKED']});
}

// Source-only intent. No resource IDs, financial/contact values, HTTP, payload or CLI registration.
export function auditSimAgreementIntent(input){
 if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('MALFORMED_INTENT');
 if(Object.keys(input).some(k=>!['action','mode','state','authority','change'].includes(k)))throw new Error('UNKNOWN_FIELD');
 if(input.mode!==undefined&&input.mode!=='dry-run')throw new Error('SUBMIT_DENIED');
 if(!['create','edit'].includes(input.action))throw new Error('ACTION_NOT_PROVEN');
 if(input.state!==undefined&&!['log-metadata','private-state-declaration'].includes(input.state))throw new Error('STATE_NOT_PROVEN');
 if(input.authority!==undefined&&input.authority!=='ldap-declaration')throw new Error('AUTHORITY_NOT_PROVEN');
 if(input.change!==undefined&&input.change!=='unchanged-declaration')throw new Error('CHANGE_NOT_PROVEN');
 return Object.freeze({action:input.action,mode:'dry-run',
 phases:input.action==='create'?['OPTIONAL_INCENTIVE_WRITE','OVERLAP_REJECTION','AGREEMENT_INSERT','LOG_APPEND']:['OPTIONAL_INCENTIVE_WRITE','OVERLAP_REJECTION','AGREEMENT_UPDATE','LOG_APPEND','MONTHLY_CHANGE_NOTICE'],
 monthlyChangeNotice:input.action==='edit',failureMayHaveWrites:true,ownershipProven:false,fullStateProven:false,noOpProven:false,
 submitAllowed:false,payloadAvailable:false,liveAccepted:false,
 blockers:['INCENTIVE_BEFORE_REJECTION','NO_COMMON_TRANSACTION','NO_CAS','LDAP_IS_NOT_OWNER','LOGS_NOT_FULL_STATE','HISTORICAL_REPORT_EFFECT_UNPROVEN','NO_IDEMPOTENT_READBACK_RECEIPT']});
}

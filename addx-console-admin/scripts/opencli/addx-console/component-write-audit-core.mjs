// Pure source audit; no HTTP, payload, values, credentials or CLI registration.
export function auditComponentIntent(input){
 if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('MALFORMED_INTENT');
 if(Object.keys(input).some(k=>!['action','mode','state','change'].includes(k)))throw new Error('UNKNOWN_FIELD');
 if(input.mode!==undefined&&input.mode!=='dry-run')throw new Error('SUBMIT_DENIED');
 if(!['create','edit','release'].includes(input.action))throw new Error('ACTION_NOT_PROVEN');
 if(input.state!==undefined&&!['current-detail','published-parent-catalogue','private-state-declaration'].includes(input.state))throw new Error('STATE_NOT_PROVEN');
 if(input.change!==undefined&&!['model-types-only','remark-only','association-change','unchanged-declaration'].includes(input.change))throw new Error('CHANGE_NOT_PROVEN');
 return Object.freeze({action:input.action,mode:'dry-run',semantics:input.action==='create'?'VERSIONED_COMPONENT_AND_ASSOCIATION_INSERTS':input.action==='release'?'GIT_BRANCH_PUSH_PR_THEN_DB_TRACKING':'CONDITIONAL_VERSION_UPDATE_AND_ASSOCIATION_INSERTS',
  saveAndRelease:false,associationStrategy:'VERSION_INSERT_NOT_ATOMIC_REPLACE',
  fullStateProven:false,noOpProven:false,externalEffects:input.action==='release',deploymentProven:false,
  submitAllowed:false,payloadAvailable:false,liveAccepted:false,
  blockers:['NO_CAS_OWNER_DEPLOYMENT_PROOF','SECOND_RESOLUTION_VERSION_COLLISION','CURRENT_DETAIL_NOT_FULL_STATE','CHANGE_DETECTION_NOT_NOOP_PROOF','GIT_DB_TRACKING_NONATOMIC','DECLARATION_NOT_AUTHORIZATION']});
}

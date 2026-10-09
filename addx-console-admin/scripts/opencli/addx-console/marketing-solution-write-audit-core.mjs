// Offline source intent only: no resource values, network, payload, or registration.
export function auditMarketingSolutionIntent(input) {
 if(!input || typeof input!=='object' || Array.isArray(input))throw new Error('MALFORMED_INTENT');
 if(Object.keys(input).some(k=>!['action','mode','state'].includes(k)))throw new Error('UNKNOWN_FIELD');
 if(input.mode!==undefined && input.mode!=='dry-run')throw new Error('SUBMIT_DENIED');
 const semantics={add:'INSERT_WITH_FRESH_UUID',clone:'INSERT_WITH_FRESH_UUID',
  'sync-solution':'INSERT_PRESERVING_UUID_DUPLICATE_IS_ERROR','sync-slot':'CHECK_NAME_THEN_INSERT_DUPLICATE_IS_ERROR',
  'migrate-media-ids':'BATCH_REWRITE_MODULES_WITH_SKIP_PARTIAL_FAILURE'};
 if(!Object.hasOwn(semantics,input.action))throw new Error('ACTION_NOT_PROVEN');
 if(input.state!==undefined && !['browser-cache','private-state-declaration'].includes(input.state))throw new Error('STATE_NOT_PROVEN');
 return Object.freeze({action:input.action,mode:'dry-run',semantics:semantics[input.action],
  atomicCascade:false,authoritativeOriginalState:false,submitAllowed:false,payloadAvailable:false,liveAccepted:false,
  readiness:'SAVED_STATE_TARGET_DEPENDENCIES_AND_DEPLOYED_AUTHORIZATION_UNPROVEN',
  blockers:['NO_CAS','CACHE_IS_NOT_SAVED_STATE','CROSS_ENVIRONMENT_SCOPE_UNVERIFIED','NO_ATOMIC_SLOT_CREATIVE_SOLUTION_TRANSACTION',
   'DUPLICATE_IS_NOT_CONTENT_EQUALITY_PROOF','AFFECTED_COUNTS_NOT_SAVED_OBJECT_RECEIPT','DECLARATION_NOT_AUTHORIZATION']});
}

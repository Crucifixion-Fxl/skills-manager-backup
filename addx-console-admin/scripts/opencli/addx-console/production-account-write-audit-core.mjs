// Pure abstract intent review; accepts no account identifiers, passwords or recipient values.
export function auditAccountIntent(i){
 const fail=()=>{throw new Error('INVALID_PRODUCTION_ACCOUNT_INTENT');};
 if(!i||typeof i!=='object'||Array.isArray(i)||Object.keys(i).some(k=>!['action','existing','targetCount','mode'].includes(k))||(i.mode!==undefined&&i.mode!=='dry-run')||i.targetCount!==1)fail();
 const state={'create':'absent','reset':'active','delete':'active','manufacturer-type-change':'present'};
 if(!Object.hasOwn(state,i.action)||i.existing!==state[i.action])fail();
 const blockers=['DEPLOYMENT_AND_ACTION_GRANT_UNVERIFIED','OWNERSHIP_UNVERIFIED','NO_ATOMIC_CAS_PROVEN','AFFECTED_ROWS_AND_READBACK_UNCHECKED','AUTHENTICATED_SERVER_FALLTHROUGH_NOT_OWNERSHIP'];
 if(i.action==='create')blockers.push('IMPLICIT_MANUFACTURER_INSERT_BEFORE_VALIDATION','ACTIVE_EMAIL_SUCCESS_NOOP','DUPLICATE_KEY_CAN_REACTIVATE_DELETED_ACCOUNT','NON_CRYPTOGRAPHIC_PASSWORD_RNG','MAIL_FAILURE_BOOLEAN_IGNORED','PASSWORD_HTML_AND_SMTP_DEBUG_RISK','PASSWORD_UPDATED_BEFORE_MAIL_DISPATCH');
 if(i.action==='reset')blockers.push('PASSWORD_UPDATED_BEFORE_MAIL_DISPATCH','NON_CRYPTOGRAPHIC_PASSWORD_RNG','MAIL_FAILURE_BOOLEAN_IGNORED','PASSWORD_HTML_AND_SMTP_DEBUG_RISK','EMAIL_UPDATE_NOT_OWNER_SCOPED','ABSENT_EMAIL_SUCCESS_NOOP');
 if(i.action==='delete')blockers.push('EMAIL_UPDATE_NOT_OWNER_SCOPED','SOFT_DELETE_NOT_CREDENTIAL_REVOCATION_PROOF');
 if(i.action==='manufacturer-type-change')blockers.push('SAFETY_CHECK_COUNTS_EXCLUDE_INACTIVE_ROWS','CHECK_THEN_UPDATE_RACE');
 return {status:'OFFLINE_INTENT_ONLY',action:i.action,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,refusalCategory:'SOURCE_OR_AUTHORIZATION_UNPROVEN',blockers};
}

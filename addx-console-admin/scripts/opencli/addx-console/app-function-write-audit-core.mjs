// Pure source-audit intent classification. No payload, transport, credentials or CLI registration.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,publishFlagFixed:false,authorityVerified:false,serverCASProvided:false,catalogueViewIsCompleteSavedState:false};
export function assessAppFunctionIntent(input={mode:'dry-run',action:'template'}){
 if(!input||typeof input!=='object'||Array.isArray(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','modelNo'].includes(k))||!['template','step1-save','step2-save','tier-save','release'].includes(input.action))throw new Error('INVALID_OFFLINE_APP_FUNCTION_INTENT');
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY'};
 if(typeof input.modelNo!=='string'||!input.modelNo.trim()||input.modelNo.length>128)throw new Error('INVALID_OFFLINE_APP_FUNCTION_INTENT');
 const status={'step1-save':'BLOCKED_VERSIONED_FULL_REPLACEMENT_AUTHORITY_CAS','step2-save':'BLOCKED_VERSIONED_FULL_REPLACEMENT_AUTHORITY_CAS','tier-save':'BLOCKED_FULL_ASSOCIATIONS_CAS_AND_REMOTE_READ','release':'BLOCKED_GIT_PROD_RELEASE_SCOPE'}[input.action];
 return {...base,status,releaseTouchesGit:input.action==='release',epochSecondVersionIsCAS:false,plainEvidenceCannotAuthorize:true};
}

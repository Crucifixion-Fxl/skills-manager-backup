// Pure metadata intent audit: never constructs upload/body nor registers a command.
export function auditCdIntent(i){
 const fail=()=>{throw new Error('INVALID_CD_INTENT');};
 if(!i||typeof i!=='object'||Array.isArray(i))fail();
 if(!['certificate-create','certificate-edit','info-edit','model-replace'].includes(i.action))fail();
 const mapping=i.action==='model-replace';const keys=['action','mode','existing','fullState',...(mapping?['desiredCount','allowClear']:[])];
 if(Object.keys(i).some(k=>!keys.includes(k))||(i.mode!==undefined&&i.mode!=='dry-run')||i.fullState!==true)fail();
 if(i.existing!==(i.action==='certificate-create'?'absent':'present'))fail();
 if(mapping&&(!Number.isSafeInteger(i.desiredCount)||i.desiredCount<0||(i.desiredCount===0&&i.allowClear!==true)||(i.allowClear!==undefined&&typeof i.allowClear!=='boolean')))fail();
 const blockers=['DEPLOYMENT_UNVERIFIED','OWNERSHIP_UNVERIFIED','NO_ATOMIC_CAS_PROVEN','UPDATE_AFFECTED_ROWS_UNCHECKED'];
 if(mapping)blockers.push('FULL_MAPPING_REPLACEMENT','PARENT_EXISTENCE_AND_MODEL_BINDING_UNGUARDED','TRANSACTION_RUNTIME_UNVERIFIED');
 if(i.action.startsWith('certificate-'))blockers.push('CERTIFICATE_CONTENT_VALIDATION_UNPROVEN','FILE_AND_SIZE_BOUNDS_UNPROVEN','CHECK_THEN_WRITE_UNIQUENESS_RACE');
 return {status:'OFFLINE_INTENT_ONLY',action:i.action,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,refusalCategory:'AUTHORIZATION_AND_CONCURRENCY_UNPROVEN',blockers};
}

// Pure intent review only: no request payload, network consumer or command registration.
export function auditDacIntent(input) {
  const fail=()=>{throw new Error('INVALID_DAC_OFFLINE_INTENT');};
  if(!input || typeof input!=='object' || Array.isArray(input)) fail();
  const generate=input.action==='generate';
  if(!generate && !['alert-create','alert-edit'].includes(input.action)) fail();
  const keys=generate ? ['action','mode','modelCount','quantity'] : ['action','mode','modelCount','existing','fullState'];
  if(Object.keys(input).some(k=>!keys.includes(k)) || (input.mode!==undefined && input.mode!=='dry-run')) fail();
  if(!Number.isSafeInteger(input.modelCount)||input.modelCount<1) fail();
  if(generate) {
    // Positive-only planning is intentionally narrower than the server's zero-quantity acceptance.
    if(!Number.isSafeInteger(input.quantity)||input.quantity<1) fail();
  } else if(input.fullState!==true || input.existing!==(input.action==='alert-create'?'absent':'present')) fail();
  const blockers=['DEPLOYMENT_UNVERIFIED','PERMISSION_UNVERIFIED','NO_ATOMIC_CAS_PROVEN'];
  if(generate) blockers.push('ASYNC_ISSUANCE_NOT_COMPLETION','PARTIAL_INSERT_AND_EXTERNAL_ISSUANCE','SERVER_QUANTITY_AND_CHUNK_BOUNDS_UNPROVEN','ACTOR_BINDING_NOT_PROVEN','PRIVATE_KEY_MATERIAL_EXCLUDED');
  else blockers.push('REPLACE_NOT_PATCH','FULL_RECIPIENT_STATE_REQUIRES_PRIVATE_REVIEW','CREATE_EDIT_ONLY_INTENT_NOT_BACKEND_GUARD','NEGATIVE_THRESHOLD_SCHEMA_MISMATCH_UNRESOLVED');
  return {status:'OFFLINE_INTENT_ONLY',action:input.action,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,blockers};
}

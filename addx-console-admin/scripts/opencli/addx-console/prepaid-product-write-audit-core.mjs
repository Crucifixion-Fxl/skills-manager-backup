// Abstract offline field-intent only: no price, identifiers, billing, tenant values or request body.
export function auditPrepaidProductIntent(i){
 const fail=()=>{throw new Error('INVALID_PREPAID_PRODUCT_WRITE_INTENT');};
 if(!i||typeof i!=='object'||Array.isArray(i)||!['create','edit'].includes(i.action))fail();
 const keys=['action','existing','stateOrigin','changedFields','mode',...(i.action==='edit'?['immutablePreserved']:[])];
 if(Object.keys(i).some(k=>!keys.includes(k))||(i.mode!==undefined&&i.mode!=='dry-run')||i.existing!==(i.action==='create'?'absent':'present')||i.stateOrigin!=='private-authoritative'||(i.action==='edit'&&i.immutablePreserved!==true))fail();
 const allowed=i.action==='edit'?['cancel','currency','price']:['productName','cancel','currency','price','tierLevel','oemType','tenantId'];
 if(!Array.isArray(i.changedFields)||!i.changedFields.length||new Set(i.changedFields).size!==i.changedFields.length||i.changedFields.some(k=>!allowed.includes(k)))fail();
 return {status:'OFFLINE_INTENT_ONLY',action:i.action,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,refusalCategory:'PRICING_FULL_STATE_AUTHORIZATION_AND_CONCURRENCY_UNPROVEN',blockers:['NO_ATOMIC_CAS_PROVEN','DETAIL_RESPONSE_OMITS_TENANT_SCOPE','STATE_DECLARATION_NOT_RUNTIME_PROOF','PRICE_SCOPE_AND_DEPLOYMENT_UNVERIFIED','NO_PUBLISH_CHAIN_IN_SAVE','UPDATE_AFFECTED_ROWS_UNCHECKED','ENTITY_BINDING_NOT_UI_IMMUTABILITY','NULL_UPDATE_STRATEGY_UNPROVEN',...(i.action==='create'?['BACKEND_CREATE_NO_FRONTEND_CREATE_ENTRY']:[])]};
}

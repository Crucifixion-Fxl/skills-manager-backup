// Pure offline factory/custom-plan readiness; no transport, credentials, payload or CLI registration.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,publishFlagFixed:false,authorityVerified:false};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x),pos=x=>Number.isSafeInteger(x)&&x>0,str=x=>typeof x==='string'&&x.length>0&&x.length<=128;
function refuse(){throw new Error('INVALID_OFFLINE_FACTORY_INTENT_OR_METADATA')}
export function assessFactoryProcessIntent(input={mode:'dry-run',action:'template'},observation=null){
 if(!obj(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','manufacturerId','modelNo','planDetailId','id'].includes(k))||!['template','custom-create-draft','custom-edit-draft','custom-delete','use-default','publish-default','publish-custom','default-factory-unlink'].includes(input.action))refuse();
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY'};
 if(!pos(input.manufacturerId)||!str(input.modelNo)||!pos(input.planDetailId)||!obj(observation)||observation.source!=='factory-model-list'||!Array.isArray(observation.rows))refuse();
 const rows=observation.rows.filter(r=>r?.manufacturerId===input.manufacturerId&&r?.modelNo===input.modelNo&&r?.planDetailId===input.planDetailId);if(rows.length!==1)refuse();const row=rows[0],custom=row.modelProducePlan;
 if(input.action==='custom-create-draft'){
  if(input.id!==undefined||custom!==null)refuse();return {...base,status:'BLOCKED_CUSTOM_CREATE_UNIQUENESS_AND_SCOPE',defaultViewIsSavedOverride:false};
 }
 if(input.action==='custom-edit-draft'||input.action==='publish-custom'){
  if(!obj(custom)||!pos(input.id)||custom.id!==input.id||custom.manufacturerId!==input.manufacturerId||custom.modelNo!==input.modelNo||custom.planDetailId!==input.planDetailId||![0,1].includes(custom.publishStatus))refuse();
 }
 const statuses={'custom-edit-draft':'BLOCKED_RAW_STATE_AUTHORITY_AND_CAS','custom-delete':'UNSUPPORTED_NO_CUSTOM_DELETE_HANDLER','use-default':'UNSUPPORTED_NO_CUSTOM_OVERRIDE_REMOVAL_CONTRACT','publish-default':'BLOCKED_ALL_ASSOCIATED_FACTORY_RUNTIME_SCOPE','publish-custom':'BLOCKED_RUNTIME_MATERIALIZATION_AND_MODEL_SCOPE','default-factory-unlink':'BLOCKED_DEFAULT_LINK_OWNERSHIP_AND_LAST_LINK_CLEANUP'};
 return {...base,status:statuses[input.action],serverCASProvided:false,fullRawConfigVerified:false,runtimeMaterializationVerified:false,publishedDBFlagIsRuntimeProof:false,oldPlanRuntimeRetentionRisk:true,crossFactoryDefaultPublicationPossible:input.action==='publish-default',unlinkIsNotCustomDelete:true};
}

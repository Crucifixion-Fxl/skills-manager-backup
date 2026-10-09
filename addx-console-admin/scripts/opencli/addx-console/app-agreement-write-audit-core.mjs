// Pure offline metadata intent audit. No HTTP, payload, CLI registration or credentials.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,authorityVerified:false,fullRawStateVerified:false};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);const str=x=>typeof x==='string'&&x.length>0&&x.length<=256;
function refuse(){throw new Error('INVALID_OFFLINE_AGREEMENT_INTENT_OR_METADATA')}
export function assessAgreementIntent(input={mode:'dry-run',action:'template'},observation=null){
 if(!obj(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','policyId','templateId','tenantId','host','templateName'].includes(k)))refuse();
 const actions=['template','policy-create','policy-edit-draft','policy-delete','template-create','template-edit','template-delete-by-name','template-publish-by-name','template-status-by-name'];if(!actions.includes(input.action))refuse();
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY'};
 if(!obj(observation)||!Array.isArray(observation.rows))refuse();
 if(input.action.startsWith('policy-')){
  if(observation.source!=='active-policy-cards'||![input.templateId,input.tenantId,input.host].every(str)||input.templateName!==undefined)refuse();
  const rows=observation.rows.filter(r=>r?.templateId===input.templateId&&r?.tenantId===input.tenantId&&r?.iotHostDomain===input.host);if(rows.length!==1)refuse();const r=rows[0];
  if(![0,1].includes(r.publishStatus))refuse();
  if(input.action==='policy-create'){
   if(input.policyId!==undefined||r.policyId!==undefined&&r.policyId!==null&&r.policyId!=='')refuse();
   return {...base,status:r.publishStatus===1?'BLOCKED_DRAFT_CAN_PUBLISH_S3':'BLOCKED_CREATE_OWNERSHIP_AND_TEMPLATE_RACE',placeholderIsSaved:false,publishParameterHonored:false};
  }
  if(!str(input.policyId)||r.policyId!==input.policyId)refuse();
  if(input.action==='policy-delete')return {...base,status:'BLOCKED_PATH_VARIABLE_BINDING_MISMATCH',externalS3DeletionPossible:true};
  return {...base,status:r.publishStatus===1?'BLOCKED_DRAFT_CAN_PUBLISH_S3':'BLOCKED_RAW_STATE_AUTHORITY_AND_CAS',publishParameterHonored:false,activeCardsOmitDisabledAndDuplicatePolicies:true,serverCASProvided:false,templateStatusCanRace:true};
 }
 if(observation.source!=='template-rows'||input.policyId!==undefined||input.tenantId!==undefined||input.host!==undefined)refuse();
 if(input.action==='template-create'){
  if(input.templateId!==undefined||!str(input.templateName))refuse();return {...base,status:'BLOCKED_CREATE_UNIQUENESS_AND_FAMILY_RENAME'};
 }
 if(input.action==='template-edit'){
  if(!str(input.templateId))refuse();const rows=observation.rows.filter(r=>r?.templateId===input.templateId);if(rows.length!==1)refuse();return {...base,status:'BLOCKED_TEMPLATE_FAMILY_RENAME_AUTHORITY_AND_CAS',codeAndLanguageServerImmutable:true,saveSetsPublishStatus:0};
 }
 if(!str(input.templateName)||input.templateId!==undefined)refuse();const rows=observation.rows.filter(r=>r?.templateName===input.templateName);if(rows.length===0||rows.some(r=>!str(r.templateId))||new Set(rows.map(r=>r.templateId)).size!==rows.length)refuse();
 return {...base,status:'BLOCKED_ALL_POLICIES_HOSTS_ENVIRONMENTS_SCOPE',observedTemplateCount:rows.length,templateNameTargetsAllLanguages:true,externalS3MutationPossible:true,asyncFailureCanStillReturnSuccess:true};
}

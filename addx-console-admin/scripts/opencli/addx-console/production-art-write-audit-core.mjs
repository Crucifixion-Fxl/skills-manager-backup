import {createHash} from 'node:crypto';
// Pure offline audit: no CLI registration, transport, credentials or writable payload.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,publishFlagFixed:false,authorityVerified:false};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);const positive=x=>Number.isSafeInteger(x)&&x>0;
function refuse(){throw new Error('INVALID_OFFLINE_ART_INTENT_OR_SNAPSHOT')}
export function assessProductionArtIntent(input={mode:'dry-run',action:'template'},snapshot=null){
 if(!obj(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','id','code','inProduceCodeType','inProduceCodeRuleId'].includes(k))||!['template','create','edit','publish','delete'].includes(input.action))refuse();
 const {action}=input;if(action==='template')return {...base,status:'TEMPLATE_ONLY'};
 let existing=null;
 if(action==='create'){if(input.id!==undefined||snapshot!==null)refuse()}
 else{if(!positive(input.id)||!obj(snapshot)||snapshot.source!=='unfiltered-art-rows'||!Array.isArray(snapshot.rows))refuse();const rows=snapshot.rows.filter(r=>r?.id===input.id);if(rows.length!==1||![0,1].includes(rows[0].publishStatus))refuse();existing=rows[0]}
 if(['publish','delete'].includes(action)){
  if(Object.keys(input).some(k=>!['mode','action','id'].includes(k)))refuse();
  return {...base,status:action==='delete'?'UNSUPPORTED_NO_ART_DELETE_CONTRACT':'BLOCKED_FACTORY_WIDE_PUBLICATION',factoryRuntimeScopeVerified:false};
 }
 if(![0,1,2,3,4,5].includes(input.inProduceCodeType))refuse();
 if(input.code!==undefined&&!Number.isSafeInteger(input.code))refuse();
 if(input.inProduceCodeRuleId!==undefined&&!positive(input.inProduceCodeRuleId))refuse();
 let ext={};
 if(existing){if(typeof existing.ext!=='string')refuse();try{ext=existing.ext===''?{}:JSON.parse(existing.ext);if(!obj(ext))refuse()}catch{refuse()}}
 const next={...ext};if(input.code!==undefined)next.code=input.code;if(input.inProduceCodeRuleId!==undefined)next.inProduceCodeRuleId=input.inProduceCodeRuleId;
 if(input.inProduceCodeType===5&&!positive(next.inProduceCodeRuleId))refuse();
 const changed=Object.keys(next).filter(k=>JSON.stringify(next[k])!==JSON.stringify(ext[k])).length;
 const digest=createHash('sha256').update(JSON.stringify({action,id:input.id??null,type:input.inProduceCodeType,ext,next,version:existing?.updateTimestamp??null,status:existing?.publishStatus??null})).digest('hex');
 return {...base,status:action==='create'?'BLOCKED_CREATE_AUTHORITY_AND_UNIQUENESS':'BLOCKED_AUTHORITY_LIFECYCLE_AND_CAS',retainedExtKeyCount:Object.keys(ext).length,changedExtKeyCount:changed,ruleOmissionClearsExisting:false,rawExtReplacementSupported:false,ruleExistenceAndScopeVerified:false,manualGenerateRequiresRule:input.inProduceCodeType===5,saveSetsPublishStatus:0,intentStateHash:digest,hashIsCAS:false,hashIsAuthorization:false};
}

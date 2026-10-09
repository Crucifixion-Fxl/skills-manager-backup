import {createHash} from 'node:crypto';
// Pure offline intent audit. No HTTP, credentials, CLI registration or writable payload.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildExecutablePayload:false,submissionAllowed:false,publishFlagFixed:false};
const integer=x=>Number.isSafeInteger(x)&&x>0;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
function refuse(){throw new Error('INVALID_OFFLINE_INTENT_OR_SNAPSHOT')}
function unique(xs,check){if(!Array.isArray(xs)||xs.some(x=>!check(x))||new Set(xs).size!==xs.length)refuse();return xs}
function saved(s,id){
 if(!object(s)||!object(s.producePlanDetail)||s.producePlanDetail.id!==id||!Array.isArray(s.modelNoList)||!Array.isArray(s.manufacturerIdList))refuse();
 for(const [rows,key,check] of [[s.modelNoList,'modelNo',x=>typeof x==='string'&&x.length>0],[s.manufacturerIdList,'manufacturerId',integer]]){
  unique(rows.map(x=>x?.id),integer);
  if(rows.some(x=>!object(x)||x.planDetailId!==id||!check(x[key])))refuse();
 }
 try{if(!Array.isArray(JSON.parse(s.producePlanDetail.processArtConfig))||!object(JSON.parse(s.producePlanDetail.producePlanArtConfig)))refuse()}catch{refuse()}
 return s;
}
export function assessDefaultPlanIntent(input={mode:'dry-run',action:'template'},snapshot=null){
 if(!object(input)||input.mode!=='dry-run')refuse();
 const allowed=['mode','action','planId','removeModelLinkIds','removeFactoryLinkIds','addModelNos','addFactoryIds'];
 if(Object.keys(input).some(k=>!allowed.includes(k)))refuse();
 const {action}=input;if(!['template','create','edit','delete','publish'].includes(action))refuse();
 if(action==='template')return {...base,status:'TEMPLATE_ONLY',actions:['create','edit','publish','delete'],executionSupported:false};
 if(action==='create'){
  if(snapshot!==null||input.planId!==undefined||input.removeModelLinkIds!==undefined||input.removeFactoryLinkIds!==undefined)refuse();
 }else{if(!integer(input.planId))refuse();saved(snapshot,input.planId)}
 if(['delete','publish'].includes(action)){
  if(Object.keys(input).some(k=>!['mode','action','planId'].includes(k)))refuse();
  return {...base,status:action==='delete'?'UNSUPPORTED_NO_PLAN_DELETE_CONTRACT':'BLOCKED_FACTORY_WIDE_RELEASE_SCOPE',affectedFactoryCount:snapshot.manufacturerIdList.length,authorityVerified:false,fullFactoryReleaseStateVerified:false};
 }
 const addModels=unique(input.addModelNos??[],x=>typeof x==='string'&&x.length>0&&x.length<=128);
 const addFactories=unique(input.addFactoryIds??[],integer);
 const removeModels=unique(input.removeModelLinkIds??[],integer),removeFactories=unique(input.removeFactoryLinkIds??[],integer);
 const models=snapshot?.modelNoList??[],factories=snapshot?.manufacturerIdList??[];
 if(removeModels.some(id=>!models.some(x=>x.id===id))||removeFactories.some(id=>!factories.some(x=>x.id===id)))refuse();
 const nextModels=models.filter(x=>!removeModels.includes(x.id)).map(x=>x.modelNo).concat(addModels);
 const nextFactories=factories.filter(x=>!removeFactories.includes(x.id)).map(x=>x.manufacturerId).concat(addFactories);
 unique(nextModels,x=>typeof x==='string'&&x.length>0);unique(nextFactories,integer);if(nextModels.length===0)refuse();
 // This digest detects offline snapshot drift; it is never server CAS or authorization.
 const state=snapshot?{detail:snapshot.producePlanDetail,models:models.map(x=>[x.id,x.modelNo]),factories:factories.map(x=>[x.id,x.manufacturerId])}:null;
 const digest=createHash('sha256').update(JSON.stringify({action,planId:input.planId??null,addModels,addFactories,removeModels,removeFactories,state})).digest('hex');
 return {...base,status:action==='create'?'BLOCKED_CREATE_SCOPE_AND_OCCUPANCY_UNPROVEN':'BLOCKED_AUTHORITY_AND_CAS_UNPROVEN',action,finalModelCount:nextModels.length,finalFactoryCount:nextFactories.length,deletedModelLinkCount:removeModels.length,deletedFactoryLinkCount:removeFactories.length,intentStateHash:digest,hashIsAuthorization:false,hashIsServerCAS:false,omittedAssociations:'RETAINED',relationSemantics:'DELTA_TOMBSTONES',scopeAndOccupancyVerified:false};
}

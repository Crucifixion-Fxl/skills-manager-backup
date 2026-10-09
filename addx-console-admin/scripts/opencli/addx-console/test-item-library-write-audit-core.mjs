import {createHash} from 'node:crypto';
// Pure offline parameter/relation intent semantics; no CLI registration/HTTP/credentials/payload.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,authorityVerified:false};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x),positive=x=>Number.isSafeInteger(x)&&x>0,str=x=>typeof x==='string'&&x.length>0&&x.length<=256;
function refuse(){throw new Error('INVALID_OFFLINE_LIBRARY_INTENT_OR_SNAPSHOT')}
function unique(a,fn){if(!Array.isArray(a)||a.some(x=>!fn(x))||new Set(a).size!==a.length)refuse();return a}
export function assessTestItemLibraryIntent(input={mode:'dry-run',action:'template'},saved=null){
 if(!obj(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','id','itemCode','parameterScope','parameters','removedRelationIds','addedArtIds','acknowledgeClearParameters'].includes(k))||!['template','create','edit','delete','script-upload'].includes(input.action))refuse();
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY'};
 if(input.action==='script-upload'){if(Object.keys(input).some(k=>!['mode','action'].includes(k)))refuse();return {...base,status:'OUT_OF_LIBRARY_SCOPE_FILECENTER_MANAGEMENT'}}
 if(input.action==='delete'){if(Object.keys(input).some(k=>!['mode','action','id'].includes(k))||!positive(input.id))refuse();return {...base,status:'UNSUPPORTED_NO_LIBRARY_DELETE_HANDLER'}}
 const create=input.action==='create';
 if(create){if(saved!==null||input.id!==undefined||!str(input.itemCode))refuse()}
 else{
  if(!obj(saved)||saved.id!==input.id||!positive(input.id)||input.itemCode!==saved.itemCode||!str(saved.itemCode)||!Array.isArray(saved.testItemParamDOList)||!Array.isArray(saved.produceArtRelationDOList))refuse();
  unique(saved.testItemParamDOList.map(x=>x?.id),positive);unique(saved.testItemParamDOList.map(x=>x?.paramCode),str);unique(saved.produceArtRelationDOList.map(x=>x?.id),positive);
  if(saved.testItemParamDOList.some(x=>x.testItemId!==saved.id||![1,2,3,4,5,6,7,8].includes(x.thresholdType))||saved.produceArtRelationDOList.some(x=>x.testItemId!==saved.id||!positive(x.produceArtId)))refuse();
 }
 if(input.parameterScope!=='complete-desired-set'||!Array.isArray(input.parameters)||input.parameters.some(x=>!obj(x)||Object.keys(x).some(k=>!['paramCode','thresholdType'].includes(k))||!str(x.paramCode)||![1,2,3,4,5,6,7,8].includes(x.thresholdType)))refuse();unique(input.parameters.map(x=>x.paramCode),str);
 const oldParams=saved?.testItemParamDOList??[],relations=saved?.produceArtRelationDOList??[];
 if(input.parameters.length===0&&oldParams.length>0&&input.acknowledgeClearParameters!==true)refuse();if(input.acknowledgeClearParameters!==undefined&&typeof input.acknowledgeClearParameters!=='boolean')refuse();
 const removed=unique(input.removedRelationIds??[],positive),added=unique(input.addedArtIds??[],positive);if(removed.some(id=>!relations.some(x=>x.id===id)))refuse();
 const nextArts=relations.filter(x=>!removed.includes(x.id)).map(x=>x.produceArtId).concat(added);unique(nextArts,positive);
 const nextCodes=new Set(input.parameters.map(x=>x.paramCode));const nRemoved=oldParams.filter(x=>!nextCodes.has(x.paramCode)).length;
 const digest=createHash('sha256').update(JSON.stringify({action:input.action,id:input.id??null,code:input.itemCode,oldParams:oldParams.map(x=>[x.id,x.paramCode,x.thresholdType]),relations:relations.map(x=>[x.id,x.produceArtId]),parameters:input.parameters,removed,added,time:saved?.lastModifyTime??null})).digest('hex');
 return {...base,status:'BLOCKED_AUTHORITY_MODEL_IMPACT_AND_CAS',parametersAreFullReplacement:true,relationsAreExplicitDeltas:true,parameterCount:input.parameters.length,removedParameterCount:nRemoved,removedArtRelationCount:removed.length,addedArtRelationCount:added.length,deletesAssociatedModelConfigurationsPossible:removed.length>0,dependentModelScopeVerified:false,versionCASProvided:false,intentStateHash:digest,hashIsAuthorization:false,hashIsServerCAS:false};
}

import {createHash} from 'node:crypto';
const positive=v=>{if(!Number.isSafeInteger(v)||v<1)throw new Error('Positive safe binding ID required');return v;};
const model=v=>{if(typeof v!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v))throw new Error('Exact safe model required');return v;};
const list=v=>{if(!Array.isArray(v)||v.length>1000)throw new Error('Bounded binding metadata list required');return v;};
export function assessBindingSaveIntent(args,view,observedListRows){
 if((args.mode??'dry-run')!=='dry-run')throw new Error('Submit permanently unsupported; no complete persisted state');
 const action=args.action??'template';
 if(!['template','assess'].includes(action)||Object.keys(args).some(k=>!['mode','action','intent'].includes(k)))throw new Error('Unknown binding audit action/fields');
 if(action==='template'){
  if(args.intent!==undefined)throw new Error('Template forbids submitted intent');
  return {status:'TEMPLATE_ONLY_NOT_A_SAVE_PLAN',intentExample:{modelNo:'EXISTING_MODEL',artPlanId:1,artIds:[1]},saveAndRelease:false,submissionImplemented:false,canBuildExecutablePayload:false};
 }
 const intent=args.intent;
 if(!intent||typeof intent!=='object'||Array.isArray(intent)||Object.keys(intent).length!==3||Object.keys(intent).some(k=>!['modelNo','artPlanId','artIds'].includes(k)))throw new Error('Only model/plan/art intent selectors allowed; no proof/context/release fields');
 const selected={modelNo:model(intent.modelNo),artPlanId:positive(intent.artPlanId),artIds:list(intent.artIds).map(positive)};
 if(!selected.artIds.length||new Set(selected.artIds).size!==selected.artIds.length)throw new Error('Distinct nonempty art intent required');
 if(!view||view.modelNo!==selected.modelNo)throw new Error('View model mismatch');
 const observedPlanIds=[...new Set(list(observedListRows).filter(r=>r?.modelNo===selected.modelNo).map(r=>positive(r.artPlanId)))].sort((a,b)=>a-b);
 if(!observedPlanIds.includes(selected.artPlanId))throw new Error('Intent plan not in observed list');
 const artIds=[],groupIds=new Set();let defaultLikeGroupCount=0,groupInstanceCount=0;
 for(const art of list(view.artConfigs)){
  const artId=positive(art?.artId);if(artIds.includes(artId))throw new Error('Duplicate art view');artIds.push(artId);
  for(const group of list(art.componentGroups)){
   groupIds.add(positive(group?.componentGroupId));groupInstanceCount++;
   if(groupInstanceCount>10000)throw new Error('Excessive group view');
   if(group.order===null||group.order===undefined)defaultLikeGroupCount++;
   else if(!Number.isSafeInteger(group.order)||group.order<0)throw new Error('Unknown group instance order');
  }
 }
 if(selected.artIds.some(id=>!artIds.includes(id)))throw new Error('Intent art absent from observed view');
 const report={status:'BLOCKED_INCOMPLETE_SAVED_STATE',kind:'SOURCE_FIXTURE_INTENT_AUDIT_NOT_EXECUTABLE_PLAN',intent:selected,observedPlanIds,modelPlanAmbiguous:observedPlanIds.length>1?true:null,observedArtIds:artIds,observedGroupIds:[...groupIds].sort((a,b)=>a-b),groupInstanceCount,defaultLikeGroupCount,planListCoverage:'OBSERVED_ROWS_ONLY_NOT_COMPLETE',serverDeletionScope:'modelNo + artId; no artPlanId predicate',saveAndRelease:false,canBuildExecutablePayload:false,submissionImplemented:false,currentRowDiff:null,blockers:['GET_VIEW_OMITS_COMPLETE_RAW_SAVED_ROWS_AND_PUBLICATION_STATE','DEFAULT_GROUPS_ARE_NOT_PROOF_OF_SAVED_ROWS','PLAN_ID_SELECTOR_NOT_SUPPORTED_BY_GET_OR_SAVE_BODY','MODEL_ART_DELETE_CAN_TOUCH_ROWS_OUTSIDE_SELECTED_PLAN','GROUP_ARRAY_RENUMBERING_LOSES_ORIGINAL_INSTANCE_SCOPE','DRAFT_SAVE_REPLACES_PUBLISHED_ROWS_WITH_DRAFT_STATE','NO_VERSION_CAS_OR_VERIFIED_TRANSACTION','DEPLOYMENT_AUTHORIZATION_AND_RAW_ROW_PERMISSION_UNKNOWN'],requiredEvidence:['Authoritative raw saved rows for exact model+arts across all affected plans, including row IDs/publishStatus/rangeType/component sentinels/order and values','Complete model-plan association set and consistent unique target','Version/atomic delete-reinsert guarantees and explicit draft/publication impact policy','Deployment and effective action authorization; ordinary strings/booleans cannot certify these']};
 report.intentHash=createHash('sha256').update(JSON.stringify({intent:selected,observedPlanIds,artIds,groupIds:report.observedGroupIds,groupInstanceCount,defaultLikeGroupCount})).digest('hex');return report;
}

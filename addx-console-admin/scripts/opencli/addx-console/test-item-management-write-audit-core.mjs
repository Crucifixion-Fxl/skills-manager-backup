// Pure offline source-boundary audit, deliberately unregistered. No HTTP or credential access.
const actions=['template','open-detail','open-edit','save-draft','submit-review','batch-save-submit','publish-pr','cancel-release','delete-management'];
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canExecute:false,canBuildPayload:false,submitAllowed:false,authoritativeFullStateVerified:false};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
function refuse(){throw new Error('INVALID_OFFLINE_ACTION_EVIDENCE')}
export function assessTestItemManagementAction(input={mode:'dry-run',action:'template'},observation=null){
 if(!obj(input)||input.mode!=='dry-run'||Object.keys(input).some(k=>!['mode','action','modelNo'].includes(k))||!actions.includes(input.action))refuse();
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY',actions};
 if(typeof input.modelNo!=='string'||input.modelNo.length===0||input.modelNo.length>128||!obj(observation)||!['saved-list','unconfigured-candidates'].includes(observation.source)||!Array.isArray(observation.rows))refuse();
 const matches=observation.rows.filter(r=>r?.modelNo===input.modelNo);
 if(matches.length!==1)refuse();
 if(['open-detail','open-edit'].includes(input.action))return {...base,status:'BLOCKED_CONDITIONAL_INITIALIZATION',endpointClassification:'MIXED',observationCannotEliminateRace:true};
 if(input.action==='delete-management')return {...base,status:'UNSUPPORTED_NO_MANAGEMENT_DELETE_CONTRACT'};
 if(observation.source==='unconfigured-candidates')return {...base,status:'BLOCKED_UNCONFIGURED_TARGET_CREATE_SIDE_EFFECT',candidateIsSavedState:false};
 const row=matches[0];if(!Number.isSafeInteger(row.id)||row.id<=0||![0,1,3,4].includes(row.status)||![0,1,2,3,4,5].includes(row.modelType))refuse();
 if(['submit-review','batch-save-submit','publish-pr','cancel-release'].includes(input.action))return {...base,status:'BLOCKED_RELEASE_WORKFLOW_MUTATION',releaseCreatesOrChangesWorkflow:true,batchCanAutoCreatePR:input.action==='batch-save-submit',cancellationCanDeleteGitBranch:input.action==='cancel-release'};
 return {...base,status:'BLOCKED_INCOMPLETE_RAW_SAVED_STATE',savedRowObserved:true,observedStatus:row.status,saveResetsStatusTo:1,saveCanUpsert:true,nonemptySubmittedFactoriesReplaceMissingPairs:true,emptyFactoryListDoesNotClear:true,expectedVersionCASProvided:false};
}

// Pure offline intent/state audit. No SN values, payload, transport or CLI registration.
const base={evidenceScope:'OFFLINE_VERIFIED_ONLY',canBuildPayload:false,submitAllowed:false,authorityVerified:false,serverCASProvided:false};
const positive=x=>Number.isSafeInteger(x)&&x>0;
function refuse(){throw new Error('INVALID_OFFLINE_SN_INTENT_OR_STATE')}
export function snGenerationState(value){if(!Number.isInteger(value)||![0,1,2].includes(value))refuse();return ['GENERATING','COMPLETE','FAILED'][value]}
export function assessSnIntent(input={mode:'dry-run',action:'template'}){
 if(!input||typeof input!=='object'||Array.isArray(input)||input.mode!=='dry-run'||!['template','generate','single-status','batch-status','file-status','export'].includes(input.action))refuse();
 const allowed={template:[],generate:['quantity'],'single-status':[],'batch-status':['recordId','updateNum','updateType'],'file-status':[],export:[]};
 if(Object.keys(input).some(k=>!['mode','action',...allowed[input.action]].includes(k)))refuse();
 if(input.action==='template')return {...base,status:'TEMPLATE_ONLY'};
 if(input.action==='generate'){if(!positive(input.quantity)||input.quantity>10000)refuse();return {...base,status:'BLOCKED_NEW_IDENTIFIER_ALLOCATION_AUTHORITY',allocatesNewIdentifiers:true,quantityCapSource:'frontend-only; not backend enforcement',asyncAcceptedIsComplete:false,failureStateIsComplete:false,uniquenessRaceUnproven:true}}
 if(input.action==='batch-status'){if(!positive(input.recordId)||!positive(input.updateNum)||![0,1].includes(input.updateType))refuse();return {...base,status:'BLOCKED_UNORDERED_LIMIT_OLD_STATE_AND_SCOPE',previewCountDefinesTargetSet:false,canAffectRegisteredOrOccupied:true}}
 const statuses={'single-status':'BLOCKED_DIRECT_IDENTIFIER_SCOPE_AND_CAS','file-status':'BLOCKED_REGISTERED_OR_OCCUPIED_FILE_SCOPE',export:'BLOCKED_BULK_EXPORT_SCOPE'};
 return {...base,status:statuses[input.action],sourceBusinessWrite:input.action!=='export'};
}

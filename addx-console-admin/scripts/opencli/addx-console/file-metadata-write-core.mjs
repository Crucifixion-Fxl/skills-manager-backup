import {createHash} from 'node:crypto';
const text=v=>{if(typeof v!=='string'||!v.trim()||v.length>200)throw new Error('Invalid bounded nonblank identifier/name');return v;};
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
export async function runFileMetadataDryRun(action,args,deps){
 if((args.mode??'dry-run')!=='dry-run')throw new Error('Submission disabled; deployment and action permission unverified');
 if(!['delete-type','rename'].includes(action))throw new Error('Unknown action');
 const allowed=new Set(['mode','targetCode',...(action==='rename'?['fileId','fileName']:[])]);
 if(Object.keys(args).some(k=>!allowed.has(k)))throw new Error('Unknown fields or state/version/release controls forbidden');
 const code=text(args.targetCode);const fileId=action==='rename'?text(args.fileId):null;const name=action==='rename'?text(args.fileName):null;
 await deps.identity();
 const types=await deps.request('/filecenter/type/list','POST');
 if(!Array.isArray(types?.fileTypes)||types.fileTypes.length>10000)throw new Error('Complete type contract unknown');
 const selected=types.fileTypes.map(r=>({code:text(r.code),name:text(r.name)}));
 if(new Set(selected.map(r=>r.code)).size!==selected.length)throw new Error('Duplicate type target');
 const type=selected.find(r=>r.code===code);if(!type)throw new Error('Existing type target required');
 const result=await deps.request('/filecenter/file/list','POST',{fileTypeCode:code});
 if(!Array.isArray(result?.files)||result.files.length>10000)throw new Error('Complete file list contract unknown');
 const ids=result.files.map(r=>{if(r.fileTypeCode!==code)throw new Error('File scope mismatch');return text(r.fileId);});
 if(new Set(ids).size!==ids.length)throw new Error('Duplicate file target');
 let before,payload,sideEffect,endpoint;
 if(action==='delete-type'){
  if(ids.length)throw new Error('Type nonempty including unavailable/unpublished files; delete rejected');
  before=type;payload={code};endpoint='/filecenter/type/delete';sideEffect='DELETE_EXACT_TYPE_ROW; SERVER_CHECKS_ALL_FILES; CONCURRENT_INSERT_RACE_UNRESOLVED';
 }else{
  const r=result.files.find(r=>r.fileId===fileId);if(!r)throw new Error('Existing file target required');
  if(typeof r.available!=='boolean'||typeof r.lastPublishedVersion!=='string'||typeof r.readyToPublishVersion!=='string'||typeof r.updateTime!=='string')throw new Error('File status/version snapshot unknown');
  before={fileId,fileTypeCode:code,fileName:text(r.fileName),available:r.available,lastPublishedVersion:r.lastPublishedVersion,readyToPublishVersion:r.readyToPublishVersion,updateTime:r.updateTime};
  payload={fileId,fileName:name};endpoint='/filecenter/file/update';sideEffect='FILE_NAME_AND_UPDATE_TIME_ONLY; AVAILABLE OMITTED TO AVOID onIssuedFileChange';
 }
 const diff=action==='delete-type'?{removed:[before]}:{fileName:{from:before.fileName,to:name}};
 return [{status:'OFFLINE_PLAN_ONLY_NOT_LIVE_ACCEPTED',action,before,payload,diff,hash:hash({action,before,payload,selected,ids}),endpoint,sideEffect,versionCAS:'NOT_SUPPORTED',snapshotVersionCoverage:action==='rename'?'DISPLAY_VERSIONS_ONLY; UPDATE_TIME_MAY_USE_PUBLISH_OR_READY_TIMESTAMP':'TYPE_HAS_NO_VERSION',deploymentAndActionPermission:'UNKNOWN_NOT_VERIFIED',submissionImplemented:false,readSideEffect:'SELECTS_PLUS_GENERATE_PRESIGNED_GET_URL; URL_EXCLUDED_FROM_OUTPUT_AND_HASH'}];
}

import {createHash} from 'node:crypto';
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const id=v=>{if(!Number.isSafeInteger(v)||v<1||v>2147483647)throw new Error('Positive int32 ID required');return v;};
const text=(v,max=50)=>{if(typeof v!=='string'||!v.trim()||v!==v.trim()||v.length>max)throw new Error('Trimmed nonblank text exceeds allowed UI bound');return v;};
function keys(value,allowed){if(!value||typeof value!=='object'||Object.keys(value).some(k=>!allowed.includes(k)))throw new Error('Unknown fields, associations or release flag forbidden');}
function finish(plan){return {...plan,status:'DRY_RUN_NOT_SUBMITTED',deploymentAndActionPermission:'UNKNOWN_NOT_VERIFIED',submissionImplemented:false,hash:hash(plan)};}
export function planEmptyGroup(action,changes,catalog,info){
 keys(changes,action==='create'?['componentGroupCode','componentGroupName','remark','iconId']:['id','componentGroupName','remark','iconId']);
 if(!Array.isArray(catalog)||!info||!Array.isArray(info.iconManageDOList))throw new Error('Complete group catalog and icon options required');
 if(catalog.some(r=>typeof r.componentGroupCode!=='string'||!r.componentGroupCode||typeof r.componentGroupName!=='string'||!r.componentGroupName))throw new Error('Group code/name inventory shape unknown');
 const ids=catalog.map(r=>id(r.id));if(new Set(ids).size!==ids.length)throw new Error('Ambiguous group catalog');
 let before=null,payload;
 if(action==='create'){
  const code=text(changes.componentGroupCode);if(!/^\w+$/.test(code))throw new Error('Group code must match UI ASCII letters/digits/underscore');
  payload={componentGroupCode:code,componentGroupName:text(changes.componentGroupName),remark:changes.remark??'',iconId:id(changes.iconId),businessType:0,relatedBusiness:0,relatedWorkstation:[],paramDOList:[],saveAndRelease:false};
 }else if(action==='edit'){
  const target=id(changes.id),matches=catalog.filter(r=>r.id===target);if(matches.length!==1||info.id!==target)throw new Error('Existing group target not verified');const row=matches[0];
  if(row.paramNum!==0||info.paramDOList!=null&&(!Array.isArray(info.paramDOList)||info.paramDOList.length))throw new Error('Complex parameter groups unsupported; refusing to clear/upsert parameters');
  if(info.componentGroupCode!==row.componentGroupCode||info.componentGroupName!==row.componentGroupName)throw new Error('Group metadata changed between list and detail');
  if(![0,1].includes(info.businessType)||!Number.isSafeInteger(info.relatedBusiness)||info.relatedBusiness<0||!Array.isArray(info.relatedWorkstation))throw new Error('Existing business/workstation associations unknown');
  if(typeof info.remark!=='string')throw new Error('Existing remark shape unknown; no null-to-empty coercion');
  const workstations=info.relatedWorkstation.map(id).sort((a,b)=>a-b);if(new Set(workstations).size!==workstations.length)throw new Error('Ambiguous existing workstation set');
  if(row.versionTime!=null&&(!Number.isSafeInteger(row.versionTime)||row.versionTime<0)||!Number.isInteger(row.releaseStatus))throw new Error('Version/status shape unknown');
  before={id:target,componentGroupCode:text(info.componentGroupCode),componentGroupName:text(info.componentGroupName),remark:info.remark??'',iconId:id(info.iconId),businessType:info.businessType,relatedBusiness:info.relatedBusiness,relatedWorkstation:workstations,paramNum:0,versionTime:row.versionTime??null,releaseStatus:row.releaseStatus,lastModifyTime:row.lastModifyTime??null};
  payload={id:target,componentGroupCode:before.componentGroupCode,componentGroupName:text(changes.componentGroupName),remark:changes.remark??before.remark,iconId:changes.iconId===undefined?before.iconId:id(changes.iconId),businessType:before.businessType,relatedBusiness:before.relatedBusiness,relatedWorkstation:workstations,paramDOList:[],saveAndRelease:false};
 }else throw new Error('Action must be create or edit');
 if(typeof payload.remark!=='string'||payload.remark.length>2000)throw new Error('Remark must be a bounded string');
 if(!info.iconManageDOList.some(r=>r.id===payload.iconId))throw new Error('Icon ID not found in current resource options');
 if(catalog.some(r=>r.id!==payload.id&&(r.componentGroupCode===payload.componentGroupCode||r.componentGroupName===payload.componentGroupName)))throw new Error('Group code/name already exists');
 const diff=before?Object.fromEntries(['componentGroupName','remark','iconId'].filter(k=>before[k]!==payload[k]).map(k=>[k,{from:before[k],to:payload[k]}])):{create:payload};
 return finish({resource:'component-group',action,path:'/model/component/group/save',scope:'OBSERVED_EMPTY_ACTIVE_PARAMETER_GROUP_METADATA_ONLY',before,payload,diff,catalogHash:hash(catalog.map(r=>({id:r.id,code:r.componentGroupCode,name:r.componentGroupName,paramNum:r.paramNum})).sort((a,b)=>a.id-b.id)),parameterMappingProof:action==='edit'?'ACTIVE_DETAIL_AND_COUNT_ONLY; HIDDEN_CURRENT_VERSION_MAPPING_UNVERIFIED':'NEW_RESOURCE_NO_EXISTING_MAPPING',sideEffect:'GROUP_ROW_SAVE_ALWAYS_UPDATES_OPERATOR_TIME; HIDDEN_PARAM_MAPPING_MAY_CHANGE_VERSION_AND_WORKSTATION_VISIBILITY; RELEASE_FORBIDDEN'});
}
export function planFileType(action,changes,types,targetCode){
 keys(changes,action==='create'?['code','name']:['name']);if(!Array.isArray(types)||types.length>10000)throw new Error('Complete file type list required');
 if(types.some(r=>typeof r.code!=='string'||typeof r.name!=='string')||new Set(types.map(r=>r.code)).size!==types.length)throw new Error('Ambiguous file type catalog');
 let before=null,code;
 if(action==='create'){
  if(targetCode!==undefined)throw new Error('Create forbids target-code');code=text(changes.code);if(!/^[A-Za-z0-9_-]+$/.test(code))throw new Error('File type code must match UI letters/digits/underscore/hyphen');if(types.some(r=>r.code===code))throw new Error('File type code exists; create would replace existing row');
 }else if(action==='edit'){
  code=text(targetCode);const rows=types.filter(r=>r.code===code);if(rows.length!==1)throw new Error('Existing file type target not found');before={code:rows[0].code,name:rows[0].name};
 }else throw new Error('Action must be create or edit');
 const payload={code,name:text(changes.name)};
 return finish({resource:'file-type',action,path:'/filecenter/type/save',scope:'EXACT_CODE_TARGET_UI_CODE_IMMUTABLE_ON_EDIT',before,payload,diff:before?{name:{from:before.name,to:payload.name}}:{create:payload},catalogHash:hash(types.map(r=>({code:r.code,name:r.name})).sort((a,b)=>a.code.localeCompare(b.code))),sideEffect:action==='edit'?'SQL_REPLACE_EXISTING_ROW_NOT_UPDATE':'SQL_REPLACE_INSERT_WHEN_CODE_ABSENT'});
}
export async function runProductDryRun(resource,args,deps){
 if(args.mode!=='dry-run')throw new Error('submit is disabled: actual deployment, action authorization and readback remain unverified');
 if(!['create','edit'].includes(args.action))throw new Error('Action must be create or edit');
 const changes=await deps.readDocument(args['changes-file']);await deps.identity();
 if(resource==='file-type'){
  const result=await deps.request('/filecenter/type/list','POST');if(!result||!Array.isArray(result.fileTypes))throw new Error('File type list contract changed');return [planFileType(args.action,changes,result.fileTypes,args['target-code'])];
 }
 if(resource!=='component-group')throw new Error('Unknown resource');
 const catalog=[];let total;
 for(let pageIndex=1;pageIndex<=100;pageIndex++){
  const r=await deps.request('/model/component/group/list','POST',{pageIndex,pageSize:20});if(!r||!Array.isArray(r.list)||!Number.isSafeInteger(r.total)||r.total<0||r.list.length>20)throw new Error('Group pagination shape changed');
  if(total!==undefined&&r.total!==total)throw new Error('Group inventory changed while reading');total=r.total;catalog.push(...r.list);
  if(catalog.length===total)break;if(!r.list.length||catalog.length>total||pageIndex===100)throw new Error('Complete bounded group catalog unavailable');
 }
 const target=args.action==='edit'?id(changes.id):0;
 const info=await deps.request('/model/component/group/info','POST',{id:target});
 return [planEmptyGroup(args.action,changes,catalog,info)];
}

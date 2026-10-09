import {createHash} from 'node:crypto';
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const id=v=>{if(!Number.isSafeInteger(v)||v<1||v>2147483647)throw new Error('Positive int32 resource ID required');return v;};
const name=v=>{if(typeof v!=='string'||!v.trim()||v!==v.trim()||v.length>50)throw new Error('Name must be trimmed, nonblank, at most UI limit 50');return v;};
const code=v=>{if(typeof v!=='string'||!/^\w{1,50}$/.test(v))throw new Error('Category code must match UI ASCII letters/digits/underscore limit 50');return v;};
function associations(rows){
 if(!Array.isArray(rows)||rows.length>1000)throw new Error('Full requiredList association array required');const seen=new Set();
 return rows.map(row=>{if(!row||Object.keys(row).some(k=>!['componentGroupId','required'].includes(k)))throw new Error('Unexpected association fields');const key=id(row.componentGroupId);if(seen.has(key)||![0,1].includes(row.required))throw new Error('Duplicate group or unknown required flag');seen.add(key);return {componentGroupId:key,required:row.required};}).sort((a,b)=>a.componentGroupId-b.componentGroupId);
}
function delta(before,after){const old=new Map(before.map(r=>[r.componentGroupId,r])),next=new Map(after.map(r=>[r.componentGroupId,r]));return {added:after.filter(r=>!old.has(r.componentGroupId)),removed:before.filter(r=>!next.has(r.componentGroupId)),changed:after.filter(r=>old.has(r.componentGroupId)&&old.get(r.componentGroupId).required!==r.required).map(r=>({componentGroupId:r.componentGroupId,from:old.get(r.componentGroupId).required,to:r.required}))};}
export function planCategory(action,changes,catalog,groups,current){
 if(!changes||typeof changes!=='object'||Object.keys(changes).some(k=>!(action==='create'?['categoryCode','categoryName','requiredList']:['id','categoryName','requiredList']).includes(k)))throw new Error('Unknown fields or publication flag forbidden');
 if(!Array.isArray(catalog)||!Array.isArray(groups))throw new Error('Complete resource catalogs required');
 const available=new Set(groups.map(r=>id(r.id)));if(available.size!==groups.length)throw new Error('Duplicate available groups');
 const catalogIds=new Set(catalog.map(r=>id(r.id)));if(catalogIds.size!==catalog.length)throw new Error('Duplicate category resources');
 const proposed=associations(changes.requiredList);if(proposed.some(r=>!available.has(r.componentGroupId)))throw new Error('Proposed group is absent from current published resource options');
 let before=null,payload;
 if(action==='create')payload={categoryCode:code(changes.categoryCode),categoryName:name(changes.categoryName),requiredList:proposed,saveAndRelease:false};
 else if(action==='edit'){
  const target=id(changes.id);if(!current||current.id!==target||!catalogIds.has(target))throw new Error('Existing category target ID not verified');
  const old=associations(current.modelCategoryComponentDOList?.map(r=>({componentGroupId:r.componentGroupId,required:r.required})));
  if(!Number.isSafeInteger(current.versionTime)||current.versionTime<1||!Number.isInteger(current.releaseStatus))throw new Error('Current version/status shape unknown');
  before={id:target,categoryCode:code(current.categoryCode),categoryName:name(current.categoryName),requiredList:old,versionTime:current.versionTime,releaseStatus:current.releaseStatus,lastModifyTime:current.lastModifyTime??null};
  payload={id:target,categoryCode:before.categoryCode,categoryName:name(changes.categoryName),requiredList:proposed,saveAndRelease:false};
 }else throw new Error('Action must be create or edit');
 if(catalog.some(r=>r.id!==payload.id&&(r.categoryCode===payload.categoryCode||r.categoryName===payload.categoryName)))throw new Error('Another category already has proposed code or name');
 const projection=catalog.map(r=>({id:r.id,categoryCode:r.categoryCode,categoryName:r.categoryName})).sort((a,b)=>a.id-b.id);
 const plan={status:'DRY_RUN_NOT_SUBMITTED',resource:'device-category',action,path:'/device/category/save',scope:'PLATFORM_GLOBAL_RESOURCE_NO_OWNER_FIELD',before,payload,groupDiff:delta(before?.requiredList??[],proposed),catalogHash:digest(projection),availableGroupIds:[...available].sort((a,b)=>a-b),sideEffects:['Changed category creates new epoch-second version and versioned association rows','Changed existing category becomes unpublished','Publication is forbidden: saveAndRelease fixed false'],deploymentAndActionPermission:'UNKNOWN_NOT_VERIFIED',submissionImplemented:false};
 return {...plan,hash:digest(plan)};
}
export async function runCategoryDryRun(action,args,deps){
 if(args.mode!=='dry-run')throw new Error('submit is disabled until live deployment/action authorization and verified readback are implemented');
 const changes=await deps.readDocument(args['changes-file']);
 // Input has no credential/context fields. Identity comes from the actual native consumer.
 await deps.identity();
 const catalog=[];let total;
 for(let pageIndex=1;pageIndex<=100;pageIndex++){
  const page=await deps.request('/device/category/list','POST',{pageIndex,pageSize:20});
  if(!page||!Array.isArray(page.list)||!Number.isSafeInteger(page.total)||page.total<0||page.list.length>20)throw new Error('Category pagination shape changed');
  if(total!==undefined&&page.total!==total)throw new Error('Category inventory changed while scanning');total=page.total;catalog.push(...page.list);
  if(catalog.length===total)break;
  if(!page.list.length||catalog.length>total||pageIndex===100)throw new Error('Complete bounded category inventory unavailable');
 }
 const params=await deps.request('/device/category/param/list','POST');
 if(!params||!Array.isArray(params.modelComponentGroupDOList))throw new Error('Published group resource options unavailable');
 let current;
 if(action==='edit')current=await deps.request('/device/category/info','POST',{id:id(changes.id)});
 return [planCategory(action,changes,catalog,params.modelComponentGroupDOList,current)];
}

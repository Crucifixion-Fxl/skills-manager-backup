// Source contracts: GitLab project 664, ref 67ff74a8feab308a7a60b67275094ba1e7cd3180.
// Pure planning and injected execution; no credentials or network dependency.
import {createHash} from 'node:crypto';
const positive=(v,name)=>{if(typeof v!=='string'&&typeof v!=='number')throw new Error(`${name} must be numeric`);const n=Number(v);if(!Number.isSafeInteger(n)||n<1)throw new Error(`${name} must be a positive safe integer`);return n;};
const text=(v,name)=>{if(typeof v!=='string'||!v.trim()||v!==v.trim()||v.length>200)throw new Error(`${name} must be nonblank, trimmed, at most 200 characters`);return v;};
function seal(plan){return {...plan,hash:createHash('sha256').update(JSON.stringify(plan)).digest('hex')};}
export function planCell(action,args,records){
 if(!Array.isArray(records))throw new Error('Complete cell records required');
 const factory=positive(args.factory,'factory');let payload,before;
 if(action==='create'){
  if(args.id!==undefined)throw new Error('Create forbids id');const model=text(args.model,'model');
  if(records.some(r=>r.batteryCellModel===model))throw new Error('Cell model already exists');
  payload={batteryCellModel:model,batteryCellFactoryId:factory};before=null;
 }else if(action==='edit'){
  if(args.model!==undefined)throw new Error('Edit preserves readonly model; model option forbidden');
  const id=positive(args.id,'id'),matches=records.filter(r=>r.id===id);if(matches.length!==1)throw new Error('Existing cell ID not uniquely found');
  const r=matches[0];payload={id,batteryCellModel:text(r.batteryCellModel,'current model'),batteryCellFactoryId:factory};
  before={id,batteryCellModel:r.batteryCellModel,batteryCellFactoryId:r.batteryCellFactoryId,updateTime:r.updateTime??null};
 }else throw new Error('Cell action must be create or edit');
 return seal({resource:'battery-cell',action,path:'/battery_cell/manage/saveOrUpdate',payload,before});
}
export function planPack(action,args,records){
 const model=text(args.model,'model'),cell=text(args.cell,'cell'),count=positive(args.count,'count');
 if(!Array.isArray(records))throw new Error('Complete exact model result required');
 const matches=records.filter(r=>r.batteryPackModel===model);if(matches.length!==1)throw new Error('Model must exist exactly once in component-backed list');
 const r=matches[0];if(r.updateTimeUTCSecond!=null&&(!Number.isSafeInteger(r.updateTimeUTCSecond)||r.updateTimeUTCSecond<1))throw new Error('Pack timestamp shape changed; refuse upsert');const configured=Number.isSafeInteger(r.updateTimeUTCSecond)&&r.updateTimeUTCSecond>0;
 if(action==='edit'&&!configured)throw new Error('Unconfigured pack would be created; use initialize');
 if(action==='initialize'&&configured)throw new Error('Pack is already configured; use edit');
 if(!['edit','initialize'].includes(action))throw new Error('Pack action must be edit or initialize');
 return seal({resource:'battery-pack',action,path:'/battery_package/manage/edit',payload:{batteryPackModel:model,batteryCellModel:cell,batteryCellNumber:count},before:{batteryPackModel:model,batteryCellModel:r.batteryCellModel??null,batteryCellNumber:r.batteryCellNumber??null,updateTimeUTCSecond:r.updateTimeUTCSecond??null}});
}
export async function executePlan(plan,options,{write,replan}){
 if(!options.commit)return {status:'DRY_RUN',...plan};
 if(options.allowWrite!=='1'||!options.expectedEmail||!options.deploymentProof||options.approvedPlan!==plan.hash)throw new Error('Write requires explicit commit, exact approved plan hash, identity, deployment proof, and private write enablement');
 if(typeof replan!=='function')throw new Error('Fresh state recheck required');
 const fresh=await replan();if(fresh.hash!==plan.hash)throw new Error('Target changed; review and approve a fresh plan');
 await write(plan.path,plan.payload);
 // Backend has no atomic conditional write. Recheck narrows but does not remove race window.
 return {status:'SUBMITTED_NOT_READBACK_VERIFIED',resource:plan.resource,action:plan.action,hash:plan.hash};
}

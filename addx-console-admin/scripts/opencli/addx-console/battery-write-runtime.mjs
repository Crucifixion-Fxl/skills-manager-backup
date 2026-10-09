import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {planCell,planPack,executePlan} from './battery-write-core.mjs';
export const gateArgs=[{valueRequired:true,name:'mode',choices:['dry-run','submit'],default:'dry-run',help:'Submit only after explicit user authorization of the displayed plan'},{valueRequired:true,name:'approved-plan',help:'Exact SHA256 from reviewed dry-run'},{valueRequired:true,name:'deployment-proof',help:'Operator verified current deployment/action permission evidence identifier'}];
async function allCells(){
 let rows=[],total;
 for(let pageIndex=1;pageIndex<=100;pageIndex++){
  const r=await request('/battery_cell/manage/list','POST',{pageIndex,pageSize:20});
  if(!r||!Array.isArray(r.list)||!Number.isSafeInteger(r.total)||r.total<0||r.list.length>20)throw new CommandExecutionError('Cell list contract changed');
  if(total!==undefined&&r.total!==total)throw new CommandExecutionError('Cell inventory changed while reading');total=r.total;rows.push(...r.list);
  if(rows.length===total){if(new Set(rows.map(x=>x.id)).size!==rows.length)throw new CommandExecutionError('Duplicate cell IDs');return rows;}
  if(rows.length>total||r.list.length===0)throw new CommandExecutionError('Incomplete cell inventory');
 }
 throw new CommandExecutionError('Cell inventory exceeds safe bounded scan; no mutation');
}
async function packRows(model){
 const r=await request('/battery_package/manage/list','POST',{batteryPackModel:model,pageIndex:1,pageSize:20});
 if(!r||!Array.isArray(r.list)||r.list.length!==r.total||r.total>20)throw new CommandExecutionError('Exact pack inventory incomplete');return r.list;
}
export async function runBatteryWrite(resource,args){
 try{
  if(!['dry-run','submit'].includes(args.mode))throw new ArgumentError('mode must be dry-run or submit');
  await identity();
  const make=async()=>{
   const cells=await allCells();
   if(resource==='cell'){
    if(!cells.some(x=>x.batteryCellFactoryId===Number(args.factory)))throw new ArgumentError('Factory must be an existing verified cell manufacturer');
    return planCell(args.action,args,cells);
   }
   if(!cells.some(x=>x.batteryCellModel===args.cell))throw new ArgumentError('Cell model must exist');
   return planPack(args.action,args,await packRows(args.model));
  };
  const plan=await make();
  const result=await executePlan(plan,{commit:args.mode==='submit',approvedPlan:args['approved-plan'],deploymentProof:args['deployment-proof'],allowWrite:process.env.CONSOLE_ALLOW_WRITE,expectedEmail:process.env.CONSOLE_EXPECTED_EMAIL},{replan:make,write:(path,payload)=>request(path,'POST',payload)});
  return [{status:result.status,resource:plan.resource,action:plan.action,planHash:plan.hash,current:JSON.stringify(plan.before),proposed:JSON.stringify(plan.payload),limitation:'No atomic server-side compare-and-swap; successful submission requires separate readback'}];
 }catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||e.name==='AuthRequiredError'||e.name==='TimeoutError')throw e;throw new ArgumentError(e.message);}
}

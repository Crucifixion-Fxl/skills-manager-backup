// Source-contract simulation only. No native credential or HTTP dependency.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
const positive=(v)=>{if(!Number.isSafeInteger(v)||v<1)throw new Error('Positive integer identifier required');return v;};
const label=v=>{if(typeof v!=='string'||!v.trim()||v!==v.trim()||v.length>200)throw new Error('Nonblank trimmed model/batch required');return v;};
function sequence(start,end){
 if(typeof start!=='string'||typeof end!=='string'||!/^\d{1,10}$/.test(start)||!/^\d{1,10}$/.test(end)||start.length!==end.length)throw new Error('Equal-width decimal sequence strings required');
 const a=Number(start),b=Number(end);if(a>b||b>2147483647)throw new Error('Sequence interval outside Java integer32 bounds');return {startSeq:start,endSeq:end,startNumber:a,endNumber:b,seqLength:start.length};
}
const fields=['batteryPackModel','batteryCellModel','batteryCellBatchCode','batteryPackBatchCode'];
export function planProduction(action,proposed,context){
 if(!context||context.simulation!==true)throw new Error('Only explicitly simulated context is supported; live verification is not implemented');
 if(![0,1].includes(context.customerType))throw new Error('LDAP/server or unknown users cannot create/edit production plans');
 const factory=positive(context.manufacturerId);
 if(!Array.isArray(context.permissions)||!context.permissions.includes('BatteryProductionPlanningManagement'))throw new Error('Simulated management permission missing');
 if(!proposed||typeof proposed!=='object')throw new Error('Proposed action required');
 let payload,before=null;
 if(action==='create'){
  if(Object.keys(proposed).some(k=>![...fields,'startSeq','endSeq'].includes(k)))throw new Error('Create forbids id/unknown fields');
  payload=Object.fromEntries(fields.map(f=>[f,label(proposed[f])]));
 }else if(action==='edit'){
  if(Object.keys(proposed).some(k=>!['id','startSeq','endSeq'].includes(k)))throw new Error('Edit preserves model/batch fields; only interval edits supported');
  const id=positive(proposed.id),current=context.current;
  if(!current||current.id!==id||!Array.isArray(context.scopedPlanIds)||!context.scopedPlanIds.includes(id))throw new Error('Existing ID must be in simulated current client-scoped list');
  if(context.editable!==true)throw new Error('Plan has completed test records or editability is unknown');
  if(!Number.isInteger(current.seqLength)||current.seqLength<1||current.seqLength>10||!Number.isInteger(current.startNumber)||!Number.isInteger(current.endNumber))throw new Error('Current sequence contract mismatch');
  const old=sequence(String(current.startNumber).padStart(current.seqLength,'0'),String(current.endNumber).padStart(current.seqLength,'0'));
  if(old.seqLength!==current.seqLength)throw new Error('Current numeric interval exceeds saved sequence width');
  payload={id,...Object.fromEntries(fields.map(f=>[f,label(current[f])]))};before={...payload,startSeq:old.startSeq,endSeq:old.endSeq};
 }else throw new Error('Action must be create or edit');
 const seq=sequence(proposed.startSeq,proposed.endSeq);
 if(action==='edit'&&seq.seqLength!==context.current.seqLength)throw new Error('Edit preserves existing sequence width');
 payload={...payload,startSeq:seq.startSeq,endSeq:seq.endSeq};
 const count=seq.endNumber-seq.startNumber+1;
 if(count>2147483647)throw new Error('Plan quantity overflows Java integer32');
 if(!Number.isSafeInteger(context.maxProductNumber)||context.maxProductNumber<0||count>context.maxProductNumber)throw new Error('Simulated unallocated production capacity insufficient or unknown');
 if(context.batchPlansComplete!==true||!Array.isArray(context.batchPlans))throw new Error('Complete global batch intervals required in simulation; scoped list is insufficient');
 const intervals=context.batchPlans.map(p=>{positive(p.id);label(p.batteryPackBatchCode);if(!Number.isInteger(p.startNumber)||!Number.isInteger(p.endNumber)||p.startNumber<0||p.startNumber>p.endNumber||p.endNumber>2147483647)throw new Error('Existing global interval shape invalid');return {id:p.id,batteryPackBatchCode:p.batteryPackBatchCode,startNumber:p.startNumber,endNumber:p.endNumber};}).sort((a,b)=>a.id-b.id);
 if(intervals.some(p=>p.id!==payload.id&&p.batteryPackBatchCode===payload.batteryPackBatchCode&&p.endNumber>=seq.startNumber&&p.startNumber<=seq.endNumber))throw new Error('Production batch interval overlaps another plan');
 const plan={status:'OFFLINE_PLAN_ONLY',resource:'battery-production-plan',action,path:'/battery_product_plan/saveOrEdit',scope:{simulatedManufacturerId:factory,customerType:context.customerType},before,payload,diff:before?{startSeq:{from:before.startSeq,to:payload.startSeq},endSeq:{from:before.endSeq,to:payload.endSeq}}:{create:payload},planProductNumber:count,startSn:payload.batteryPackBatchCode+seq.startSeq,endSn:payload.batteryPackBatchCode+seq.endSeq,sideEffect:action==='create'?'CREATE_PRODUCTION_PLAN_AND_RESERVE_SN_INTERVAL':'EDIT_PLAN_AND_CHANGE_RESERVED_SN_INTERVAL',assumptions:{simulatedOnly:true,maxProductNumber:context.maxProductNumber,managementPermission:true,editable:action==='edit'?true:null,globalBatchIntervals:intervals},submissionImplemented:false};
 return {...plan,hash:createHash('sha256').update(JSON.stringify(plan)).digest('hex')};
}
export async function runProductionOffline(action,args,reader=readFile){
 if(args.mode!=='dry-run')throw new Error('Production submission is not implemented: deployment, action permission and current scope need verified live adapters');
 let document;try{const raw=await reader(args['simulation-file'],'utf8');if(Buffer.byteLength(raw)>1048576)throw new Error();document=JSON.parse(raw);}catch{throw new Error('Simulation file must contain bounded JSON; contents are not echoed');}
 return [planProduction(action,document.proposed,document.context)];
}

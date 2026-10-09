import {readFile} from 'node:fs/promises';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {planFactory,executeFactoryPlan} from './pack-factory-write-core.mjs';
export async function runFactoryWrite(args,deps={readFile,identity,request}){
 try{
  if(!['dry-run','submit'].includes(args.mode))throw new ArgumentError('mode must be dry-run or submit');
  const factory=Number(args['factory-id']);if(typeof args['factory-id']==='boolean'||!Number.isSafeInteger(factory)||factory<1)throw new ArgumentError('factory-id must be positive');
  let raw,desired;try{raw=await deps.readFile(args['replacement-file'],'utf8');if(Buffer.byteLength(raw)>1048576)throw new Error();desired=JSON.parse(raw);}catch{throw new ArgumentError('Replacement file must be valid bounded JSON; contents are not echoed');}
  if(!desired||desired.complete!==true||desired.packFactoryId!==factory||Object.keys(desired).some(k=>!['complete','packFactoryId','batteryCellFactoryList'].includes(k)))throw new ArgumentError('Explicit complete single-factory replacement required');
  if(!desired||!Array.isArray(desired.batteryCellFactoryList)||desired.batteryCellFactoryList.length>1000)throw new ArgumentError('Complete association list required');
  if(desired.batteryCellFactoryList.some(r=>!r||Object.keys(r).some(k=>!['batteryCellModelFactoryId','batteryCellModels'].includes(k))))throw new ArgumentError('Unexpected association fields');
  if(typeof args['clear-factory']==='boolean')throw new ArgumentError('clear-factory requires exact numeric factory ID');
  const clear=args['clear-factory']===undefined?undefined:Number(args['clear-factory']);
  await deps.identity();
  const make=async()=>{
   const factories=await deps.request('/pack_factory/manage/getPackFactories');
   if(!Array.isArray(factories)||factories.filter(r=>r.key===factory).length!==1)throw new CommandExecutionError('Existing Pack factory not uniquely verified');
   const current=await deps.request('/pack_factory/manage/list','POST',{packFactoryId:factory});
   const available=[];const ids=[...new Set(desired.batteryCellFactoryList.map(r=>r.batteryCellModelFactoryId))];
   for(const id of ids){
    if(!Number.isSafeInteger(id)||id<1)throw new ArgumentError('Positive integer cell factory required');
    const cells=await deps.request('/battery_cell/manage/getBatteryCellModels?batteryCellFactoryId='+id);
    if(!Array.isArray(cells)||cells.length>10000||cells.some(r=>r.batteryCellFactoryId!==id||typeof r.batteryCellModel!=='string'))throw new CommandExecutionError('Cell availability shape changed');available.push(...cells);
   }
   return planFactory(factory,current,desired,available,clear);
  };
  const plan=await make();
  const result=await executeFactoryPlan(plan,{commit:args.mode==='submit',approvedPlan:args['approved-plan'],approvedDeletions:args['approved-deletions'],deploymentProof:args['deployment-proof'],allowWrite:process.env.CONSOLE_ALLOW_WRITE,expectedEmail:process.env.CONSOLE_EXPECTED_EMAIL},{replan:make,write:(path,payload)=>deps.request(path,'POST',payload)});
  return [{status:result.status,factoryId:factory,planHash:plan.hash,deletionHash:plan.deletionHash,additions:JSON.stringify(plan.additions),deletions:JSON.stringify(plan.deletions),current:JSON.stringify(plan.before),replacement:JSON.stringify(plan.payload.packFactoryList[0].batteryCellFactoryList),limitation:'Full replacement; no atomic conflict guard; separate readback required after authorized submit'}];
 }catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||['AuthRequiredError','TimeoutError'].includes(e.name))throw e;throw new ArgumentError(e.message);}
}

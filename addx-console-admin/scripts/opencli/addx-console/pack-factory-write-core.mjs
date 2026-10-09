import {createHash} from 'node:crypto';
import {executePlan} from './battery-write-core.mjs';
const hash=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
const id=x=>{if(typeof x!=='number'||!Number.isSafeInteger(x)||x<1)throw new Error('Positive integer factory ID required');return x;};
function models(v){if(!Array.isArray(v)||v.length>10000||v.some(x=>typeof x!=='string'||!x.trim()||x!==x.trim()||x.length>200)||new Set(v).size!==v.length)throw new Error('Unique nonblank model array required');return [...v].sort();}
function normalize(rows,key){
 if(!Array.isArray(rows)||rows.length>1000)throw new Error('Complete bounded association array required');const seen=new Set();
 return rows.map(r=>{const f=id(r.batteryCellModelFactoryId);if(seen.has(f))throw new Error('Duplicate cell factory');seen.add(f);return {batteryCellModelFactoryId:f,batteryCellModels:models(r[key])};}).sort((a,b)=>a.batteryCellModelFactoryId-b.batteryCellModelFactoryId);
}
const pairs=rows=>rows.flatMap(r=>r.batteryCellModels.map(model=>({factory:r.batteryCellModelFactoryId,model})));
const pairKey=p=>JSON.stringify([p.factory,p.model]);
export function planFactory(factory,current,desired,available,clearFactory){
 id(factory);
 if(!desired||desired.complete!==true||desired.packFactoryId!==factory||Object.keys(desired).some(k=>!['complete','packFactoryId','batteryCellFactoryList'].includes(k)))throw new Error('Explicit complete single-factory replacement required; append/partial payload forbidden');
 const before=normalize(current,'selectedBatteryCellModels'),after=normalize(desired.batteryCellFactoryList,'batteryCellModels');
 if(after.some(r=>!r.batteryCellModels.length))throw new Error('Empty child models are skipped by server; omit factory from complete replacement to delete');
 if(!after.length&&clearFactory!==factory)throw new Error('Clearing all requires exact factory acknowledgement');
 if(!Array.isArray(available))throw new Error('Complete cell availability required');
 const oldPairs=pairs(before),newPairs=pairs(after),oldSet=new Set(oldPairs.map(pairKey)),newSet=new Set(newPairs.map(pairKey));
 const additions=newPairs.filter(p=>!oldSet.has(pairKey(p))),deletions=oldPairs.filter(p=>!newSet.has(pairKey(p)));
 const allowed=new Set(available.map(r=>pairKey({factory:id(r.batteryCellFactoryId),model:r.batteryCellModel})));
 if(additions.some(p=>!allowed.has(pairKey(p))))throw new Error('New factory/model association not in verified cell inventory');
 const plan={resource:'pack-factory',action:'replace-complete',path:'/pack_factory/manage/save',payload:{packFactoryList:[{packFactoryId:factory,batteryCellFactoryList:after}]},before,additions,deletions,deletionHash:hash({factory,deletions}),clearAcknowledged:!after.length};
 return {...plan,hash:hash(plan)};
}
export async function executeFactoryPlan(plan,options,deps){
 if(options.commit&&plan.deletions.length&&options.approvedDeletions!==plan.deletionHash)throw new Error('Explicit approval of exact deletion scope required');
 return executePlan(plan,options,deps);
}

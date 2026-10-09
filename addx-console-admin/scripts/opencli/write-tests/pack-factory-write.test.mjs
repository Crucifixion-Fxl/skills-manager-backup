import test from 'node:test';import assert from 'node:assert/strict';
import {planFactory,executeFactoryPlan} from '../addx-console/pack-factory-write-core.mjs';
const current=[{batteryCellModelFactoryId:10,selectedBatteryCellModels:['B','A']}];
const available=[{batteryCellFactoryId:10,batteryCellModel:'A'},{batteryCellFactoryId:10,batteryCellModel:'B'},{batteryCellFactoryId:11,batteryCellModel:'C'}];
const desired={complete:true,packFactoryId:1,batteryCellFactoryList:[{batteryCellModelFactoryId:10,batteryCellModels:['A']},{batteryCellModelFactoryId:11,batteryCellModels:['C']}]};
test('replacement yields exact addition/removal diff',()=>{const p=planFactory(1,current,desired,available);assert.deepEqual(p.additions,[{factory:11,model:'C'}]);assert.deepEqual(p.deletions,[{factory:10,model:'B'}]);assert.equal(p.payload.packFactoryList[0].packFactoryId,1);});
test('partial append, mismatch, duplicates and empty sublists reject',()=>{
 for(const d of [{...desired,complete:false},{...desired,append:true},{...desired,packFactoryId:2},{...desired,batteryCellFactoryList:[desired.batteryCellFactoryList[0],desired.batteryCellFactoryList[0]]},{...desired,batteryCellFactoryList:[{batteryCellModelFactoryId:10,batteryCellModels:[]}]}])assert.throws(()=>planFactory(1,current,d,available));
});
test('clear requires exact explicit factory acknowledgement',()=>{
 const d={...desired,batteryCellFactoryList:[]};assert.throws(()=>planFactory(1,current,d,available));assert.throws(()=>planFactory(1,current,d,available,2));assert.equal(planFactory(1,current,d,available,1).deletions.length,2);
});
test('canonical order, stale target, invalid new model',()=>{
 const p=planFactory(1,current,desired,available);assert.equal(p.hash,planFactory(1,[{...current[0],selectedBatteryCellModels:['A','B']}],desired,available).hash);
 assert.notEqual(p.hash,planFactory(1,[{...current[0],selectedBatteryCellModels:['A']}],desired,available).hash);
 assert.throws(()=>planFactory(1,current,{...desired,batteryCellFactoryList:[{batteryCellModelFactoryId:11,batteryCellModels:['UNKNOWN']}]},available));
});
test('deletion scope approval mandatory for submit; dry-run no writes',async()=>{
 const p=planFactory(1,current,desired,available);let writes=0;const deps={write:async()=>writes++,replan:async()=>p};
 const dry=await executeFactoryPlan(p,{},deps);assert.equal(dry.status,'DRY_RUN');assert.equal(writes,0);
 const opts={commit:true,approvedPlan:p.hash,allowWrite:'1',expectedEmail:'fixture@example.invalid',deploymentProof:'fixture'};
 await assert.rejects(executeFactoryPlan(p,opts,deps));assert.equal(writes,0);
 const r=await executeFactoryPlan(p,{...opts,approvedDeletions:p.deletionHash},deps);assert.equal(r.status,'SUBMITTED_NOT_READBACK_VERIFIED');assert.equal(writes,1);
});

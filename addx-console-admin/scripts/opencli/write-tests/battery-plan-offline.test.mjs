import test from 'node:test';import assert from 'node:assert/strict';
import {planProduction,runProductionOffline} from '../addx-console/battery-plan-offline-core.mjs';
const c={simulation:true,customerType:1,manufacturerId:1,permissions:['BatteryProductionPlanningManagement'],maxProductNumber:5000,batchPlansComplete:true,batchPlans:[],scopedPlanIds:[7],editable:true,current:{id:7,batteryPackModel:'P1',batteryCellModel:'C1',batteryCellBatchCode:'CELLB',batteryPackBatchCode:'PACKB',startNumber:1,endNumber:100,seqLength:5}};
const create={batteryPackModel:'P1',batteryCellModel:'C1',batteryCellBatchCode:'CELLB',batteryPackBatchCode:'PACKB',startSeq:'00001',endSeq:'00100'};
test('create explicitly reserves new padded SN interval; no id',()=>{const p=planProduction('create',create,c);assert.equal(p.status,'OFFLINE_PLAN_ONLY');assert.equal(p.startSn,'PACKB00001');assert.equal(p.planProductNumber,100);assert.equal(Object.hasOwn(p.payload,'id'),false);assert.equal(p.sideEffect,'CREATE_PRODUCTION_PLAN_AND_RESERVE_SN_INTERVAL');});
test('edit targets existing scoped editable ID and preserves model/batch fields',()=>{const p=planProduction('edit',{id:7,startSeq:'00001',endSeq:'00200'},c);assert.equal(p.payload.batteryPackModel,'P1');assert.equal(p.payload.id,7);assert.equal(p.before.endSeq,'00100');assert.throws(()=>planProduction('edit',{id:8,startSeq:'00001',endSeq:'00200'},c));assert.throws(()=>planProduction('edit',{id:7,batteryPackModel:'P2',startSeq:'00001',endSeq:'00200'},c));});
test('LDAP/server role, missing permission or simulated scope reject',()=>{
 for(const context of [{...c,customerType:-1},{...c,permissions:[]},{...c,manufacturerId:null},{...c,simulation:false}])assert.throws(()=>planProduction('create',create,context));
 assert.throws(()=>planProduction('edit',{id:7,startSeq:'00001',endSeq:'00200'},{...c,scopedPlanIds:[]}));
 assert.throws(()=>planProduction('edit',{id:7,startSeq:'00001',endSeq:'00200'},{...c,editable:false}));
});
test('equal sequence widths, integer32 bounds, capacity and global overlap enforced',()=>{
 for(const patch of [{endSeq:'100'},{startSeq:'-0001'},{startSeq:'00101',endSeq:'00100'},{startSeq:'2147483648',endSeq:'2147483648'}])assert.throws(()=>planProduction('create',{...create,...patch},c));
 assert.throws(()=>planProduction('create',create,{...c,maxProductNumber:99}));
 assert.throws(()=>planProduction('create',create,{...c,batchPlansComplete:false}));
 assert.throws(()=>planProduction('create',create,{...c,batchPlans:[{id:5,batteryPackBatchCode:'PACKB',startNumber:100,endNumber:200}]}));
});
test('hash includes current target, desired range, assumptions; submission always refused',async()=>{
 const p=planProduction('edit',{id:7,startSeq:'00001',endSeq:'00200'},c);const q=planProduction('edit',{id:7,startSeq:'00001',endSeq:'00200'},{...c,current:{...c.current,endNumber:99}});assert.notEqual(p.hash,q.hash);
 await assert.rejects(runProductionOffline('create',{mode:'submit'},async()=>{throw new Error('must not read file');}),/not implemented/);
});

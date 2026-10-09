import test from 'node:test';
import assert from 'node:assert/strict';
import {planCell, planPack, executePlan} from '../addx-console/battery-write-core.mjs';
const cell={id:7,batteryCellModel:'C1',batteryCellFactoryId:10,updateTime:'2026-01-01'};
const pack={batteryPackModel:'P1',batteryCellModel:'C1',batteryCellNumber:2,updateTimeUTCSecond:123};
test('cell edit preserves readonly model and requires existing ID',()=>{
 const p=planCell('edit',{id:7,factory:11},[cell]);assert.equal(p.payload.batteryCellModel,'C1');assert.equal(p.payload.id,7);
 assert.throws(()=>planCell('edit',{id:8,factory:11},[cell]));assert.throws(()=>planCell('edit',{id:7,model:'RENAME',factory:11},[cell]));
});
test('create deliberately omits id; duplicates rejected',()=>{
 const p=planCell('create',{model:'C2',factory:11},[cell]);assert.equal(Object.hasOwn(p.payload,'id'),false);
 assert.throws(()=>planCell('create',{id:7,model:'C2',factory:11},[cell]));assert.throws(()=>planCell('create',{model:'C1',factory:11},[cell]));
});
test('pack edit rejects unconfigured rows; initialize is explicit',()=>{
 assert.throws(()=>planPack('edit',{model:'P1',cell:'C1',count:3},[{...pack,updateTimeUTCSecond:null}]));
 assert.throws(()=>planPack('initialize',{model:'P1',cell:'C1',count:3},[pack]));
 assert.equal(planPack('edit',{model:'P1',cell:'C1',count:3},[pack]).payload.batteryPackModel,'P1');
 assert.equal(planPack('initialize',{model:'P1',cell:'C1',count:3},[{...pack,updateTimeUTCSecond:null}]).action,'initialize');
});
test('input bounds and ambiguity reject',()=>{
 for(const count of [0,-1,1.5,NaN])assert.throws(()=>planPack('edit',{model:'P1',cell:'C1',count},[pack]));
 assert.throws(()=>planPack('edit',{model:'P1',cell:'C1',count:3},[pack,pack]));
 assert.throws(()=>planCell('create',{model:' ',factory:1},[]));
});
test('dry run never performs write',async()=>{
 const p=planCell('edit',{id:7,factory:11},[cell]);let writes=0;
 const r=await executePlan(p,{}, {write:async()=>writes++});assert.equal(r.status,'DRY_RUN');assert.equal(writes,0);
});
test('commit gates and stale target reject before write',async()=>{
 const p=planCell('edit',{id:7,factory:11},[cell]);let writes=0;
 const deps={write:async()=>{writes++},replan:async()=>p};
 await assert.rejects(executePlan(p,{commit:true},deps));assert.equal(writes,0);
 const opts={commit:true,approvedPlan:p.hash,allowWrite:'1',expectedEmail:'user@example.invalid',deploymentProof:'operator-reviewed-deployment'};
 await assert.rejects(executePlan(p,opts,{...deps,replan:async()=>planCell('edit',{id:7,factory:11},[{...cell,batteryCellFactoryId:12}])}));assert.equal(writes,0);
 const r=await executePlan(p,opts,deps);assert.equal(r.status,'SUBMITTED_NOT_READBACK_VERIFIED');assert.equal(writes,1);
});

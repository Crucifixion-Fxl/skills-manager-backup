import test from 'node:test';
import assert from 'node:assert/strict';
import {loadCheckoutModule} from './fixtures/checkout-module-loader.mjs';
const {runBatteryWrite}=await loadCheckoutModule('battery-write-runtime.mjs');
process.env.CONSOLE_TOKEN='TEST-NOT-A-CREDENTIAL';
process.env.CONSOLE_EXPECTED_EMAIL='test@example.invalid';
delete process.env.CONSOLE_ALLOW_WRITE;
let calls=[];
globalThis.fetch=async(url,opts)=>{
 const path=new URL(url).pathname;calls.push(path);
 if(path==='/user/info')return Response.json({code:0,data:{id:1,email:'test@example.invalid',userName:'Fixture'}});
 if(path==='/battery_cell/manage/list')return Response.json({code:0,data:{total:1,list:[{id:7,batteryCellModel:'C1',batteryCellFactoryId:10,updateTime:'2026-01-01'}]}});
 if(path==='/battery_package/manage/list')return Response.json({code:0,data:{total:1,list:[{batteryPackModel:'P1',batteryCellModel:'C1',batteryCellNumber:2,updateTimeUTCSecond:123}]}});
 throw new Error('Offline test forbids write endpoint');
};
test('registered runtime dry-run performs only approved reads',async()=>{
 calls=[];const r=await runBatteryWrite('cell',{mode:'dry-run',action:'edit',id:7,factory:10});assert.equal(r[0].status,'DRY_RUN');assert.deepEqual(calls,['/user/info','/battery_cell/manage/list']);
 const p=await runBatteryWrite('pack',{mode:'dry-run',action:'edit',model:'P1',cell:'C1',count:3});assert.equal(p[0].status,'DRY_RUN');
});
test('runtime submit cannot bypass private gate',async()=>{
 calls=[];await assert.rejects(runBatteryWrite('cell',{mode:'submit',action:'edit',id:7,factory:10}),/Write requires/);assert.equal(calls.some(x=>x.includes('saveOrUpdate')),false);
});
test('unknown manufacturer rejected and no credentials persisted',async()=>{
 await assert.rejects(runBatteryWrite('cell',{mode:'dry-run',action:'create',model:'C2',factory:99}),/Factory must/);
 const r=await runBatteryWrite('cell',{mode:'dry-run',action:'create',model:'C2',factory:10});assert.equal(JSON.stringify(r).includes('TEST-NOT-A-CREDENTIAL'),false);
});

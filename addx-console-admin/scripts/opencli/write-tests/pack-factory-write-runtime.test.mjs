import test from 'node:test';import assert from 'node:assert/strict';import {loadCheckoutModule} from './fixtures/checkout-module-loader.mjs';
const {runFactoryWrite}=await loadCheckoutModule('pack-factory-write-runtime.mjs');
const desired={complete:true,packFactoryId:1,batteryCellFactoryList:[{batteryCellModelFactoryId:10,batteryCellModels:['A']}]};
let calls=[];const deps={identity:async()=>({userId:1,email:'fixture@example.invalid'}),readFile:async()=>JSON.stringify(desired),request:async(path,method,payload)=>{
 calls.push({path,method,payload});
 if(path==='/pack_factory/manage/getPackFactories')return [{key:1,value:'Fixture'}];
 if(path==='/pack_factory/manage/list')return [{batteryCellModelFactoryId:10,selectedBatteryCellModels:['A','B']}];
 if(path==='/battery_cell/manage/getBatteryCellModels?batteryCellFactoryId=10')return [{batteryCellFactoryId:10,batteryCellModel:'A'}];
 throw new Error('Offline test forbids production write endpoint');
}};
test('runtime reads full associations and produces deletion diff without submitting',async()=>{
 calls=[];const r=await runFactoryWrite({'factory-id':1,'replacement-file':'fixture',mode:'dry-run'},deps);
 assert.equal(r[0].status,'DRY_RUN');assert.deepEqual(JSON.parse(r[0].deletions),[{factory:10,model:'B'}]);assert.equal(calls.length,3);assert.equal(calls.some(x=>x.path.endsWith('/save')),false);
});
test('malformed and partial input rejected before network',async()=>{
 calls=[];await assert.rejects(runFactoryWrite({'factory-id':1,'replacement-file':'fixture',mode:'dry-run'},{...deps,readFile:async()=>'{'}));assert.equal(calls.length,0);
 await assert.rejects(runFactoryWrite({'factory-id':1,'replacement-file':'fixture',mode:'dry-run'},{...deps,readFile:async()=>JSON.stringify({...desired,complete:false})}),/complete/);
});
test('empty replacement requires exact clear acknowledgement and reports all removals',async()=>{
 const clearDeps={...deps,readFile:async()=>JSON.stringify({...desired,batteryCellFactoryList:[]})};
 await assert.rejects(runFactoryWrite({'factory-id':1,'replacement-file':'fixture',mode:'dry-run'},clearDeps),/Clearing all/);
 const r=await runFactoryWrite({'factory-id':1,'replacement-file':'fixture',mode:'dry-run','clear-factory':1},clearDeps);assert.equal(JSON.parse(r[0].deletions).length,2);
});

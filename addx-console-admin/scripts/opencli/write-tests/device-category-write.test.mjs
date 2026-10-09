import test from 'node:test';import assert from 'node:assert/strict';
import {planCategory,runCategoryDryRun} from '../addx-console/device-category-write-core.mjs';
const catalog=[{id:1,categoryCode:'bird',categoryName:'Bird',versionTime:10,releaseStatus:2}];
const groups=[{id:3},{id:4}];
const current={...catalog[0],modelCategoryComponentDOList:[{componentGroupId:3,required:1}],modelComponentGroupDOList:groups};
test('create deliberately omits id and fixes release false',()=>{const p=planCategory('create',{categoryCode:'door',categoryName:'Door',requiredList:[{componentGroupId:4,required:0}]},catalog,groups);assert.equal(p.payload.saveAndRelease,false);assert.equal(Object.hasOwn(p.payload,'id'),false);assert.equal(p.status,'DRY_RUN_NOT_SUBMITTED');});
test('edit requires existing target and preserves disabled code',()=>{const p=planCategory('edit',{id:1,categoryName:'Bird2',requiredList:[{componentGroupId:4,required:1}]},catalog,groups,current);assert.equal(p.payload.categoryCode,'bird');assert.equal(p.payload.id,1);assert.deepEqual(p.groupDiff.removed,[{componentGroupId:3,required:1}]);assert.throws(()=>planCategory('edit',{id:2,categoryName:'Bird2',requiredList:[]},catalog,groups,current));assert.throws(()=>planCategory('edit',{id:1,categoryCode:'rename',categoryName:'Bird2',requiredList:[]},catalog,groups,current));});
test('release/unknown fields, missing full associations, duplicate resources reject',()=>{
 for(const patch of [{saveAndRelease:true},{extra:'value'},{requiredList:null},{requiredList:[{componentGroupId:3,required:1},{componentGroupId:3,required:0}]},{requiredList:[{componentGroupId:99,required:1}]}])assert.throws(()=>planCategory('create',{categoryCode:'door',categoryName:'Door',requiredList:[],...patch},catalog,groups));
 assert.throws(()=>planCategory('create',{categoryCode:'bird',categoryName:'Door',requiredList:[]},catalog,groups));
 assert.throws(()=>planCategory('create',{categoryCode:'door',categoryName:'Bird',requiredList:[]},catalog,groups));
});
test('hash preserves before version and replacement deletion diff, omits unrelated response secrets',()=>{
 const args={id:1,categoryName:'Bird2',requiredList:[]};const p=planCategory('edit',args,catalog,groups,{...current,secret:'DO-NOT-OUTPUT'});const q=planCategory('edit',args,catalog,groups,{...current,versionTime:11});assert.notEqual(p.hash,q.hash);assert.equal(JSON.stringify(p).includes('DO-NOT-OUTPUT'),false);
});
test('runtime submit rejects before any file or HTTP activity',async()=>{
 await assert.rejects(runCategoryDryRun('create',{mode:'submit'},{readDocument:async()=>{throw new Error('Must not read');}}),/submit is disabled/);
});
const changes={id:1,categoryName:'Bird2',requiredList:[{componentGroupId:4,required:0}]};
function runtimeDeps({pageTotal=1,info=current}={}){const calls=[];return {calls,readDocument:async()=>changes,identity:async()=>({userId:1}),request:async(path,method,payload)=>{calls.push({path,method,payload});if(path==='/device/category/list')return {list:catalog,total:pageTotal};if(path==='/device/category/param/list')return {modelComponentGroupDOList:groups};if(path==='/device/category/info')return info;throw new Error('Offline test forbids write endpoints');}};}
test('runtime edit uses actual-consumer request shapes; no secret params emitted',async()=>{
 const deps=runtimeDeps({info:{...current,paramDefinitions:{password:'DO-NOT-OUTPUT'}}});const p=await runCategoryDryRun('edit',{mode:'dry-run','changes-file':'fixture'},deps);assert.equal(p[0].payload.saveAndRelease,false);assert.equal(JSON.stringify(p).includes('DO-NOT-OUTPUT'),false);assert.deepEqual(deps.calls.map(x=>x.path),['/device/category/list','/device/category/param/list','/device/category/info']);assert.deepEqual(deps.calls[2].payload,{id:1});assert.equal(deps.calls.some(x=>x.path.endsWith('/save')||x.path.endsWith('/release')),false);
});
test('runtime mismatched resource identity and unstable pagination fail closed',async()=>{
 await assert.rejects(runCategoryDryRun('edit',{mode:'dry-run'},runtimeDeps({info:{...current,id:2}})),/Existing category/);
 let count=0;const deps=runtimeDeps();deps.request=async()=>{count++;return {list:catalog,total:count===1?2:3};};await assert.rejects(runCategoryDryRun('edit',{mode:'dry-run'},deps),/changed while scanning/);
});

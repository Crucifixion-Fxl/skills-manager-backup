import test from 'node:test';import assert from 'node:assert/strict';
import {planEmptyGroup,planFileType,runProductDryRun} from '../addx-console/product-resource-write-core.mjs';
const catalog=[{id:1,componentGroupCode:'lens',componentGroupName:'Lens',paramNum:0,versionTime:10,releaseStatus:2,lastModifyTime:'t1'}];
const info={id:1,componentGroupCode:'lens',componentGroupName:'Lens',remark:'',iconId:4,businessType:0,relatedBusiness:0,relatedWorkstation:[8],paramDOList:null,iconManageDOList:[{id:4,url:'DO-NOT-OUTPUT'}]};
test('create empty group uses explicit defaults and forbids release/params',()=>{
 const p=planEmptyGroup('create',{componentGroupCode:'camera',componentGroupName:'Camera',iconId:4},catalog,info);assert.equal(p.payload.saveAndRelease,false);assert.deepEqual(p.payload.paramDOList,[]);assert.equal(Object.hasOwn(p.payload,'id'),false);
 for(const extra of [{saveAndRelease:true},{paramDOList:[]},{businessType:1}])assert.throws(()=>planEmptyGroup('create',{componentGroupCode:'camera',componentGroupName:'Camera',iconId:4,...extra},catalog,info));
});
test('edit only empty parameter groups, preserving code/business/workstations',()=>{
 const p=planEmptyGroup('edit',{id:1,componentGroupName:'Lens2'},catalog,info);assert.equal(p.payload.componentGroupCode,'lens');assert.deepEqual(p.payload.relatedWorkstation,[8]);assert.equal(JSON.stringify(p).includes('DO-NOT-OUTPUT'),false);
 assert.throws(()=>planEmptyGroup('edit',{id:1,componentGroupName:'Lens2'},[{...catalog[0],paramNum:1}],info));
 assert.throws(()=>planEmptyGroup('edit',{id:1,componentGroupName:'Lens2'},catalog,{...info,paramDOList:[{paramValue:'SECRET'}]}));
 assert.throws(()=>planEmptyGroup('edit',{id:1,componentGroupCode:'rename',componentGroupName:'Lens2'},catalog,info));
});
test('group duplicate name/code and unknown icon or mismatched target reject',()=>{
 assert.throws(()=>planEmptyGroup('create',{componentGroupCode:'lens',componentGroupName:'Camera',iconId:4},catalog,info));
 assert.throws(()=>planEmptyGroup('create',{componentGroupCode:'camera',componentGroupName:'Camera',iconId:999},catalog,info));
 assert.throws(()=>planEmptyGroup('edit',{id:2,componentGroupName:'Lens2'},catalog,info));
});
test('file type create/edit distinguish SQL REPLACE side effect and immutable code',()=>{
 const types=[{code:'iq',name:'IQ'}];const p=planFileType('edit',{name:'IQ2'},types,'iq');assert.deepEqual(p.payload,{code:'iq',name:'IQ2'});assert.equal(p.sideEffect,'SQL_REPLACE_EXISTING_ROW_NOT_UPDATE');
 assert.throws(()=>planFileType('create',{code:'iq',name:'Other'},types));assert.throws(()=>planFileType('edit',{name:'IQ2'},types,'missing'));assert.throws(()=>planFileType('edit',{code:'rename',name:'IQ2'},types,'iq'));
 assert.equal(planFileType('create',{code:'manual',name:'IQ'},types).action,'create');
});
test('submit rejects before file/identity/requests',async()=>{await assert.rejects(runProductDryRun('file-type',{mode:'submit'},{readDocument:async()=>{throw new Error('Must not execute');}}),/submit is disabled/);});
function deps(resource){const calls=[];return {calls,readDocument:async()=>resource==='group'?{id:1,componentGroupName:'Lens2'}:{name:'IQ2'},identity:async()=>({userId:1}),request:async(path,method,payload)=>{calls.push({path,method,payload});if(path==='/model/component/group/list')return {list:catalog,total:1};if(path==='/model/component/group/info')return info;if(path==='/filecenter/type/list')return {fileTypes:[{code:'iq',name:'IQ',secret:'DO-NOT-OUTPUT'}]};throw new Error('Write endpoints forbidden in offline test');}};}
test('native-consumer group preconditions only read and omit raw parameter options',async()=>{const d=deps('group');const out=await runProductDryRun('component-group',{mode:'dry-run',action:'edit','changes-file':'fixture'},d);assert.deepEqual(d.calls.map(x=>x.path),['/model/component/group/list','/model/component/group/info']);assert.deepEqual(d.calls[1].payload,{id:1});assert.equal(out[0].payload.saveAndRelease,false);assert.equal(JSON.stringify(out).includes('DO-NOT-OUTPUT'),false);});
test('native-consumer file type reads full list and preserves exact target code',async()=>{const d=deps('type');const out=await runProductDryRun('file-type',{mode:'dry-run',action:'edit','target-code':'iq'},d);assert.deepEqual(out[0].payload,{code:'iq',name:'IQ2'});assert.equal(d.calls.length,1);assert.equal(JSON.stringify(out).includes('DO-NOT-OUTPUT'),false);});
test('legacy unknown params/remark and file name validation fail closed',()=>{assert.throws(()=>planEmptyGroup('edit',{id:1,componentGroupName:'Lens2'},catalog,{...info,remark:null}));assert.throws(()=>planFileType('create',{code:'new',name:null},[]));assert.throws(()=>planFileType('create',{code:'bad/code',name:'Name'},[]));});

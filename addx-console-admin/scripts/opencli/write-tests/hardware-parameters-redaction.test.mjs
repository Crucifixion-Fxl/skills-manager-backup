import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../addx-console/hardware-parameters.js',import.meta.url),'utf8');
class ArgumentError extends Error {} class CommandExecutionError extends Error {} class EmptyResultError extends Error {}
async function fixture(parameters,{identityError=null}={}){
 let command;const calls=[];const context=vm.createContext({});
 const registry=new vm.SyntheticModule(['cli','Strategy'],function(){this.setExport('cli',c=>{command=c;});this.setExport('Strategy',{LOCAL:'LOCAL'});},{context});
 const errors=new vm.SyntheticModule(['ArgumentError','CommandExecutionError','EmptyResultError'],function(){this.setExport('ArgumentError',ArgumentError);this.setExport('CommandExecutionError',CommandExecutionError);this.setExport('EmptyResultError',EmptyResultError);},{context});
 const native=new vm.SyntheticModule(['identity','request'],function(){this.setExport('identity',async()=>{calls.push('identity');if(identityError)throw identityError;return {};});this.setExport('request',async(path,method,body)=>{calls.push({path,method,body});return {componentParamResponses:[{componentGroupName:'Fixture group',componentList:[{componentName:'Fixture component',paramDOList:parameters}]}]};});},{context});
 const m=new vm.SourceTextModule(source,{context});await m.link(s=>s.endsWith('/registry')?registry:s.endsWith('/errors')?errors:native);await m.evaluate();return {command,calls};
}
const param=(code,extra={})=>({paramCode:code,paramName:'Fixture parameter',paramType:'string',value:'SYNTHETIC_PRIVATE_VALUE',...extra});
test('API key, authorization and unknown codes never emit saved or display values',async()=>{
 for(const code of ['apiKey','authorization','newUnknownParameter','password','sessionCookie']){
  const f=await fixture([param(code)]);const [r]=await f.command.func({model:'FIXTURE'});
  assert.equal(r.savedValue,'[REDACTED]');assert.equal(r.displayValue,'[REDACTED]');assert.equal(JSON.stringify(r).includes('SYNTHETIC_PRIVATE_VALUE'),false);
  assert.equal(r.code,code);assert.equal(r.name,'Fixture parameter');
 }
});
test('only audited public boolean codes preserve safe enum values and labels',async()=>{
 for(const [code,value,label] of [['supportBirdDetect','1','支持'],['supportShutterRemote','0','不支持']]){
  const f=await fixture([param(code,{paramType:'enum',value,paramValue:JSON.stringify([{value:0,label:'不支持'},{value:1,label:'支持'}])})]);const [r]=await f.command.func({model:'FIXTURE'});
  assert.equal(r.savedValue,value);assert.equal(r.displayValue,label);
 }
});
test('a public code does not authorize a string, unexpected value or sensitive enum label',async()=>{
 for(const extra of [{},{paramType:'enum',value:'SYNTHETIC_PRIVATE_VALUE',paramValue:'[]'},{paramType:'enum',value:'1',paramValue:JSON.stringify([{value:1,label:'SYNTHETIC_PRIVATE_VALUE'}])}]){
  const f=await fixture([param('supportBirdDetect',extra)]);const [r]=await f.command.func({model:'FIXTURE'});assert.equal(JSON.stringify(r).includes('SYNTHETIC_PRIVATE_VALUE'),false);assert.equal(r.displayValue,'[REDACTED]');
 }
});
test('unknown enum options are not parsed and null remains safe only for public enum',async()=>{
 const f=await fixture([param('unknownEnum',{paramType:'enum',paramValue:'not-json'}),param('supportBirdDetect',{paramType:'enum',value:null})]);const rows=await f.command.func({model:'FIXTURE'});assert.equal(rows[0].savedValue,null);assert.equal(rows[1].savedValue,'[REDACTED]');
});
test('identity preflight, fixed read request, exact-code filter and empty typed error remain',async()=>{
 const f=await fixture([param('unknown')]);await f.command.func({model:'FIXTURE',code:'unknown'});assert.equal(f.calls[0],'identity');assert.equal(f.calls[1].path,'/device/model/component/param/query');assert.equal(f.calls[1].method,'POST');assert.equal(f.calls[1].body.functionType,0);assert.equal(f.calls[1].body.modelNo,'FIXTURE');
 await assert.rejects(f.command.func({model:'FIXTURE',code:'absent'}),EmptyResultError);
 const q=await fixture([param('unknown')],{identityError:new Error('FIXTURE_AUTH_FAILURE')});await assert.rejects(q.command.func({model:'FIXTURE'}));assert.equal(q.calls.length,1);
});

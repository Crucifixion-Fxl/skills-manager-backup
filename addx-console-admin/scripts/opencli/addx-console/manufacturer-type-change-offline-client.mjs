// Unregistered, fixture-only client. No network transport or production submission.
import {createHash} from 'node:crypto';
const clients=new WeakMap();
const plans=new WeakSet();
const fail=code=>{throw new Error(code)};
function record(value,keys){
 if(!value || typeof value!=='object' || Array.isArray(value) || Object.getPrototypeOf(value)!==Object.prototype)fail('INVALID_OBJECT');
 if(Object.keys(value).some(k=>!keys.includes(k)))fail('UNKNOWN_FIELD');
}
const idValid=v=>Number.isSafeInteger(v)&&v>0;
const typeValid=v=>v===0||v===1;
function rows(value){
 if(!Array.isArray(value))fail('DIRECTORY_INVALID');
 const seen=new Set();
 return value.map(row=>{
  if(!row || !idValid(row.id) || typeof row.name!=='string' || !row.name.trim() || !typeValid(row.type) || seen.has(row.id))fail('DIRECTORY_INVALID');
  seen.add(row.id);return {id:row.id,name:row.name,type:row.type};
 }).sort((a,b)=>a.id-b.id);
}
function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
function digest(value){return createHash('sha256').update(JSON.stringify(value)).digest('hex')}
export function createFixtureReadClient(input){
 record(input,['directory','safety']);
 const snapshot=structuredClone(input),calls=[];
 const client=Object.freeze({calls:()=>structuredClone(calls)});
 clients.set(client,{read:async path=>{
  calls.push({method:'GET',path});
  if(path==='/worker/consumer/list')return structuredClone(snapshot.directory);
  const match=/^\/worker\/manufacture\/checkTypeChangeable\?id=([1-9][0-9]*)$/.exec(path);
  if(match)return structuredClone(snapshot.safety?.[match[1]]);
  fail('FIXTURE_ROUTE_DENIED');
 }});return client;
}
export function buildManufacturerTypeRequest(input){
 record(input,['id','type']);
 if(!idValid(input.id))fail('INVALID_ID');
 if(!typeValid(input.type))fail('INVALID_TYPE');
 return {method:'POST',path:'/worker/manufacture/updateType',body:{id:input.id,type:input.type}};
}
export async function planManufacturerTypeChange(intent,client){
 record(intent,['name','type']);
 if(typeof intent.name!=='string'||!intent.name.trim())fail('INVALID_NAME');
 if(!typeValid(intent.type))fail('INVALID_TYPE');
 const reader=clients.get(client);if(!reader)fail('FIXTURE_CLIENT_REQUIRED');
 const directory=rows(await reader.read('/worker/consumer/list'));
 const matches=directory.filter(row=>row.name===intent.name);
 if(matches.length!==1)fail(matches.length?'TARGET_AMBIGUOUS':'TARGET_MISSING');
 const before=matches[0];if(before.type===intent.type)fail('UNCHANGED_TYPE');
 const safety=await reader.read(`/worker/manufacture/checkTypeChangeable?id=${before.id}`);
 if(!safety || Object.keys(safety).some(k=>!['changeable','reasons'].includes(k)) || safety.changeable!==true || !Array.isArray(safety.reasons) || safety.reasons.length!==0)fail('SAFETY_REJECTED');
 // Reasons are deliberately never propagated into output or errors.
 const core={contract:'manufacturer-type-change-fixture-v1',sourceRef:'67ff74a8feab308a7a60b67275094ba1e7cd3180',directory,before,request:buildManufacturerTypeRequest({id:before.id,type:intent.type}),diff:{type:{before:before.type,after:intent.type}},safety:{changeable:true,reasons:[]},submitAllowed:false,liveAccepted:false,atomicCAS:false,deploymentPermissionVerified:false};
 const result=freeze({...core,hash:digest(core)});plans.add(result);return result;
}
export function assertUnchangedPlan(previous,fresh){
 if(!plans.has(previous)||!plans.has(fresh)||previous.hash!==fresh.hash)fail('STALE_PLAN');
 return true;
}
export function verifyManufacturerReadback(plan,observed){
 if(!plans.has(plan))fail('READBACK_INVALID_PLAN');
 let directory;try{directory=rows(observed)}catch{fail('READBACK_INVALID_DIRECTORY')}
 const expected=plan.directory.map(row=>row.id===plan.before.id?{...row,type:plan.request.body.type}:row);
 if(JSON.stringify(directory)!==JSON.stringify(expected))fail('READBACK_MISMATCH');
 return {status:'OFFLINE_READBACK_MATCH',liveAccepted:false};
}
export function submitManufacturerTypeChange(){fail('SUBMIT_HARD_DISABLED')}

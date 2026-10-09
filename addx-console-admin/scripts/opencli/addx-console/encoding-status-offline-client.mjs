// Unregistered fixture-only status wire client. No HTTP dependency or live submit path.
import {createHash} from 'node:crypto';
const clients=new WeakMap(),plans=new WeakMap();
const fail=code=>{throw new Error(code)};
function record(v,allowed){
 if(!v||typeof v!=='object'||Array.isArray(v)||Object.getPrototypeOf(v)!==Object.prototype)fail('INVALID_OBJECT');
 for(const k of Reflect.ownKeys(v)){
  if(typeof k!=='string'||!allowed.includes(k))fail('UNKNOWN_FIELD');
  if(!('value' in Object.getOwnPropertyDescriptor(v,k)))fail('ACCESSOR_FORBIDDEN');
 }
}
const positive=v=>Number.isSafeInteger(v)&&v>0;
const binary=v=>v===0||v===1;
const digest=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
function freeze(v){if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v)}return v}
function normalize(v){
 record(v,['engine','activeRuleIds','generationRecordCount']);
 record(v.engine,['id','name','effectType','status','downstreamOpen','comment','createTime','updateTime']);
 const r=v.engine;
 if(!positive(r.id)||typeof r.name!=='string'||!r.name||!binary(r.effectType)||!binary(r.status)||!binary(r.downstreamOpen)||(r.comment!==null&&typeof r.comment!=='string')||typeof r.createTime!=='string'||typeof r.updateTime!=='string')fail('INVALID_ENGINE');
 if(!Array.isArray(v.activeRuleIds)||v.activeRuleIds.some(x=>!positive(x))||new Set(v.activeRuleIds).size!==v.activeRuleIds.length||!Number.isSafeInteger(v.generationRecordCount)||v.generationRecordCount<0)fail('INVALID_DEPENDENCIES');
 return {engine:{id:r.id,name:r.name,effectType:r.effectType,status:r.status,downstreamOpen:r.downstreamOpen,comment:r.comment,createTime:r.createTime,updateTime:r.updateTime},activeRuleIds:[...v.activeRuleIds].sort((a,b)=>a-b),generationRecordCount:v.generationRecordCount};
}
export function createEncodingFixtureClient(input){
 const snapshot=normalize(input),calls=[];
 const c=Object.freeze({calls:()=>structuredClone(calls)});
 // This is an explicitly offline raw-state provider. It does not assert a public API can return it.
 clients.set(c,()=>{calls.push({kind:'OFFLINE_RAW_ENGINE_DEPENDENCY_METADATA'});return structuredClone(snapshot)});return c;
}
function read(c){const r=clients.get(c);if(!r)fail('FIXTURE_CLIENT_REQUIRED');return r()}
const canGenerate=r=>r.status===1&&r.effectType===1&&r.downstreamOpen===1;
export async function planEncodingStatus(intent,client){
 record(intent,['id','status']);if(!positive(intent.id))fail('INVALID_ID');if(!binary(intent.status))fail('INVALID_STATUS');
 const snapshot=read(client),r=snapshot.engine;if(r.id!==intent.id)fail('TARGET_MISMATCH');if(r.status===intent.status)fail('UNCHANGED_STATUS');
 const core={contract:'encoding-status-fixture-wire-v1',sourceRef:'67ff74a8feab308a7a60b67275094ba1e7cd3180',status:'FIXTURE_WIRE_PREPARED_NOT_LIVE_READY',before:{id:r.id,name:r.name,status:r.status,effectType:r.effectType,downstreamOpen:r.downstreamOpen,updateTime:r.updateTime,activeRuleCount:snapshot.activeRuleIds.length,generationRecordCount:snapshot.generationRecordCount},request:{method:'POST',path:'/code-rules/info/editStatus',body:{id:r.id,status:intent.status}},diff:{status:{before:r.status,after:intent.status}},effect:{engineGenerationBefore:canGenerate(r),engineGenerationAfter:canGenerate({...r,status:intent.status}),notProofOfSpecificRuleGeneration:true},stateHash:digest(snapshot),submitAllowed:false,liveAccepted:false,atomicCAS:false,deploymentPermissionVerified:false,gaps:['Actual caller status-write scope not established by frontend view permission','No deployed authoritative raw-engine/dependency read binding','No ownership/version comparison or affected-row guard in handler','BaseMapper/deployment DB triggers not independently verified','Actual write and downstream generation availability not exercised']};
 const result=freeze({...core,hash:digest(core)});plans.set(result,{snapshot,client});return result;
}
export function assertEncodingPlanFresh(previous,fresh){if(!plans.has(previous)||!plans.has(fresh)||previous.hash!==fresh.hash)fail('STALE_PLAN');return true}
export async function verifyEncodingFixtureReadback(plan,client){
 const state=plans.get(plan);if(!state)fail('READBACK_INVALID_PLAN');if(client===state.client)fail('FRESH_FIXTURE_REQUIRED');
 const observed=read(client),expected=structuredClone(state.snapshot);expected.engine.status=plan.request.body.status;
 // Timestamp is output evidence, not a server CAS condition or proof of actual execution.
 expected.engine.updateTime=observed.engine.updateTime;
 if(JSON.stringify(observed)!==JSON.stringify(expected))fail('READBACK_MISMATCH');
 return {status:'OFFLINE_READBACK_MATCH',liveAccepted:false,atomicCAS:false,generationExecuted:false};
}
export function submitEncodingStatus(){fail('SUBMIT_HARD_DISABLED')}

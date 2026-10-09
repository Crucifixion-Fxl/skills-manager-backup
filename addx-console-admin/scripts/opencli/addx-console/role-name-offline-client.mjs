// Unregistered fixture-only wire client. No HTTP transport, no production permissions assertion.
import {createHash} from 'node:crypto';
const clients=new WeakMap(),plans=new WeakMap();
const fail=code=>{throw new Error(code)};
function record(value,keys){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.getPrototypeOf(value)!==Object.prototype)fail('INVALID_OBJECT');
 for(const key of Reflect.ownKeys(value)){
  if(typeof key!=='string'||!keys.includes(key))fail('UNKNOWN_FIELD');
  const d=Object.getOwnPropertyDescriptor(value,key);if(!d||!('value' in d))fail('ACCESSOR_FORBIDDEN');
 }
}
const positive=v=>Number.isSafeInteger(v)&&v>0;
const name=v=>typeof v==='string'&&v.length>0&&v===v.trim()&&!/[\x00-\x1f\x7f]/.test(v);
const page=v=>name(v)&&v.length<=200;
const digest=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
function freeze(v){if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v)}return v}
function normalize(value){
 record(value,['role','associations','catalogue']);record(value.role,['roleId','roleName','type','customerType','mdate']);
 const r=value.role;
 if(!positive(r.roleId)||!name(r.roleName)||![0,1].includes(r.type)||(r.customerType!==null&&![0,1].includes(r.customerType))||typeof r.mdate!=='string')fail('INVALID_ROLE');
 if(!Array.isArray(value.associations)||!value.associations.length)fail('EMPTY_ASSOCIATIONS');
 if(!Array.isArray(value.catalogue)||value.catalogue.some(v=>!page(v))||new Set(value.catalogue).size!==value.catalogue.length)fail('INVALID_CATALOGUE');
 const catalogue=[...value.catalogue].sort(),seen=new Set();
 const associations=value.associations.map(row=>{
  record(row,['pageId','operations','parameters']);
  if(!page(row.pageId)||typeof row.operations!=='string'||typeof row.parameters!=='string')fail('INVALID_ASSOCIATION');
  if(seen.has(row.pageId))fail('DUPLICATE_ASSOCIATION');seen.add(row.pageId);
  if(!catalogue.includes(row.pageId))fail('DANGLING_ASSOCIATION');
  return {pageId:row.pageId,operations:row.operations,parameters:row.parameters};
 }).sort((a,b)=>a.pageId.localeCompare(b.pageId));
 return {role:{roleId:r.roleId,roleName:r.roleName,type:r.type,customerType:r.customerType,mdate:r.mdate},associations,catalogue};
}
export function createRoleFixtureClient(input){
 const snapshot=normalize(input),calls=[];
 const client=Object.freeze({calls:()=>structuredClone(calls)});
 // Raw complete state exists only as explicit offline fixture. No deployed endpoint is invented.
 clients.set(client,{snapshot:()=>{calls.push({kind:'OFFLINE_RAW_ROLE_ASSOCIATIONS_CATALOGUE'});return structuredClone(snapshot)}});
 return client;
}
function read(client){const c=clients.get(client);if(!c)fail('FIXTURE_CLIENT_REQUIRED');return c.snapshot()}
export async function planRoleNameEdit(intent,client){
 record(intent,['namespace','roleId','roleName']);
 if(!['client','server'].includes(intent.namespace))fail('INVALID_NAMESPACE');
 if(!positive(intent.roleId))fail('INVALID_ID');if(!name(intent.roleName))fail('INVALID_NAME');
 const snapshot=read(client),r=snapshot.role,type=intent.namespace==='client'?0:1;
 if(r.roleId!==intent.roleId)fail('TARGET_MISMATCH');if(r.type!==type)fail('TYPE_MISMATCH');
 if(r.roleName===intent.roleName)fail('UNCHANGED_NAME');
 const request={method:'POST',path:`/permission/edit_${intent.namespace}_role`,body:{roleId:r.roleId,roleName:intent.roleName,rolePages:snapshot.associations.map(({pageId})=>({pageId}))}};
 // Server ignores type/customerType/mdate as edit inputs; no optimistic version field exists.
 const core={contract:'role-name-fixture-wire-v1',sourceRef:'67ff74a8feab308a7a60b67275094ba1e7cd3180',namespace:intent.namespace,before:{...r,pageCount:snapshot.associations.length},request,diff:{roleName:{before:r.roleName,after:intent.roleName},pageIds:{added:[],removed:[]}},stateHash:digest(snapshot),atomicCAS:false,deploymentPermissionVerified:false,submitAllowed:false,liveAccepted:false,status:'FIXTURE_WIRE_PREPARED_NOT_LIVE_READY',sideEffects:['Role mdate update','Unconditional role-user permission event after service transaction','Asynchronous consumer may initialize customer account/snapshot/alert configuration'],gaps:['Deployed full raw-state read contract absent; query role VO filters associations','Actual caller write permission and scope unknown','No server CAS/version guard','Asynchronous outcome not proved by handler response']};
 const result=freeze({...core,hash:digest(core)});plans.set(result,{snapshot,client});return result;
}
export function assertRolePlanFresh(previous,fresh){
 if(!plans.has(previous)||!plans.has(fresh)||previous.hash!==fresh.hash)fail('STALE_PLAN');return true;
}
export async function verifyRoleFixtureReadback(plan,client){
 const prior=plans.get(plan);if(!prior)fail('READBACK_INVALID_PLAN');
 if(client===prior.client)fail('FRESH_FIXTURE_REQUIRED');
 const observed=read(client),expected=structuredClone(prior.snapshot);expected.role.roleName=plan.request.body.roleName;
 // Changed mdate is evidence only; neither equality nor a newer string proves atomicity.
 expected.role.mdate=observed.role.mdate;
 if(JSON.stringify(observed)!==JSON.stringify(expected))fail('READBACK_MISMATCH');
 return {status:'OFFLINE_READBACK_MATCH',liveAccepted:false,atomicCAS:false,asyncEffectsVerified:false};
}
export function submitRoleNameEdit(){fail('SUBMIT_HARD_DISABLED')}

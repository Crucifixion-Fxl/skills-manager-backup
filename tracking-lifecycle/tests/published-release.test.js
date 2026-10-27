 'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { digest } = require('../cli/src/contracts');
const { PlatformClient } = require('../cli/src/platform');
const { verifyPublishedSnapshot, ProductionSchemaReader } = require('../cli/src/published-release');
const event = {type:'SELF_DEFINE',trackerType:'BASE',spm:['ping'],point:'ping',eventName:'ping',name:'Ping',parameters:[],baseSchemas:[]};
const expected = {application:'demo',applicationId:7,releaseId:356,version:'1-0-4',eventDigest:digest([event])};
const schema = {self:{vendor:'com.demo',name:'ping',format:'jsonschema',version:'1-0-4'},type:'object',properties:{},additionalProperties:false};
const path = 'com.demo/ping/jsonschema/1-0-4';
const snapshot = () => ({schemaVersion:1,...expected,releaseStatus:3,events:[event],schemas:[{path,schema:structuredClone(schema)}]});
const reader = new ProductionSchemaReader({baseUrls:['https://schemas.example/schemas'],fetchImpl:async (url,options) => {assert.equal(options.redirect,'error');assert.equal(options.headers,undefined);return {ok:true,json:async()=>schema};}});
test('specified published release passes without querying latest or current tree',async()=>{
 const result=await verifyPublishedSnapshot({expected,snapshot:snapshot(),schemaReader:reader}); assert.equal(result.status,'PASS');assert.equal(result.eventDigest,expected.eventDigest);
});
test('identity, state, digest, schema duplicates and missing schema fail closed',async()=>{
 for(const mutate of [s=>s.releaseId++,s=>s.application='other',s=>s.applicationId++,s=>s.version='1-0-5',s=>s.releaseStatus=2,s=>s.events[0].name='changed',s=>s.schemas=[],s=>s.schemas.push(s.schemas[0])]) {
  const s=snapshot();s.events=structuredClone(s.events);mutate(s);await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader:reader}));
 }
});
test('full constraints are checked, not just parameter types',async()=>{
 const s=snapshot();s.schemas[0].schema.additionalProperties=true;await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader:reader}),/schema differs/i);
});
test('unresolved references and unrelated extra schemas are rejected',async()=>{
 for(const mutate of [s=>s.schemas[0].schema.properties.x={$ref:'#/definitions/missing'},s=>s.schemas[0].schema.properties.x={$ref:'https://untrusted.example/schema'},s=>s.schemas.push({path:'com.demo/extra/jsonschema/1-0-4',schema})]){
 const s=snapshot();mutate(s);await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader:reader}));}
});
test('published snapshot adapter reads only release snapshot and validates physical identities',async()=>{
 const raw={...snapshot(),events:[{id:1,parentApplicationId:7,type:4,spm:['ping'],point:'ping',name:'Ping',parameters:[],baseSchemas:[]}]};
 const client=new PlatformClient({request:async(method,url)=>{assert.equal(method,'GET');assert.equal(url,'/api/release/356/snapshot?applicationId=7');return raw;}});
 assert.equal(digest((await client.readPublishedRelease(expected)).events),expected.eventDigest);
 raw.events.push(raw.events[0]);await assert.rejects(client.readPublishedRelease(expected));
});
test('schema reader rejects ambiguous endpoints',()=>{
 for(const url of ['http://schemas.example','https://user:pass@schemas.example','https://schemas.example/?token=x'])assert.throws(()=>new ProductionSchemaReader({baseUrls:[url]}));
});
test('full frozen reference closure is fetched and checked', async()=>{
 const s=snapshot(); const dep='com.demo/base/jsonschema/1-0-4';
 const base={self:{vendor:'com.demo',name:'base',format:'jsonschema',version:'1-0-4'},definitions:{field:{type:'string',maxLength:5}}};
 s.schemas[0].schema.properties.x={$ref:`iglu:${dep}#/definitions/field`};s.schemas.push({path:dep,schema:base});
 const seen=[];const schemaReader={read:async p=>{seen.push(p);return [structuredClone(s.schemas.find(x=>x.path===p).schema)];}};
 assert.equal((await verifyPublishedSnapshot({expected,snapshot:s,schemaReader})).schemaCount,2);
 assert.deepEqual(seen.sort(),[dep,path].sort());
 s.schemas.pop();await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader}),/missing/);
});
test('all configured production resolvers must match and redirects fail',async()=>{
 let calls=0;
 const schemaReader=new ProductionSchemaReader({baseUrls:['https://a.example/schemas','https://b.example/schemas'],fetchImpl:async()=>({ok:true,json:async()=>++calls===1?schema:{...schema,additionalProperties:true}})});
 await assert.rejects(verifyPublishedSnapshot({expected,snapshot:snapshot(),schemaReader}),/differs/);assert.equal(calls,2);
 const redirected=new ProductionSchemaReader({baseUrls:['https://a.example'],fetchImpl:async()=>({ok:true,redirected:true})});
 await assert.rejects(redirected.read(path),/unavailable/);
});
test('independent context dependency declaration cannot silently pass',async()=>{
 await assert.rejects(verifyPublishedSnapshot({expected:{...expected,schemaDependencies:['context']},snapshot:snapshot(),schemaReader:reader}),/independent context/);
});
test('CLI exports only after complete historical and production verification',async t=>{
 const fs=require('node:fs'),os=require('node:os'),nodePath=require('node:path');
 const {run,argsFrom}=require('../cli/bin/events-tdd');
 const dir=fs.mkdtempSync(nodePath.join(os.tmpdir(),'published-baseline-'));
 const prior=global.fetch;t.after(()=>{global.fetch=prior;fs.rmSync(dir,{recursive:true,force:true});});
 const contract=nodePath.join(dir,'expected.json'),out=nodePath.join(dir,'baseline.yaml');
 fs.writeFileSync(contract,JSON.stringify(expected));
 let drift=false;
 const prodRequests=[];
 global.fetch=async(url,options)=>{
  assert.equal(options.redirect,'error');
  if(String(url).startsWith('https://platform.example')){
   assert.equal(options.headers.Authorization,'Bearer canary');
   return {ok:true,json:async()=>({code:200,data:{...snapshot(),events:[{id:1,parentApplicationId:7,type:4,spm:['ping'],point:'ping',name:'Ping',parameters:[],baseSchemas:[]}]}})};
  }
  assert.equal(options.headers,undefined);
  prodRequests.push(String(url));
  return {ok:true,json:async()=>drift?{...schema,additionalProperties:true}:schema};
 };
 const args=argsFrom([`--expected=${contract}`,'--host=host','--prod-schema-base=https://schemas.example/schemas',`--out=${out}`]);
 const env={TMT_TOKEN:'canary',TRACKING_PLATFORM_BASE_URL:'https://platform.example'};
 await run('pull-published-baseline',args,env);
 const baseline=require('yaml').parse(fs.readFileSync(out,'utf8'));
 assert.equal(baseline.release.id,356);assert.equal(digest(baseline.events),expected.eventDigest);
 assert.deepEqual(prodRequests,['https://schemas.example/schemas/com.demo/ping/jsonschema/1-0-4']);
 prodRequests.length=0;
 const multiple=argsFrom([`--expected=${contract}`,'--host=host',
  '--prod-schema-base=https://schemas.example/schemas',
  '--prod-schema-base=https://backup.example/schemas',`--out=${out}`]);
 assert.deepEqual(multiple['prod-schema-base'],['https://schemas.example/schemas','https://backup.example/schemas']);
 await run('pull-published-baseline',multiple,env);
 assert.deepEqual(prodRequests,[
  'https://schemas.example/schemas/com.demo/ping/jsonschema/1-0-4',
  'https://backup.example/schemas/com.demo/ping/jsonschema/1-0-4']);
 const old=fs.readFileSync(out,'utf8');drift=true;
 await assert.rejects(run('pull-published-baseline',multiple,env),/differs/);assert.equal(fs.readFileSync(out,'utf8'),old);
});
test('schema paths cannot traverse outside configured resolver prefix',async()=>{
 await assert.rejects(reader.read('../ping/jsonschema/1-0-4'),/invalid/);
});
test('literal instance data is not interpreted as schema references or resolution scope',async()=>{
 const s=snapshot();s.schemas[0].schema.properties.metadata={type:'object',default:{id:'user-id',$ref:'business-value'},enum:[{id:'other'}],examples:[{$ref:'literal'}]};
 const schemaReader={read:async()=>[structuredClone(s.schemas[0].schema)]};
 assert.equal((await verifyPublishedSnapshot({expected,snapshot:s,schemaReader})).status,'PASS');
});

test('historical interaction uses only trackers actually emitted by the publisher',async()=>{
 const raw={...snapshot(),events:[{id:1,parentApplicationId:7,type:2,spm:['home','panel'],point:'panel',name:'Panel',parameters:[{name:'id',valueType:'string',isRequired:1,trackerType:1}],baseSchemas:[]}]};
 const client=new PlatformClient({request:async()=>raw});
 const actual=await client.readPublishedRelease(expected);
 assert.equal(actual.events.length,1);assert.equal(actual.events[0].trackerType,'CLK');
 raw.events[0].parameters=[];
 assert.deepEqual((await client.readPublishedRelease(expected)).events.map(e=>e.trackerType),['CLK','EXP']);
});
test('references into annotation locations still validate their transitive schema targets',async()=>{
 const s=snapshot();s.schemas[0].schema.default={nested:{$ref:'iglu:com.demo/missing/jsonschema/1-0-4'}};
 s.schemas[0].schema.properties.x={$ref:'#/default/nested'};
 const schemaReader={read:async()=>[structuredClone(s.schemas[0].schema)]};
 await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader}),/missing/i);
 s.schemas[0].schema.default.nested={$ref:'#/properties/x'};
 assert.equal((await verifyPublishedSnapshot({expected,snapshot:s,schemaReader})).status,'PASS');
});
test('unsupported reference forms and unknown schema keywords fail closed',async()=>{
 const schemaReader={read:async()=>[structuredClone(schema)]};
 for(const property of [
  {$dynamicRef:'iglu:com.demo/missing/jsonschema/1-0-4'},
  {$recursiveRef:'#'},
  {shadow:{$ref:'iglu:com.demo/missing/jsonschema/1-0-4'}},
  {$dynamicAnchor:'node'},
  {contentSchema:{$ref:'iglu:com.demo/missing/jsonschema/1-0-4'}},
 ]) {
  const s=snapshot();s.schemas[0].schema.properties.x=property;
  await assert.rejects(verifyPublishedSnapshot({expected,snapshot:s,schemaReader}),
    /unsupported|unresolved|missing/i);
 }
});

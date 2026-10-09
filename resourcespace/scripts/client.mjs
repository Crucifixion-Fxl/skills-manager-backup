import fs from 'node:fs';
import {openAsBlob} from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {Readable, Transform} from 'node:stream';
import {pipeline} from 'node:stream/promises';
export class Failure extends Error { constructor(code,message){super(message);this.code=code;} }
const fail=(code,message)=>{throw new Failure(code,message)};
export function integer(value,label,min=1,max=Number.MAX_SAFE_INTEGER){const n=Number(value);if(!Number.isSafeInteger(n)||n<min||n>max)fail('ARGUMENT',`${label} out of range`);return n;}
export function config(env=process.env){
 const base=new URL(env.RESOURCESPACE_URL || fail('AUTH_REQUIRED','Set RESOURCESPACE_URL'));
 if(base.username||base.password||base.search||base.hash)fail('ARGUMENT','Base URL cannot contain credentials or query');
 if(base.protocol!=='https:' && !(base.protocol==='http:' && env.RESOURCESPACE_ALLOW_HTTP==='true'))fail('ARGUMENT','HTTPS required; approved local PoC may explicitly allow HTTP');
 if(!env.RESOURCESPACE_USER)fail('AUTH_REQUIRED','Set RESOURCESPACE_USER');
 let key=env.RESOURCESPACE_API_KEY;
 if(!key){try{const stat=fs.lstatSync(env.RESOURCESPACE_API_KEY_FILE);if(!stat.isFile()||(stat.mode&0o077)||stat.uid!==process.getuid())throw Error();key=fs.readFileSync(env.RESOURCESPACE_API_KEY_FILE,'utf8').trim();}catch{fail('AUTH_REQUIRED','API key file must be owned by current user and mode 600');}}
 if(!/^[a-f0-9]{64}$/i.test(key))fail('AUTH_REQUIRED','Invalid API key');
 return {base:base.href.replace(/\/$/,''),user:env.RESOURCESPACE_USER,key};
}
export function scrub(value){
 if(Array.isArray(value))return value.map(scrub);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,/^(sign|access_key|api_key|password|sessionkey)$/i.test(k)?'[redacted]':scrub(v)]));
 if(typeof value==='string')return value.replace(/([?&](?:sign|access_key|api_key|sessionkey)=)[^&#\s"<>]*/gi,'$1[redacted]');
 return value;
}
async function request(url,options={},fetcher=fetch){
 try{const r=await fetcher(url,{...options,redirect:'manual',signal:AbortSignal.timeout(120000)});if(r.status===401||r.status===403)fail('AUTH_REQUIRED','API authentication or permission denied');if(!r.ok)fail('COMMAND_EXEC',`HTTP ${r.status}; no automatic retry`);return r;}catch(e){if(e instanceof Failure)throw e;if(e.name==='TimeoutError'||e.name==='AbortError')fail('TIMEOUT','Request timed out; inspect state before retrying writes');fail('COMMAND_EXEC','Request failed; signed URLs suppressed');}
}
export async function api(c,fn,params={},upload,fetcher=fetch){
 const q=new URLSearchParams({user:c.user,function:fn,...params}).toString();const sign=crypto.createHash('sha256').update(c.key+q).digest('hex');
 const form=upload ? new FormData():undefined;
 if(upload)form.append('file',await openAsBlob(upload),path.basename(upload));
 const r=await request(c.base+'/api/?'+q+'&sign='+sign,form?{method:'POST',body:form}:{},fetcher);
 if(upload && r.status===204)return {uploaded:true};
 let bytes=0;const chunks=[];
 for await(const chunk of r.body){bytes+=chunk.length;if(bytes>8*1024*1024)fail('COMMAND_EXEC','API response exceeds 8 MiB');chunks.push(chunk);}
 let result;try{result=JSON.parse(Buffer.concat(chunks).toString())}catch{fail('COMMAND_EXEC','Non-JSON API response');}
 if(result===false||result===null||result?.error||['error','fail'].includes(result?.status)||result?.success===false)fail('COMMAND_EXEC','API returned failure; response details suppressed');
 return result;
}
export const definitions=[
 ['status','read',[]],['search','read',['query','limit','offset','archive']],['resource','read',['ref']],['metadata','read',['ref']],['types','read',[]],['fields','read',[]],['collection','read',['ref']],
 ['create','write',['type','apply']],['update-field','write',['ref','field','value-file','apply']],['upload','write',['ref','file','apply']],['create-collection','write',['name','apply']],['add-to-collection','write',['ref','collection','apply']],['download','read',['ref','extension','size','output']]
].map(([name,access,args])=>({name,access,args}));
export async function perform(name,a={},c=config(),fetcher=fetch){
 const d=definitions.find(x=>x.name===name);if(!d)fail('ARGUMENT','Unknown command');
 if(Object.keys(a).some(k=>!d.args.includes(k)))fail('ARGUMENT','Unknown command argument');
 if(d.access==='write' && a.apply!==true && a.apply!=='true')fail('ARGUMENT','Writes require --apply=true and task authorization');
 const id=()=>integer(a.ref,'ref');const call=(fn,p,file)=>api(c,fn,p,file,fetcher);
 let result;
 switch(name){
 case 'status':result=await call('get_system_status',{basic:'true'});break;
 case 'search':if(typeof a.query!=='string'||!a.query.trim())fail('ARGUMENT','query required');result=await call('do_search',{search:a.query,fetchrows:String(integer(a.limit??20,'limit',1,100)),offset:String(integer(a.offset??0,'offset',0)),archive:String(integer(a.archive??0,'archive',-2,2))});break;
 case 'resource':result=await call('get_resource_data',{resource:id()});break;
 case 'metadata':result=await call('get_resource_field_data',{resource:id()});break;
 case 'types':result=await call('get_resource_types');break;
 case 'fields':result=await call('get_resource_type_fields');break;
 case 'collection':result=await call('get_collection',{ref:id()});break;
 case 'create':result={ref:await call('create_resource',{resource_type:integer(a.type,'type'),archive:-2})};break;
 case 'update-field':{const value=fs.readFileSync(a['value-file'],'utf8');if(Buffer.byteLength(value)>65536)fail('ARGUMENT','Metadata value too large');const ref=id(),field=integer(a.field,'field');await call('update_field',{resource:ref,field,value});result={updated:true,metadata:await call('get_resource_field_data',{resource:ref})};break;}
 case 'upload':{const ref=id();const st=fs.lstatSync(a.file);if(!st.isFile()||st.size>1024**3)fail('ARGUMENT','Upload needs regular file at most 1 GiB');await call('upload_multipart',{ref,no_exif:'0',revert:'0',file_name:path.basename(a.file)},a.file);result={uploaded:true,resource:await call('get_resource_data',{resource:ref})};break;}
 case 'create-collection':if(!a.name)fail('ARGUMENT','name required');result={ref:await call('create_collection',{name:a.name})};break;
 case 'add-to-collection':{const ref=id(),collection=integer(a.collection,'collection');await call('add_resource_to_collection',{resource:ref,collection});result={added:true,collection:await call('get_collection',{ref:collection})};break;}
 case 'download':{
 const ref=id();if(!/^[a-zA-Z0-9]{1,12}$/.test(a.extension??''))fail('ARGUMENT','extension required');if(!a.output)fail('ARGUMENT','output required');
 const link=await call('get_resource_path',{ref,extension:a.extension,size:a.size??'',generate:'false'});if(typeof link!=='string'||!link)fail('EMPTY_RESULT','Download unavailable');
 const url=new URL(link,c.base);if(url.origin!==new URL(c.base).origin||url.username||url.password)fail('COMMAND_EXEC','Cross-origin downloads unsupported; verify storage routing first');
 const response=await request(url.href,{},fetcher);if(/text\/html/i.test(response.headers.get('content-type')??''))fail('COMMAND_EXEC','Download returned a page instead of media');const target=path.resolve(a.output);let fd;
 try{fd=fs.openSync(target,'wx',0o600);}catch{fail('ARGUMENT','Output must be a new writable file');}
 const hash=crypto.createHash('sha256');let bytes=0;const meter=new Transform({transform(chunk,enc,cb){bytes+=chunk.length;if(bytes>2*1024**3)return cb(new Error('limit'));hash.update(chunk);cb(null,chunk);}});
 try{await pipeline(Readable.fromWeb(response.body),meter,fs.createWriteStream(target,{fd}));}catch{fs.rmSync(target,{force:true});fail('COMMAND_EXEC','Download interrupted; partial output removed');}
 result={ref,path:target,bytes,sha256:hash.digest('hex')};break;
 }
 }
 return scrub(result);
}

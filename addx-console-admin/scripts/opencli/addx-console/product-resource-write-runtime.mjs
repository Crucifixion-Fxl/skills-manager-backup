import {readFile} from 'node:fs/promises';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {runProductDryRun} from './product-resource-write-core.mjs';
export async function runProductWrite(resource,args){
 try{return await runProductDryRun(resource,args,{identity,request,readDocument:async path=>{try{const raw=await readFile(path,'utf8');if(Buffer.byteLength(raw)>1048576)throw new Error();return JSON.parse(raw);}catch{throw new ArgumentError('Changes file must be bounded business JSON; contents are not echoed');}}});}
 catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||['AuthRequiredError','TimeoutError'].includes(e.name))throw e;throw new ArgumentError(e.message);}
}
export const productWriteArgs=[{name:'action',required:true,valueRequired:true,choices:['create','edit']},{name:'changes-file',required:true,valueRequired:true,help:'Business JSON only; no credentials/context/associations/release controls'},{name:'mode',valueRequired:true,choices:['dry-run'],default:'dry-run',help:'Native read preconditions and plan only; submit disabled'}];
export const productWriteColumns=['status','resource','action','scope','parameterMappingProof','before','payload','diff','hash','sideEffect','deploymentAndActionPermission','submissionImplemented'];

import {readFile} from 'node:fs/promises';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {runCategoryDryRun} from './device-category-write-core.mjs';
export async function runCategoryWrite(action,args){
 try{
  return await runCategoryDryRun(action,args,{identity,request,readDocument:async path=>{
   try{const raw=await readFile(path,'utf8');if(Buffer.byteLength(raw)>1048576)throw new Error();return JSON.parse(raw);}catch{throw new ArgumentError('Changes file must be bounded business JSON; contents are not echoed');}
  }});
 }catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||['AuthRequiredError','TimeoutError'].includes(e.name))throw e;throw new ArgumentError(e.message);}
}
export const categoryWriteArgs=[{name:'changes-file',valueRequired:true,required:true,help:'Business JSON with full requiredList; edit requires id and preserves code; no credentials/context'},{name:'mode',valueRequired:true,choices:['dry-run'],default:'dry-run',help:'Native resource reads and diff only; submit disabled'}];
export const categoryWriteColumns=['status','resource','action','scope','before','payload','groupDiff','hash','deploymentAndActionPermission','submissionImplemented'];

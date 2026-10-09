import {readFile} from 'node:fs/promises';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {runMinimumFirmwareDryRun,MinimumFirmwarePlanError} from './minimum-firmware-write-core.mjs';
export async function runMinimumFirmwareWrite(args){
 args={...args};delete args.__opencliOptionSources; // Internal 1.8.8 provenance, never a business userdata field.
 try{return await runMinimumFirmwareDryRun(args,{identity,request,readDocument:async path=>{try{const raw=await readFile(path,'utf8');if(Buffer.byteLength(raw)>1048576)throw new Error();return JSON.parse(raw);}catch{throw new ArgumentError('Changes file must be bounded business JSON; contents not echoed');}}});}
 catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||['AuthRequiredError','TimeoutError'].includes(e.name))throw e;if(e instanceof MinimumFirmwarePlanError)throw new ArgumentError(e.message);throw new CommandExecutionError('Minimum firmware planning prerequisite failed; details redacted');}
}

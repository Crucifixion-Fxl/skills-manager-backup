import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {runFileMetadataDryRun} from './file-metadata-write-core.mjs';
export async function runFileMetadataWrite(action,args){
 const input={mode:args.mode,targetCode:args['target-code']};
 if(action==='rename'){input.fileId=args['file-id'];input.fileName=args['file-name'];}
 try{return await runFileMetadataDryRun(action,input,{identity,request});}
 catch(e){if(e instanceof ArgumentError||e instanceof CommandExecutionError||['AuthRequiredError','TimeoutError'].includes(e.name))throw e;throw new ArgumentError(e.message);}
}
export const fileMetadataArgs=[{name:'target-code',required:true,valueRequired:true,help:'Exact existing file type code; no implicit creation'},{name:'mode',default:'dry-run',choices:['dry-run'],valueRequired:true,help:'Native read preconditions and candidate plan only; submit disabled'}];
export const fileMetadataColumns=['status','action','before','payload','diff','hash','endpoint','sideEffect','versionCAS','snapshotVersionCoverage','deploymentAndActionPermission','submissionImplemented','readSideEffect'];

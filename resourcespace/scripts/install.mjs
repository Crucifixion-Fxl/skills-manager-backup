import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {fileURLToPath,pathToFileURL} from 'node:url';
import {definitions} from './client.mjs';
const root=process.argv[2]||path.join(os.homedir(),'.opencli');const entry=pathToFileURL(fileURLToPath(new URL('./client.mjs',import.meta.url))).href;
const source=`// ResourceSpace official signed API via installed AddX Skill. Reinstall after moving the Skill.
import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,EmptyResultError,CommandExecutionError,AuthRequiredError,TimeoutError} from '@jackwener/opencli/errors';
import {perform,definitions} from ${JSON.stringify(entry)};
for(const d of definitions)cli({site:'resourcespace',name:d.name,description:'ResourceSpace '+d.name,strategy:Strategy.LOCAL,browser:false,access:d.access,defaultFormat:'json',args:d.args.map(name=>({name,type:'string',help:name})),columns:['command','result'],func:async args=>{try{return [{command:d.name,result:await perform(d.name,Object.fromEntries(d.args.filter(k=>args[k]!==undefined).map(k=>[k,args[k]])))}]}catch(e){switch(e.code){case 'ARGUMENT':throw new ArgumentError(e.message);case 'EMPTY_RESULT':throw new EmptyResultError(d.name,e.message);case 'AUTH_REQUIRED':throw new AuthRequiredError('resourcespace',e.message);case 'TIMEOUT':throw new TimeoutError('ResourceSpace',120);default:throw new CommandExecutionError(e.code?e.message:'ResourceSpace operation failed');}}}});
`;
const dir=path.join(root,'clis/resourcespace');fs.mkdirSync(dir,{recursive:true});const out=path.join(dir,'resourcespace.js');
if(fs.existsSync(out)&&fs.readFileSync(out,'utf8')!==source)throw Error('Existing adapter differs; refusing overwrite');fs.writeFileSync(out,source);console.log(JSON.stringify({adapter:out,commands:definitions.length}));

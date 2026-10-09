#!/usr/bin/env node
import {perform,definitions} from './client.mjs';
const [command,...argv]=process.argv.slice(2);const args={};
try{
 if(command==='help'||!command){console.log(JSON.stringify(definitions));process.exit(0);}
 for(const item of argv){const match=/^--([a-z-]+)=(.*)$/s.exec(item);if(!match)throw Error('Arguments use --name=value');args[match[1]]=match[2];}
 console.log(JSON.stringify(await perform(command,args)));
}catch(e){console.error(JSON.stringify({code:e.code??'ARGUMENT',message:e.code?e.message:'Invalid arguments or local file'}));process.exit(1);}

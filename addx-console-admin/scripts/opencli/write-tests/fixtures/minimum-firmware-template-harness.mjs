// Offline registry/schema hydration; actual checkout adapter+runtime, not a real CLI parser acceptance.
import {loadCheckoutModule} from './checkout-module-loader.mjs';
let definition,consumerCalled=false,fetchCalls=0;
globalThis.fetch=async()=>{fetchCalls++;throw Error('OFFLINE_NETWORK_FORBIDDEN')};
try{
 await loadCheckoutModule('minimum-firmware-write.js',{onRegister:def=>definition=def});
 if(!definition||definition.site!=='addx-console'||definition.name!=='minimum-firmware-write'||definition.access!=='write')throw Error('REGISTRY_SCHEMA_CHANGED');
 const flags=JSON.parse(process.argv[2]),args={};
 for(const arg of definition.args)if(arg.default!==undefined)args[arg.name]=arg.default;
 const sources={};
 for(let i=0;i<flags.length;i+=2){
  const key=flags[i]?.replace(/^--/,'');const spec=definition.args.find(x=>x.name===key);
  if(!spec||typeof flags[i+1]!=='string'||(spec.choices&&!spec.choices.includes(flags[i+1])))throw Error('ARGUMENT_ERROR');
  args[key]=flags[i+1];sources[key]='cli';
 }
 // Exact internal option provenance shape from the pinned Bridge is part of the consumer contract.
 args.__opencliOptionSources=sources;consumerCalled=true;
 const rows=await definition.func(args);
 console.log(JSON.stringify({rows,consumerCalled,fetchCalls,scope:'OFFLINE_REGISTRY_HYDRATION_NOT_REAL_CLI'}));
}catch{console.log(JSON.stringify({status:'REFUSED',consumerCalled,fetchCalls}));process.exitCode=1}

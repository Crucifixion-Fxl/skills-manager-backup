// Test-only loader: evaluates checkout modules and mocks package errors, never private adapters.
import fs from 'node:fs/promises';import vm from 'node:vm';
export async function loadCheckoutModule(name,options={}){
 const root=new URL('../../addx-console/',import.meta.url),cache=new Map();
 const context=vm.createContext({process,Buffer,Response,AbortSignal,URL,console,fetch:(...args)=>globalThis.fetch(...args)});
 class OfflineError extends Error{constructor(message){super(message)}}
 const errors={ArgumentError:OfflineError,CommandExecutionError:OfflineError,AuthRequiredError:OfflineError,TimeoutError:OfflineError};
 const synthetic=(values)=>new vm.SyntheticModule(Object.keys(values),function(){for(const [k,v]of Object.entries(values))this.setExport(k,v)},{context});
 async function load(spec){
  if(cache.has(spec))return cache.get(spec);
  if(spec==='@jackwener/opencli/registry'){if(typeof options.onRegister!=='function')throw Error('REGISTRY_NOT_EXPECTED');const m=synthetic({cli:options.onRegister,Strategy:{LOCAL:'local'}});cache.set(spec,m);return m}
  if(spec==='@jackwener/opencli/errors'){const m=synthetic(errors);cache.set(spec,m);return m}
  if(spec.startsWith('node:')){if(!['node:crypto','node:fs/promises'].includes(spec))throw Error('UNAPPROVED_BUILTIN');const m=synthetic(await import(spec));cache.set(spec,m);return m}
  if(!/^\.\/[A-Za-z0-9_-]+\.(mjs|js)$/.test(spec))throw Error('UNAPPROVED_MODULE');
  const source=await fs.readFile(new URL(spec.slice(2),root),'utf8');
  const m=new vm.SourceTextModule(source,{context,identifier:new URL(spec.slice(2),root).href});cache.set(spec,m);await m.link(load);return m;
 }
 const module=await load('./'+name);await module.evaluate();return module.namespace;
}

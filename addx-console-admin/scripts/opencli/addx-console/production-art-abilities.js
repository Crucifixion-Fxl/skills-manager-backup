import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'production-art-abilities',access:'read',description:'Production art ability names; unpaginated dictionary, limit bounds local output only; excludes timestamps and write actions',strategy:Strategy.LOCAL,browser:false,args:[{name:'limit',type:'int',default:20,help:'Local output limit 1 to 100, not server pagination'}],columns:['id','name','returnedTotal'],func:async(args)=>{
 const limit=Number(args.limit);if(!Number.isSafeInteger(limit)||limit<1||limit>100)throw new ArgumentError('limit must be 1 to 100');
 await identity();const data=await request('/produce/art/all-produce-art-ability','POST',{});
 if(!data||!Array.isArray(data.list)||data.list.some(row=>!row||!Number.isSafeInteger(row.id)||row.id<1||typeof row.name!=='string')||new Set(data.list.map(row=>row.id)).size!==data.list.length)throw new CommandExecutionError('Production art ability dictionary contract changed');
 if(!data.list.length)throw new EmptyResultError('addx-console production-art-abilities','No visible production art ability definitions');
 return data.list.slice(0,limit).map(row=>({id:row.id,name:row.name,returnedTotal:data.list.length}));
}});

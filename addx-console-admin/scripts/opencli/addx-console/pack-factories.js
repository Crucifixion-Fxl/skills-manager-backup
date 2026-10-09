import {cli,Strategy} from '@jackwener/opencli/registry';
import {CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'pack-factories',access:'read',description:'List configured Pack factories',strategy:Strategy.LOCAL,browser:false,args:[],columns:['id','name'],func:async()=>{
 await identity();const data=await request('/pack_factory/manage/getPackFactories');
 if(!Array.isArray(data)||data.length>1000)throw new CommandExecutionError('Pack factory list contract changed');
 if(!data.length)throw new EmptyResultError('addx-console pack-factories','No configured factories');
 return data.map(x=>{if(!Number.isInteger(x.key)||typeof x.value!=='string')throw new CommandExecutionError('Pack factory record contract changed');return{id:x.key,name:x.value};}).sort((a,b)=>a.id-b.id);
}});

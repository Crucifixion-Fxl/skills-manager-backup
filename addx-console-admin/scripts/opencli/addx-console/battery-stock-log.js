import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'battery-stock-log',access:'read',description:'Read existing change history for one stock record',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Existing stock record ID'},{name:'limit',type:'int',default:20,help:'Output limit, 1 to 100; server returns all history'}],columns:['id','recordId','content','operator','createdAt'],func:async(args)=>{
 const id=Number(args.id),limit=Number(args.limit);if(!Number.isSafeInteger(id)||id<1||!Number.isInteger(limit)||limit<1||limit>100)throw new ArgumentError('id must be positive and limit must be 1 to 100');
 await identity();const data=await request('/pack_warehousing/manage/log?id='+id);
 if(!Array.isArray(data))throw new CommandExecutionError('Stock log contract changed');
 if(!data.length)throw new EmptyResultError('addx-console battery-stock-log','No visible historical log entries');
 const rows=data.map(x=>{if(!Number.isSafeInteger(x.id)||x.keyId!==id||x.source!==0||![x.content,x.operator,x.createTime].every(v=>typeof v==='string'))throw new CommandExecutionError('Stock log record contract changed');return{id:x.id,recordId:x.keyId,content:x.content,operator:x.operator,createdAt:x.createTime};});
 return rows.sort((a,b)=>b.id-a.id).slice(0,limit);
}});

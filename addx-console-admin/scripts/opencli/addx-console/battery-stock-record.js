import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {stockRecord} from './stock-record.mjs';
cli({site:'addx-console',name:'battery-stock-record',access:'read',description:'Read one authorized battery stock record',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Existing stock record ID'}],columns:['id','model','batch','cellFactory','packFactory','quantity','remaining','createdAt'],func:async(args)=>{
 const id=Number(args.id);if(!Number.isSafeInteger(id)||id<1)throw new ArgumentError('id must be a positive safe integer');
 await identity();const d=await request('/pack_warehousing/manage/info/'+id);if(d?.id!==id)throw new CommandExecutionError('Stock record identity mismatch');return[stockRecord(d)];
}});

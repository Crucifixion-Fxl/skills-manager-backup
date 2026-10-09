import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {productRecord} from './cloud-product-record.mjs';
cli({site:'addx-console',name:'cloud-product',access:'read',description:'Read one existing cloud service product',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Existing product ID'}],columns:['id','name','price','currency','settlementStrategy','tier','oemType','createdAt','updatedAt'],func:async(args)=>{
 const id=Number(args.id);if(!Number.isSafeInteger(id)||id<1)throw new ArgumentError('id must be a positive safe integer');
 await identity();const x=await request('/cloud/service/product/info','POST',{id});if(x?.id!==id)throw new CommandExecutionError('Cloud product identity mismatch');return[productRecord(x)];
}});

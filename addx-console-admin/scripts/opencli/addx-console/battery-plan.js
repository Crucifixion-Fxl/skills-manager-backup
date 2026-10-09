import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'battery-plan',access:'read',description:'Read one authorized battery production plan',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Existing production plan ID'}],columns:['id','model','cellModel','cellFactory','cellBatch','packBatch','startSequence','endSequence'],func:async(args)=>{
 const id=Number(args.id);if(!Number.isSafeInteger(id)||id<1)throw new ArgumentError('id must be a positive safe integer');
 await identity();const x=await request('/battery_product_plan/info/'+id);
 if(!x||x.id!==id||!['batteryPackModel','batteryCellModel','batteryCellFactory','batteryCellBatchCode','batteryPackBatchCode'].every(k=>typeof x[k]==='string')||![x.startNumber,x.endNumber,x.seqLength].every(Number.isSafeInteger)||x.startNumber<0||x.endNumber<x.startNumber||x.seqLength<1||x.seqLength>16||String(x.endNumber).length>x.seqLength)throw new CommandExecutionError('Battery plan detail contract changed');
 return[{id:x.id,model:x.batteryPackModel,cellModel:x.batteryCellModel,cellFactory:x.batteryCellFactory,cellBatch:x.batteryCellBatchCode,packBatch:x.batteryPackBatchCode,startSequence:String(x.startNumber).padStart(x.seqLength,'0'),endSequence:String(x.endNumber).padStart(x.seqLength,'0')}];
}});

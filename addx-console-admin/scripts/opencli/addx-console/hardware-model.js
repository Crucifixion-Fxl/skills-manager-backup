import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'hardware-model',access:'read',description:'Read hardware model basic information; component parameters are separate',strategy:Strategy.LOCAL,browser:false,args:[{name:'model',type:'string',required:true,help:'Existing exact model number'}],columns:['id','model','modelType','category','productId','relationModel','displayModels','materialNo','description'],func:async(args)=>{
 if(typeof args.model!=='string'||!args.model.trim()||args.model.length>128)throw new ArgumentError('model must be an existing nonempty model number of at most 128 characters');
 const model=args.model.trim();await identity();const d=await request('/device/model/queryDeviceModel','POST',{functionType:0,modelNo:model});
 if(!d||d.modelNo!==model||!Number.isSafeInteger(d.id)||d.id<1||!Number.isInteger(d.modelType)||!Array.isArray(d.categoryList)||!Array.isArray(d.displayModelNoList))throw new CommandExecutionError('Hardware model detail contract changed');
 const category=d.categoryList.find(x=>x.id===d.categoryId)?.categoryName;
 const displayModels=d.displayModelNoList.map(x=>{if(typeof x.displayModelNo!=='string')throw new CommandExecutionError('Hardware display model contract changed');return x.displayModelNo;}).sort();
 return[{id:d.id,model:d.modelNo,modelType:d.modelType,category,productId:d.productId,relationModel:d.relationModel,displayModels,materialNo:d.materialNo,description:d.description}];
}});

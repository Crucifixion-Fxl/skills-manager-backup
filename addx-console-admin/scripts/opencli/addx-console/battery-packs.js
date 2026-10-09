import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'battery-packs',access:'read',description:'Read a bounded page of battery packs',strategy:Strategy.LOCAL,browser:false,args:[{name:'model',type:'string',help:'Exact battery pack model'},{name:'cell-model',type:'string',help:'Exact battery cell model'},{name:'supplier-id',type:'int',help:'Positive Pack factory ID'},{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size, 1 to 20'}],columns:['model','cellModel','cellCount','factories','updatedAt','total'],func:async(args)=>{
 const page=Number(args.page),size=Number(args.limit);
 if(!Number.isInteger(page)||page<1)throw new ArgumentError('page must be a positive integer');
 if(!Number.isInteger(size)||size<1||size>20)throw new ArgumentError('limit must be an integer from 1 to 20');
 const payload={pageIndex:page,pageSize:size};
 for(const [arg,key] of [['model','batteryPackModel'],['cell-model','batteryCellModel']]){if(args[arg]!==undefined){if(typeof args[arg]!=='string'||!args[arg].trim()||args[arg].length>128)throw new ArgumentError(arg+' must be a nonempty model of at most 128 characters');payload[key]=args[arg].trim();}}
 if(args['supplier-id']!==undefined){const id=Number(args['supplier-id']);if(!Number.isInteger(id)||id<1)throw new ArgumentError('supplier-id must be a positive integer');payload.supplierId=id;}
 await identity();
 const result=await request('/battery_package/manage/list','POST',payload);
 if(!result||!Array.isArray(result.list)||!Number.isInteger(result.total)||result.total<0||result.list.length>size)throw new CommandExecutionError('Console battery pack pagination contract mismatch');
 if(result.list.length===0)throw new EmptyResultError('addx-console battery-packs','This page has no records');
 return result.list.map(record=>{
  if(typeof record.batteryPackModel!=='string'||(record.batteryCellModel!==null&&typeof record.batteryCellModel!=='string')||(record.batteryCellNumber!==null&&!Number.isInteger(record.batteryCellNumber))||(record.packFactories!==null&&(!Array.isArray(record.packFactories)||!record.packFactories.every(x=>typeof x==='string'))))throw new CommandExecutionError('Console battery pack record shape changed');
  return {model:record.batteryPackModel,cellModel:record.batteryCellModel,cellCount:record.batteryCellNumber,factories:record.packFactories,updatedAt:record.updateTime,total:result.total};
 });
}});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'battery-plans',access:'read',description:'Read a bounded page of visible battery production plans',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size, 1 to 20'}],columns:['id','model','factory','batch','planned','produced','createdAt','total'],func:async(args)=>{
 const page=Number(args.page),size=Number(args.limit);
 if(!Number.isInteger(page)||page<1)throw new ArgumentError('page must be a positive integer');
 if(!Number.isInteger(size)||size<1||size>20)throw new ArgumentError('limit must be an integer from 1 to 20');
 await identity();const data=await request('/battery_product_plan/list','POST',{pageIndex:page,pageSize:size});
 if(!data||!Array.isArray(data.list)||!Number.isInteger(data.total)||data.total<0||data.list.length>size)throw new CommandExecutionError('Battery plan pagination contract changed');
 if(!data.list.length)throw new EmptyResultError('addx-console battery-plans','This page has no visible production plans');
 return data.list.map(x=>{
  if(!Number.isInteger(x.id)||typeof x.batteryPackModel!=='string'||typeof x.batteryPackBatchCode!=='string'||!Number.isInteger(x.planProductNumber)||!Number.isInteger(x.productNumber))throw new CommandExecutionError('Battery plan record contract changed');
  return{id:x.id,model:x.batteryPackModel,factory:x.packFactory,batch:x.batteryPackBatchCode,planned:x.planProductNumber,produced:x.productNumber,createdAt:x.createTime,total:data.total};
 });
}});

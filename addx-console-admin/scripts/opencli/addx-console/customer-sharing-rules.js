import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'customer-sharing-rules',access:'read',description:'Read customer sharing rules; excludes factory branch with mutation side effects',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size, 1 to 20'}],columns:['customerCode','ruleId','configured','type','coefficient','cashBackEnabled','cashBack','shareAmount','currency','updatedAt','total'],func:async(args)=>{
 const page=Number(args.page),size=Number(args.limit);if(!Number.isInteger(page)||page<1||!Number.isInteger(size)||size<1||size>20)throw new ArgumentError('page must be positive and limit must be 1 to 20');
 await identity();const d=await request('/devide/coefficient/cuid/list','POST',{name:'',type:'',whetherCashBack:'',configStatus:'',customerChannelList:[],pageIndex:page,pageSize:size});
 if(!d||!Array.isArray(d.list)||!Number.isSafeInteger(d.total)||d.total<0||d.list.length>size)throw new CommandExecutionError('Customer sharing rule pagination contract changed');
 if(!d.list.length)throw new EmptyResultError('addx-console customer-sharing-rules','No visible customers on this page');
 return d.list.map(x=>{
  if(typeof x.cuid!=='string'||!Number.isSafeInteger(x.id)||x.id<0||![x.type,x.coefficient,x.whetherCashBack,x.cashBack,x.shareAmount].every(v=>v===null||(typeof v==='number'&&Number.isFinite(v)))||(x.currency!==null&&typeof x.currency!=='string'))throw new CommandExecutionError('Customer sharing rule record contract changed');
  return{customerCode:x.cuid,ruleId:x.id,configured:x.id!==0,type:x.type,coefficient:x.coefficient,cashBackEnabled:x.whetherCashBack,cashBack:x.cashBack,shareAmount:x.shareAmount,currency:x.currency,updatedAt:x.mdateStr,total:d.total};
 });
}});

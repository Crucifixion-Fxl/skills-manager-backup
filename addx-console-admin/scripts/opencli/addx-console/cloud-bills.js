import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'cloud-bills',access:'read',description:'Read a bounded page of App or non-App cloud bills',strategy:Strategy.LOCAL,browser:false,args:[{name:'from',type:'string',help:'Inclusive start month YYYY-MM; requires to'},{name:'to',type:'string',help:'Inclusive end month YYYY-MM; requires from'},{name:'customer',type:'string',help:'Exact existing enterprise code from cloud-bill-customers'},{name:'group',type:'string',required:true,help:'app or non-app'},{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size, 1 to 20'}],columns:['customerCode','month','amount','currency','services','recordOemType','estimated','total','totalServices','summaryUsd','summaryCny'],func:async(args)=>{
 const page=Number(args.page),size=Number(args.limit);if(!['app','non-app'].includes(args.group)||!Number.isInteger(page)||page<1||!Number.isInteger(size)||size<1||size>20)throw new ArgumentError('group must be app/non-app, page positive, limit 1 to 20');
 const hasRange=args.from!==undefined||args.to!==undefined;const validMonth=x=>typeof x==='string'&&/^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(x);if(hasRange&&(!validMonth(args.from)||!validMonth(args.to)||args.from>args.to))throw new ArgumentError('from and to must both be YYYY-MM months in ascending order');
 if(args.customer!==undefined&&(typeof args.customer!=='string'||!args.customer.trim()||args.customer.length>128))throw new ArgumentError('customer must be a nonempty exact code of at most 128 characters');
 await identity();const d=await request('/cloud/oem/order/list','POST',{startDate:hasRange?args.from:'',endDate:hasRange?args.to:'',cuid:args.customer?.trim()||'',pageIndex:page,pageSize:size,oemType:args.group==='app'?1:2});
 if(!d||!Array.isArray(d.list)||!Number.isSafeInteger(d.total)||d.total<0||d.list.length>size||!Number.isSafeInteger(d.totalNum)||![d.summaryAmountUs,d.summaryAmountCn].every(x=>typeof x==='number'&&Number.isFinite(x)))throw new CommandExecutionError('Cloud bill pagination/summary contract changed');
 if(!d.list.length)throw new EmptyResultError('addx-console cloud-bills','No visible bills on this page');
 return d.list.map(x=>{
  if(typeof x.cuid!=='string'||typeof x.staticMonth!=='string'||typeof x.amount!=='number'||!Number.isFinite(x.amount)||typeof x.currency!=='string'||!Number.isSafeInteger(x.num)||typeof x.expected!=='boolean'||!Number.isInteger(x.oemType))throw new CommandExecutionError('Cloud bill record contract changed');
  return{customerCode:x.cuid,month:x.staticMonth,amount:x.amount,currency:x.currency,services:x.num,recordOemType:x.oemType,estimated:x.expected,total:d.total,totalServices:d.totalNum,summaryUsd:d.summaryAmountUs,summaryCny:d.summaryAmountCn};
 });
}});

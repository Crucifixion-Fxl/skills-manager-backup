import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
import {productRecord} from './cloud-product-record.mjs';
cli({site:'addx-console',name:'cloud-products',access:'read',description:'Read a bounded page of cloud service products',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size, 1 to 20'}],columns:['id','name','price','currency','settlementStrategy','tier','oemType','createdAt','updatedAt','total'],func:async(args)=>{
 const page=Number(args.page),size=Number(args.limit);if(!Number.isInteger(page)||page<1||!Number.isInteger(size)||size<1||size>20)throw new ArgumentError('page must be positive and limit must be 1 to 20');
 await identity();const d=await request('/cloud/service/product/list','POST',{createDate:'',lastModifyDate:'',id:'',cancel:'',pageIndex:page,pageSize:size});
 if(!d||!Array.isArray(d.list)||!Number.isSafeInteger(d.total)||d.total<0||d.list.length>size)throw new CommandExecutionError('Cloud product pagination contract changed');
 if(!d.list.length)throw new EmptyResultError('addx-console cloud-products','No visible products on this page');
 return d.list.map(x=>({...productRecord(x),total:d.total}));
}});

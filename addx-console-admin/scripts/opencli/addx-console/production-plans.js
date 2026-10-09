import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {productionPlanListQuery,productionPlanRows} from './production-plan-records.mjs';
cli({site:'addx-console',name:'production-plans',access:'read',description:'Read bounded existing default production-process plans and association counts',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page'},{name:'limit',type:'int',default:10,help:'Page size 1 to 20'}],columns:['id','name','modelCount','manufacturerCount','totalManufacturerCount','publishStatus','total'],func:async(args)=>{const p=productionPlanListQuery(args);await identity();return productionPlanRows(await request(p.path,'POST',{}),p.limit);}});

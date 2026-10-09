import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {bindingListPath,bindingRows} from './production-binding-records.mjs';
cli({site:'addx-console',name:'production-bindings',access:'read',description:'Read bounded binding-process configuration summaries for published production plans',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page'},{name:'limit',type:'int',default:10,help:'Page size 1 to 20'}],columns:['model','planId','modelType','category','status','statusName','configuredArtCount','totalArtCount','total'],func:async(args)=>{const p=bindingListPath(args);await identity();return bindingRows(await request(p.path,'POST',{}),p.limit);}});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {batchPage,batchRows} from './sn-batch-records.mjs';
cli({site:'addx-console',name:'sn-batches',access:'read',description:'Paged generation batch model/aggregate count metadata only; excludes serial numbers, orders, people, rules, details and generation/status writes',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page'},{name:'limit',type:'int',default:10,help:'Server page size 1 to 20'}],columns:['id','model','quantity','status','uiStatus','availableCount','usedCount','disabledCount','total'],func:async args=>{const page=batchPage(args);await identity();return batchRows(await request(page.path,'GET'),page.size);}});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {localLimit,releaseRows} from './release-task-records.mjs';
cli({site:'addx-console',name:'pending-release-tasks',access:'read',description:'Read pending release task metadata only; no PR synchronization, publish, cancellation, links, personal names or configuration',strategy:Strategy.LOCAL,browser:false,args:[{name:'limit',type:'int',default:10,help:'Local output limit 1 to 20, server returns unpaged current-window list'}],columns:['id','type','uiTypeName','itemName','releaseStatus','hasPrLink','createdAt','total'],func:async args=>{const limit=localLimit(args.limit);await identity();return releaseRows(await request('/device/release/release/tasks/-1','GET'),limit);}});

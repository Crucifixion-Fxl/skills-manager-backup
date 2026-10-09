import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {boundedPage} from './expansion-records.mjs';
import {mappingRows} from './function-mapping-records.mjs';
cli({site:'addx-console',name:'function-mappings',access:'read',description:'Read bounded function mapping master metadata without PR links or values',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page number'},{name:'limit',type:'int',default:10,help:'Page size 1 to 20'}],columns:['componentId','name','code','groupName','groupCode','categories','modelTypes','releaseStatus','status','createdAt','updatedAt','total'],func:async(args)=>{const body=boundedPage(args);await identity();return mappingRows(await request('/model/component/param/master/list','POST',body),body.pageSize);}});

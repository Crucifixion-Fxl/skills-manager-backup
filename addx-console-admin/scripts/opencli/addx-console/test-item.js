import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {observedItemId,itemMetadata} from './test-item-detail-records.mjs';
cli({site:'addx-console',name:'test-item',access:'read',description:'Exact existing test item library metadata; excludes scripts, thresholds, logs, people and remarks; not management info initialization',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Existing ID observed in a fresh authorized test-items list'}],columns:['id','itemCode','itemName','paramCount','paramNames','artCount'],func:async args=>{const id=observedItemId(args.id);await identity();return[itemMetadata(await request('/test/item/info','POST',{id}),id)];}});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {meterId,meterDetail} from './electricity-meter-records.mjs';
cli({site:'addx-console',name:'electricity-meter',access:'read',description:'Read exact existing electricity meter public metadata, no file or PR URLs',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Exact existing meter ID from an authorized list'}],columns:['id','code','type','capacity','version','releaseStatus','updatedAt','releasedCode','releasedType','releasedCapacity','supplierIds'],func:async(args)=>{const id=meterId(args);await identity();const data=await request('/device/model/battery/info','POST',{id});return[meterDetail(data,id)];}});

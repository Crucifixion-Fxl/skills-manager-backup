import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {detailId,categoryDetail} from './resource-detail-records.mjs';
cli({site:'addx-console',name:'device-category',access:'read',description:'Read exact existing resource public metadata only',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Exact existing resource ID from an authorized list'}],columns:["id", "name", "code", "version", "releasedVersion", "releaseStatus", "componentGroupIds", "componentGroupCount", "updatedAt"],func:async(args)=>{const id=detailId(args);await identity();const data=await request('/device/category/info','POST',{id});return [categoryDetail(data,id)];}});

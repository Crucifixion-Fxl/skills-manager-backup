import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {detailId,groupDetail} from './resource-detail-records.mjs';
cli({site:'addx-console',name:'component-group',access:'read',description:'Read exact existing resource public metadata only',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Exact existing resource ID from an authorized list'}],columns:["id", "name", "code", "categories", "businessType", "relatedBusiness", "workstationIds", "parameterCodes", "parameterCount", "subParameterCount"],func:async(args)=>{const id=detailId(args);await identity();const data=await request('/model/component/group/info','POST',{id});return [groupDetail(data,id)];}});

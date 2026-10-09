import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {integrationId,integrationDetail} from './factory-integration-detail-records.mjs';
cli({site:'addx-console',name:'factory-integration',access:'read',description:'Read existing integration selected metadata; requestedId is request provenance, DTO does not echo ID',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Exact existing ID from factory-integrations'}],columns:['requestedId','manufacturerId','productType','integrationType','codeRulesRuleId'],func:async(args)=>{const id=integrationId(args);await identity();return[integrationDetail(await request('/factory_integration/manage/detail?id='+id,'GET'),id)];}});

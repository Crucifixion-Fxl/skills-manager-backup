import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {boundedPage} from './expansion-records.mjs';
import {integrationId,integrationFileRows} from './factory-integration-detail-records.mjs';
cli({site:'addx-console',name:'factory-integration-files',access:'read',description:'Read existing integration uploaded-record metadata without filenames, operator, URLs or content',strategy:Strategy.LOCAL,browser:false,args:[{name:'id',type:'int',required:true,help:'Exact existing ID from factory-integrations'},{name:'page',type:'int',default:1,help:'Positive page'},{name:'limit',type:'int',default:10,help:'Page size 1 to 20'}],columns:['requestedIntegrationId','fileRecordId','records','status','uploadedAt','total'],func:async(args)=>{const id=integrationId(args),body={...boundedPage(args),integrationId:id};await identity();return integrationFileRows(await request('/factory_integration/manage/file/list','POST',body),id,body.pageSize);}});

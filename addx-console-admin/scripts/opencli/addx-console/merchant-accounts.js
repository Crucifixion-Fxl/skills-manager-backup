import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {merchantRead} from './merchant-records.mjs';
cli({site:'addx-console',name:'merchant-accounts',access:'read',description:'Read prepaid company/App metadata and separate active-account/unopened counts; omit amounts, tenant IDs and alert recipients',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive source page'},{name:'limit',type:'int',default:10,help:'Source page rows1..20'}],columns:['companyCode','companyName','customerType','customerTypeLabel','appName','accountStatus','activeAccountCount','remainingOEMMapCount','requestedPage','returnedRows'],func:args=>merchantRead(args,{identity,request})});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {boundedPage} from './expansion-records.mjs';
import {minimumFirmwareRows} from './minimum-firmware-records.mjs';
cli({site:'addx-console',name:'minimum-firmwares',access:'read',description:'Read bounded existing model minimum-firmware configuration',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive page'},{name:'limit',type:'int',default:10,help:'Page size 1 to 20'}],columns:['model','minimumFirmware','total'],func:async(args)=>{const b=boundedPage(args);await identity();const data=await request('/factory/firmware/list?pageIndex='+b.pageIndex+'&pageSize='+b.pageSize,'GET');return minimumFirmwareRows(data,b.pageSize);}});

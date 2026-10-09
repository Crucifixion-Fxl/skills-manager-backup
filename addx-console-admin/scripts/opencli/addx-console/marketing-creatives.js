import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity} from './native.mjs';
import {marketingRead,marketingRequest} from './marketing-list-records.mjs';
cli({site:'addx-console',name:'marketing-creatives',access:'read',description:'Read bounded current marketing creatives metadata, omit links/layouts/content/UUIDs/IDs',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive source page'},{name:'limit',type:'int',default:10,help:'Source rows 1 to 10'}],columns:['name', 'createdAtEpoch', 'updatedAtEpoch', 'ctype', 'width', 'height', 'language', 'requestedPage', 'returnedRows', 'total'],func:args=>marketingRead(args,'creatives',{identity,request:marketingRequest})});

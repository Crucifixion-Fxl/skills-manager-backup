import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {clientUserRead} from './client-user-summary-records.mjs';
cli({site:'addx-console',name:'client-user-summary',access:'read',description:'Aggregate bounded client-user page by company and exact composite role labels; omit individual and permission fields',strategy:Strategy.LOCAL,browser:false,args:[{name:'page',type:'int',default:1,help:'Positive source page, no enumeration'},{name:'limit',type:'int',default:10,help:'Source page rows 1 to 10'}],columns:['companyLabel','roleLabelsText','pageGroupUserCount','requestedPage','returnedPageUsers','sourceTotalUsers'],func:args=>clientUserRead(args,{identity,request})});

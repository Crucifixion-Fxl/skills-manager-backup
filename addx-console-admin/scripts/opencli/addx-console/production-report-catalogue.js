import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {catalogueArgs,catalogueRows} from './production-report-catalogue-records.mjs';
cli({site:'addx-console',name:'production-report-catalogue',access:'read',strategy:Strategy.LOCAL,browser:false,description:'Read default cached production report catalogue counts only; no device seed, filters, report values or export',args:[],columns:['category','cachedEntryCount','cacheState','cachedReportCount','reportCountState'],func:async args=>{const body=catalogueArgs(args);await identity();return catalogueRows(await request('/factory/test-item-result/export-info','POST',body));}});

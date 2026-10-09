import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {typeEndpoint,typeRows} from './factory-integration-records.mjs';
cli({site:'addx-console',name:'factory-integration-types',access:'read',description:'Read public product or integration type dictionary',strategy:Strategy.LOCAL,browser:false,args:[{name:'kind',type:'string',default:'integration',help:'integration or product'}],columns:['code','name'],func:async(args)=>{const endpoint=typeEndpoint(args);await identity();return typeRows(await request(endpoint,'GET'));}});

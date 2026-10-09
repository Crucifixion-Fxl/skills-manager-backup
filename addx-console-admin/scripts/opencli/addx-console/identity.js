import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity} from './native.mjs';
cli({site:'addx-console',name:'identity',access:'read',description:'Read current Console target identity',strategy:Strategy.LOCAL,browser:false,args:[],columns:['userId','name','email'],func:async()=>[await identity()]});

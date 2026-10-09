import {cli,Strategy} from '@jackwener/opencli/registry';
import {CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'battery-models',access:'read',description:'Read battery cell model selections after identity check',strategy:Strategy.LOCAL,browser:false,args:[],columns:['model'],func:async()=>{await identity();const records=await request('/battery_cell/manage/selections');if(!Array.isArray(records)||records.some(x=>typeof x!=='string'))throw new CommandExecutionError('Console model response shape changed');if(records.length===0)throw new EmptyResultError('addx-console battery-models','No selectable models');return records.map(value=>({model:value}));}});

import {cli,Strategy} from '@jackwener/opencli/registry';
import {runProductWrite,productWriteArgs,productWriteColumns} from './product-resource-write-runtime.mjs';
cli({site:'addx-console',name:'component-group-write',access:'write',description:'Native observed-empty group metadata planning; proposed code/association IDs retained; hidden mappings unresolved; submit disabled',strategy:Strategy.LOCAL,browser:false,args:productWriteArgs,columns:productWriteColumns,func:args=>runProductWrite('component-group',args)});

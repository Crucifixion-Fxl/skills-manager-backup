import {cli,Strategy} from '@jackwener/opencli/registry';
import {runCategoryWrite,categoryWriteArgs,categoryWriteColumns} from './device-category-write-runtime.mjs';
cli({site:'addx-console',name:'device-category-create',access:'write',description:'Plan category creation from actual native identity/catalog reads; release forbidden and submit disabled',strategy:Strategy.LOCAL,browser:false,args:categoryWriteArgs,columns:categoryWriteColumns,func:args=>runCategoryWrite('create',args)});

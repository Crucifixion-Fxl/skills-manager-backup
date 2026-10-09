import {cli,Strategy} from '@jackwener/opencli/registry';
import {runCategoryWrite,categoryWriteArgs,categoryWriteColumns} from './device-category-write-runtime.mjs';
cli({site:'addx-console',name:'device-category-edit',access:'write',description:'Plan existing category edit preserving UI-immutable code; full association diff; submit disabled',strategy:Strategy.LOCAL,browser:false,args:categoryWriteArgs,columns:categoryWriteColumns,func:args=>runCategoryWrite('edit',args)});

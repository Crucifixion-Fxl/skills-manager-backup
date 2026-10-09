import {cli,Strategy} from '@jackwener/opencli/registry';
import {runProductWrite,productWriteArgs,productWriteColumns} from './product-resource-write-runtime.mjs';
cli({site:'addx-console',name:'file-type-write',access:'write',description:'Native file type create/edit planning; SQL REPLACE effect explicit; submit disabled',strategy:Strategy.LOCAL,browser:false,args:[...productWriteArgs,{name:'target-code',valueRequired:true,help:'Exact existing immutable code for edit; forbidden for create'}],columns:productWriteColumns,func:args=>runProductWrite('file-type',args)});

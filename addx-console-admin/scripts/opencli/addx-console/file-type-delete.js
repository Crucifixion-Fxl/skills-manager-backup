import {cli,Strategy} from '@jackwener/opencli/registry';
import {runFileMetadataWrite,fileMetadataArgs,fileMetadataColumns} from './file-metadata-write-runtime.mjs';
cli({site:'addx-console',name:'file-type-delete',access:'write',description:'Plan deletion of an existing empty file type; includes disabled/unpublished files; submit disabled',strategy:Strategy.LOCAL,browser:false,args:fileMetadataArgs,columns:fileMetadataColumns,func:args=>runFileMetadataWrite('delete-type',args)});

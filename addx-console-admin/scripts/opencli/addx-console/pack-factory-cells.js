import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
cli({site:'addx-console',name:'pack-factory-cells',access:'read',description:'Read cell factory/model associations for one Pack factory',strategy:Strategy.LOCAL,browser:false,args:[{name:'factory-id',type:'int',required:true,help:'Positive Pack factory ID'}],columns:['cellFactoryId','cellFactory','availableModels','selectedModels'],func:async(args)=>{
 const id=Number(args['factory-id']);if(!Number.isInteger(id)||id<1)throw new ArgumentError('factory-id must be a positive integer');
 await identity();const data=await request('/pack_factory/manage/list','POST',{packFactoryId:id});
 if(!Array.isArray(data)||data.length>1000)throw new CommandExecutionError('Pack factory cell list contract changed');
 if(!data.length)throw new EmptyResultError('addx-console pack-factory-cells','No configured cell associations');
 return data.map(x=>{
  if(!Number.isInteger(x.batteryCellModelFactoryId)||typeof x.batteryCellModelFactoryName!=='string'||![x.batteryCellModels,x.selectedBatteryCellModels].every(a=>Array.isArray(a)&&a.every(v=>typeof v==='string')))throw new CommandExecutionError('Pack factory cell record contract changed');
  return{cellFactoryId:x.batteryCellModelFactoryId,cellFactory:x.batteryCellModelFactoryName,availableModels:[...x.batteryCellModels].sort(),selectedModels:[...x.selectedBatteryCellModels].sort()};
 }).sort((a,b)=>a.cellFactoryId-b.cellFactoryId);
}});

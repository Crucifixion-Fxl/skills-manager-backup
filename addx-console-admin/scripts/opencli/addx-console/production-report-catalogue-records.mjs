import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
export function catalogueArgs(args){
 if(!args||typeof args!=='object'||Array.isArray(args))throw new ArgumentError('No business parameters are accepted');
 for(const k of Object.keys(args))if(k!=='__opencliOptionSources')throw new ArgumentError('No business parameters are accepted');
 if(Object.hasOwn(args,'__opencliOptionSources')){const s=args.__opencliOptionSources;if(!s||typeof s!=='object'||Array.isArray(s)||Object.keys(s).length)throw new ArgumentError('Unexpected framework option provenance');}
 return {componentModelNos:'',createTimestampEnd:'',createTimestampStart:'',derivationModelNos:'',displayModelNos:'',manufacturerIds:'',modelNos:'',produceArtId:''};
}
const fields=[['totalModelNos','PCBA型号'],['totalDerivationModelNos','整机型号'],['totalComponentModelNos','部件型号'],['totalDisplayModelNos','客户型号']];
export function catalogueRows(data){
 const fail=()=>{throw new CommandExecutionError('Production report cache metadata shape changed');};
 if(!data||typeof data!=='object'||Array.isArray(data))fail();
 const hasTotal=Object.hasOwn(data,'total'),total=hasTotal?data.total:null;
 if(total!==null&&(!Number.isSafeInteger(total)||total<0))fail();
 if(!fields.some(([key])=>Object.hasOwn(data,key)))fail();
 return fields.map(([key,category])=>{
  const present=Object.hasOwn(data,key),values=present?data[key]:null;
  if(values!==null){if(!Array.isArray(values)||values.length>10000)fail();const seen=new Set();for(const value of values){if(typeof value!=='string'||value.length>512||/[\x00-\x1f\x7f]/.test(value)||seen.has(value))fail();seen.add(value);}}
  return {category,cachedEntryCount:values===null?null:values.length,cacheState:!present?'MISSING':values===null?'NULL':'AVAILABLE',cachedReportCount:total,reportCountState:!hasTotal?'MISSING':total===null?'NULL':'AVAILABLE'};
 });
}

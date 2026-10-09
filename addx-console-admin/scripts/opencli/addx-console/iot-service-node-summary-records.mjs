import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
export function validateNodeSummaryArgs(args){
 if(!args||typeof args!=='object'||Array.isArray(args))throw new ArgumentError('No business parameters accepted');
 for(const key of Object.keys(args))if(key!=='__opencliOptionSources')throw new ArgumentError('No business parameters accepted');
 if(Object.hasOwn(args,'__opencliOptionSources')){const s=args.__opencliOptionSources;if(!s||typeof s!=='object'||Array.isArray(s)||Object.keys(s).length)throw new ArgumentError('Unexpected framework option provenance');}
}
export function nodeSummaryRows(data){
 const fail=()=>{throw new CommandExecutionError('Service-node catalogue metadata shape changed');};
 if(!Array.isArray(data)||data.length>100)fail();
 if(data.length===0)throw new EmptyResultError('No configured service-node groups returned');
 const seen=new Set();return data.map(row=>{
  if(!row||typeof row!=='object'||Array.isArray(row)||typeof row.evn!=='string'||row.evn.length===0||row.evn.length>128||/[\x00-\x1f\x7f]/.test(row.evn)||/^[a-z][a-z0-9+.-]*:\/\//i.test(row.evn)||seen.has(row.evn))fail();seen.add(row.evn);
  if(!Array.isArray(row.nodeList)||row.nodeList.length>1000||row.nodeList.some(n=>!n||typeof n!=='object'||Array.isArray(n)))fail();
  return {environment:row.evn,configuredNodeCount:row.nodeList.length};
 });
}

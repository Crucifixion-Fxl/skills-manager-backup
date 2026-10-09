import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
export function ownMonthArgs(args){
 if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).some(k=>!['page','limit','__opencliOptionSources'].includes(k)))throw new ArgumentError('Only bounded own-context pagination is accepted');
 if(Object.hasOwn(args,'__opencliOptionSources')){const s=args.__opencliOptionSources;if(!s||typeof s!=='object'||Array.isArray(s)||Object.keys(s).some(k=>!['page','limit'].includes(k)||s[k]!=='cli'))throw new ArgumentError('Unexpected framework option provenance');}
 const page=Number(args.page),limit=Number(args.limit);if(!Number.isSafeInteger(page)||page<1||page>10000||!Number.isSafeInteger(limit)||limit<1||limit>20)throw new ArgumentError('page 1..10000 and limit 1..20 required');
 return {startMonth:'',endMonth:'',pageIndex:page,pageSize:limit};
}
const labels={0:'普通设备',1:'门铃设备',2:'4G设备'};
export function ownMonthRows(data,body){
 const fail=()=>{throw new CommandExecutionError('Own-company monthly registration metadata shape changed');};
 if(!data||!Array.isArray(data.list)||data.list.length>body.pageSize||!Number.isSafeInteger(data.total)||data.total<data.list.length)fail();
 if(!data.list.length)throw new EmptyResultError('No own-company registration month rows on this page; not authentication failure');
 const months=new Set(),rows=[];
 for(const group of data.list){if(!group||typeof group.month!=='string'||!/^\d{4}-(0[1-9]|1[0-2])$/.test(group.month)||months.has(group.month)||!Array.isArray(group.deviceDetails)||!group.deviceDetails.length||group.deviceDetails.length>3)fail();months.add(group.month);const types=new Set();
  for(const detail of group.deviceDetails){if(!detail||detail.month!==group.month||!Number.isInteger(detail.deviceType)||![0,1,2].includes(detail.deviceType)||types.has(detail.deviceType)||(detail.registerCount!==null&&(!Number.isSafeInteger(detail.registerCount)||detail.registerCount<0)))fail();types.add(detail.deviceType);rows.push({month:group.month,deviceType:detail.deviceType,deviceTypeLabel:labels[detail.deviceType],registerCount:detail.registerCount,totalMonths:data.total,requestedPage:body.pageIndex});}
 }
 return rows;
}

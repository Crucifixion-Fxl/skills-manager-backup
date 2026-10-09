import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
const known=['kind','customer','manufacturer','config','start-month','end-month','page','limit','__opencliOptionSources'];
const validMonth=v=>typeof v==='string'&&/^\d{4}-(0[1-9]|1[0-2])$/.test(v);
const monthIndex=v=>Number(v.slice(0,4))*12+Number(v.slice(5,7))-1;
const argument=()=>new ArgumentError('Invalid bounded 4G statistics query; use exact observed enterprise filter');
export function simStatsQuery(args){
 if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).some(k=>!known.includes(k)))throw argument();
 const kind=args.kind??'customer',page=Number(args.page??1),size=Number(args.limit??10);
 if(!['customer','factory'].includes(kind)||!Number.isSafeInteger(page)||page<1||!Number.isSafeInteger(size)||size<1||size>20)throw argument();
 const body={customerType:kind==='customer'?0:1,pageIndex:page,pageSize:size};
 if(args.customer!==undefined){if(kind!=='customer'||typeof args.customer!=='string'||!args.customer.trim()||args.customer.length>128||args.customer!==args.customer.trim())throw argument();body.cuid=args.customer;}
 if(args.manufacturer!==undefined){const id=Number(args.manufacturer);if(kind!=='factory'||!Number.isSafeInteger(id)||id<1)throw argument();body.manufacturerId=id;}
 if(args.config!==undefined){const v=Number(args.config);if(!Number.isInteger(v)||![0,1].includes(v))throw argument();body.configStatus=v;}
 const start=args['start-month'],end=args['end-month'];
 if(start!==undefined||end!==undefined){if(!validMonth(start)||!validMonth(end)||monthIndex(end)<monthIndex(start)||monthIndex(end)-monthIndex(start)>11)throw argument();body.startMonth=start;body.endMonth=end;}
 return body;
}
export function simStatsRows(data,body){
 const bad=()=>new CommandExecutionError('4G statistics metadata contract changed');
 if(data===null)throw new EmptyResultError('addx-console sim-revenue-stats','Native service returned null for this page; no success row synthesized');
 if(!data||typeof data!=='object'||!Array.isArray(data.list)||!Number.isSafeInteger(data.total)||data.total<0||data.total<data.list.length||data.list.length>body.pageSize)throw bad();
 if(!data.list.length)throw new EmptyResultError('addx-console sim-revenue-stats','No visible statistics rows on this page');
 const count=(obj,key)=>{if(!Object.hasOwn(obj,key)||obj[key]!==null&&!Number.isSafeInteger(obj[key]))throw bad();return obj[key];};
 const summary={rowCount:data.list.length,total:data.total,totalDeviceCount:count(data,'totalDeviceNum'),totalActiveBefore30Days:count(data,'totalActiveNum'),totalActiveWithin30Days:count(data,'totalActiveNumInThirtyDay')};
 return data.list.map(row=>{if(!row||typeof row!=='object'||!validMonth(row.month)||!Object.hasOwn(row,'revenueRule')||row.revenueRule!==null&&typeof row.revenueRule!=='string')throw bad();
  return {kind:body.customerType===0?'customer':'factory',month:row.month,deviceCount:count(row,'deviceNum'),activeBefore30Days:count(row,'activeNum'),activeWithin30Days:count(row,'activeNumInThirtyDay'),incentiveDeviceCount:count(row,'activationIncentiveDeviceNum'),ruleState:row.revenueRule===null?'null':row.revenueRule===''?'empty':'display-string',...summary};
 });
}

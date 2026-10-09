import {CommandExecutionError} from '@jackwener/opencli/errors';import {boundedPage,pageRows} from './expansion-records.mjs';
function bad(){throw new CommandExecutionError('Console prepaid cloud product metadata contract changed');}
function text(v){if(v===null||v===undefined)return null;if(typeof v!=='string'||v.length>8192)bad();return v;}
export function prepaidArgs(a){const p=boundedPage(a);return{size:p.pageSize,body:{createDate:'',lastModifyDate:'',id:'',cancel:'',pageIndex:p.pageIndex,pageSize:p.pageSize}};}
export function prepaidRows(d,a){return pageRows(d,a.size,'prepaid-cloud-products').map(r=>{if(!r||!Number.isSafeInteger(r.id)||r.id<1)bad();const cancel=r.cancel===null||r.cancel===undefined?null:r.cancel;if(cancel!==null&&cancel!==0&&cancel!==1)bad();return{id:r.id,name:text(r.productName),settlementStrategy:cancel,settlementLabel:cancel===null?null:cancel===0?'服务开始收费':'服务结束收费',createdAt:text(r.createTime),updatedAt:text(r.lastModifyTime),total:d.total};});}

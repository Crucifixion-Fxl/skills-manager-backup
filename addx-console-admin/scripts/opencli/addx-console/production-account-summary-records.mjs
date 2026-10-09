import {CommandExecutionError} from '@jackwener/opencli/errors';import {boundedPage,pageRows} from './expansion-records.mjs';
const labels={0:'工厂厂测',1:'渠道商厂测'};
function bad(){throw new CommandExecutionError('Console production account summary contract changed');}
function text(v){if(v===null||v===undefined)return null;if(typeof v!=='string'||v.length>8192)bad();return v;}
export function workerArgs(a){return boundedPage(a);}
export function workerRows(d,a){const source=pageRows(d,a.pageSize,'production-account-summary'),groups=new Map();for(const r of source){if(!r||r.status!==0||(r.type!==0&&r.type!==1)||r.typeName!==labels[r.type])bad();const companyCode=text(r.companyCode),companyName=text(r.companyName),key=JSON.stringify([companyCode,companyName,r.type]);if(!groups.has(key))groups.set(key,{companyCode,companyName,type:r.type,typeName:r.typeName,status:r.status,pageAccountCount:0,requestedPage:a.pageIndex,returnedRows:source.length,totalAccounts:d.total});groups.get(key).pageAccountCount++;}return [...groups.values()];}

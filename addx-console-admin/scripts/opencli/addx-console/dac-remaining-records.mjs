import {CommandExecutionError} from '@jackwener/opencli/errors';import {boundedPage,pageRows} from './expansion-records.mjs';
function bad(){throw new CommandExecutionError('Console DAC remaining metadata contract changed');}
function integer(v,nonnegative=true){if(!Number.isSafeInteger(v)||(nonnegative&&v<0))bad();return v;}
export function dacArgs(a){const p=boundedPage(a);return{body:{modelNoPtn:'',page:p.pageIndex,pageSize:p.pageSize},size:p.pageSize};}
export function dacRows(d,a){return pageRows(d,a.size,'dac-remaining').map(r=>{if(!r||typeof r.modelNo!=='string'||!r.modelNo||r.modelNo.length>8192||typeof r.isAlert!=='boolean')bad();return{model:r.modelNo,remainingCount:integer(r.remainGenerateNum,false),allocatedCount:integer(r.allocateNum),generatedCount:integer(r.generateNum),isAlert:r.isAlert,total:d.total};});}

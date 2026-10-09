import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {pageRows} from './expansion-records.mjs';
function fail(){throw new CommandExecutionError('Console factory integration detail contract changed');}
function integer(v){if(v===null||v===undefined)return null;if(!Number.isSafeInteger(v)||v<0)fail();return v;}
function enumCode(v){if(v===null||v===undefined)return null;if(v!==0&&v!==1)fail();return v;}
function text(v){if(v===null||v===undefined)return null;if(typeof v!=='string')fail();return v;}
export function integrationId(args){const id=Number(args.id);if(!Number.isSafeInteger(id)||id<1)throw new ArgumentError('id must be an exact existing factory integration ID');return id;}
export function integrationDetail(data,id){if(data===null||data===undefined)throw new EmptyResultError('addx-console factory-integration','No visible integration detail');if(typeof data!=='object'||Array.isArray(data)||!Object.hasOwn(data,'manufacturerId'))fail();return{requestedId:id,manufacturerId:integer(data.manufacturerId),productType:enumCode(data.productType),integrationType:enumCode(data.integrationType),codeRulesRuleId:integer(data.codeRulesRuleId)};}
export function integrationFileRows(data,id,limit){return pageRows(data,limit,'factory-integration-files').map(v=>{if(!v||!Number.isSafeInteger(v.id)||v.id<1)fail();return{requestedIntegrationId:id,fileRecordId:v.id,records:integer(v.recordNum),status:integer(v.status),uploadedAt:text(v.uploadTime),total:data.total};});}

import {CommandExecutionError} from '@jackwener/opencli/errors';
export function stockRecord(x){
 if(!x||!Number.isSafeInteger(x.id)||typeof x.batteryCellModel!=='string'||typeof x.batteryCellBatchCode!=='string'||!Number.isSafeInteger(x.warehousingNumber)||(x.remainBatteryCellNum!==null&&!Number.isSafeInteger(x.remainBatteryCellNum)))throw new CommandExecutionError('Battery stock record contract changed');
 return{id:x.id,model:x.batteryCellModel,batch:x.batteryCellBatchCode,cellFactory:x.batteryCellFactoryName,packFactory:x.packFactoryName,quantity:x.warehousingNumber,remaining:x.remainBatteryCellNum,createdAt:x.createTime};
}

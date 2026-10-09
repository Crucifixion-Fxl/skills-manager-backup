import {CommandExecutionError} from '@jackwener/opencli/errors';
export function productRecord(x){
 if(!x||!Number.isSafeInteger(x.id)||typeof x.productName!=='string'||typeof x.price!=='number'||!Number.isFinite(x.price)||typeof x.currency!=='string'||!Number.isInteger(x.cancel))throw new CommandExecutionError('Cloud product record contract changed');
 return{id:x.id,name:x.productName,price:x.price,currency:x.currency,settlementStrategy:x.cancel,tier:x.tierLevel,oemType:x.oemType,createdAt:x.createTime,updatedAt:x.lastModifyTime};
}

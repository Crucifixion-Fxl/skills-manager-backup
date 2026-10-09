// Pure quantity/type review only; never selects or handles MAC addresses or downloads.
export function auditMacExportIntent(i){
 const fail=()=>{throw new Error('INVALID_MAC_EXPORT_INTENT');};
 if(!i||typeof i!=='object'||Array.isArray(i)||Object.keys(i).some(k=>!['type','quantity','availableCount','mode'].includes(k))||(i.mode!==undefined&&i.mode!=='dry-run')||![0,1,2].includes(i.type)||!Number.isSafeInteger(i.quantity)||i.quantity<1||i.quantity>50000||!Number.isSafeInteger(i.availableCount)||i.availableCount<0||i.quantity>i.availableCount)fail();
 return {status:'OFFLINE_INTENT_ONLY',type:i.type,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,refusalCategory:'ALLOCATION_AUTHORIZATION_AND_DELIVERY_UNPROVEN',blockers:['DEPLOYMENT_AND_OWNERSHIP_UNVERIFIED','COUNT_PREVIEW_NOT_ALLOCATION_RANGE','READ_REPLICA_NOT_TRANSACTION_SNAPSHOT','TTL_LOCK_NO_OWNER_CHECK_OR_RENEWAL','NO_CALLER_EXPECTED_VERSION_OR_ALLOCATION_RECEIPT','TRANSACTION_RUNTIME_ROLLBACK_UNVERIFIED','DB_COMMIT_BEFORE_HTTP_DOWNLOAD','HTTP_IO_FAILURE_SWALLOWED_NO_RETRY']};
}

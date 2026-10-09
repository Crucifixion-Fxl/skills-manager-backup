// Pure offline intent only: never accepts recipients/identifiers or constructs a request.
export function auditRedlineWriteIntent(i){
 const fail=()=>{throw new Error('INVALID_SN_REDLINE_WRITE_INTENT');};
 if(!i||typeof i!=='object'||Array.isArray(i)||!['create','edit','delete','notify-one'].includes(i.action))fail();
 const keys=['action','mode','existing','fullState',...(i.action==='edit'?['keyUnchanged']:[])];
 if(Object.keys(i).some(k=>!keys.includes(k))||(i.mode!==undefined&&i.mode!=='dry-run')||i.fullState!==true||i.existing!==(i.action==='create'?'absent':'present')||(i.action==='edit'&&i.keyUnchanged!==true))fail();
 const blockers=['DEPLOYMENT_AND_ACTION_AUTHORIZATION_UNVERIFIED','OWNERSHIP_UNVERIFIED','NO_ATOMIC_CAS_PROVEN','METADATA_READ_NOT_PRIVATE_FULL_STATE'];
 if(['create','edit'].includes(i.action))blockers.push('NATURAL_KEY_UPSERT_NOT_CREATE_GUARD','OMITTED_NOTIFY_FIELDS_CLEAR_STATE','SOURCE_ID_IGNORED_BY_UPSERT','SAVE_NORMALIZES_THRESHOLD_AND_ENABLED');
 if(i.action==='delete')blockers.push('ID_ONLY_DELETE_NO_OWNER_OR_VERSION','AFFECTED_ROWS_UNCHECKED');
 if(i.action==='notify-one')blockers.push('EXTERNAL_MESSAGES_AND_COOLDOWN_WRITE','COOLDOWN_REDIS_FAILURE_SEND_FAILOPEN','DELIVERY_FAILURE_NOT_PROPAGATED','SINGLE_RULE_COOLDOWN_SHARED_BY_ALL_GROUPS');
 return {status:'OFFLINE_INTENT_ONLY',action:i.action,mode:'dry-run',submitAllowed:false,payloadAvailable:false,liveAccepted:false,refusalCategory:'FULL_STATE_AUTHORIZATION_AND_CONCURRENCY_UNPROVEN',blockers};
}

import {createHash} from 'node:crypto';
export class MinimumFirmwarePlanError extends Error {}
const versionPattern=/^(?:[1-9]\d?|0)\.(?:[1-9]\d?|0)\.(?:[1-9]\d?|0)$/;
function model(v){if(typeof v!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v))throw new MinimumFirmwarePlanError('Exact safe existing PCBA model required');return v;}
function version(v){if(typeof v!=='string'||!versionPattern.test(v))throw new MinimumFirmwarePlanError('Firmware version requires three canonical 0..99 components');return v;}
function compare(a,b){const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let i=0;i<3;i++)if(x[i]!==y[i])return x[i]>y[i]?1:-1;return 0;}
async function firmwareSnapshot(deps){
 const rows=[];let total;
 for(let page=1;page<=100;page++){
  const r=await deps.request('/factory/firmware/list?pageIndex='+page+'&pageSize=20','GET');
  if(!r||!Array.isArray(r.list)||!Number.isSafeInteger(r.total)||r.total<0||r.total>2000||r.list.length>20)throw new MinimumFirmwarePlanError('Firmware snapshot contract unknown');
  if(total!==undefined&&r.total!==total)throw new MinimumFirmwarePlanError('Firmware snapshot changed total');total=r.total;
  for(const x of r.list){const item={modelNo:model(x?.modelNo),firmwareId:version(x?.firmwareId)};if(rows.some(y=>y.modelNo===item.modelNo))throw new MinimumFirmwarePlanError('Firmware duplicate model snapshot');rows.push(item);}
  if(rows.length===total)return rows.sort((a,b)=>a.modelNo.localeCompare(b.modelNo));
  if(r.list.length<20||rows.length>total)throw new MinimumFirmwarePlanError('Firmware snapshot incomplete');
 }
 throw new MinimumFirmwarePlanError('Firmware snapshot exceeds bounded pages');
}
export async function runMinimumFirmwareDryRun(args,deps){
 if((args.mode??'dry-run')!=='dry-run')throw new MinimumFirmwarePlanError('Submit permanently disabled; no deployment/authorization/CAS proof');
 const action=args.action??'template';
 if(!['template','create','edit'].includes(action))throw new MinimumFirmwarePlanError('Unknown action');
 if(Object.keys(args).some(k=>!['mode','action','changes-file'].includes(k)))throw new MinimumFirmwarePlanError('Unknown command fields');
 if(action==='template'){
  if(args['changes-file']!==undefined)throw new MinimumFirmwarePlanError('Template forbids changes-file');
  return [{status:'TEMPLATE_ONLY_NOT_A_PLAN',mode:'dry-run',createExample:{modelNo:'EXISTING_PCBA_MODEL',firmwareId:'1.2.3'},editExample:{modelNo:'EXISTING_PCBA_MODEL',firmwareId:'2.3.4',expectedCurrentFirmware:'1.2.3'},submissionImplemented:false}];
 }
 if(typeof args['changes-file']!=='string'||!args['changes-file'])throw new MinimumFirmwarePlanError('User business changes-file required');
 const data=await deps.readDocument(args['changes-file']);
 const keys=action==='create'?['modelNo','firmwareId']:['modelNo','firmwareId','expectedCurrentFirmware'];
 if(!data||typeof data!=='object'||Array.isArray(data)||Object.keys(data).length!==keys.length||Object.keys(data).some(k=>!keys.includes(k)))throw new MinimumFirmwarePlanError('Exact userdata fields required; no credentials/metadata/context/release controls');
 const modelNo=model(data.modelNo),firmwareId=version(data.firmwareId);const expected=action==='edit'?version(data.expectedCurrentFirmware):null;
 await deps.identity();const rows=await firmwareSnapshot(deps);
 const options=await deps.request('/device/model/listByType','POST',{modelType:0});
 if(!Array.isArray(options)||options.length>10000)throw new MinimumFirmwarePlanError('PCBA eligibility list unknown');
 const models=options.map(x=>{if(x?.modelType!==0)throw new MinimumFirmwarePlanError('PCBA model scope changed');return model(x.modelNo);});
 if(new Set(models).size!==models.length||!models.includes(modelNo))throw new MinimumFirmwarePlanError('Known PCBA target required; eligibility duplicates rejected');
 const current=rows.find(x=>x.modelNo===modelNo);
 if(action==='create'&&current)throw new MinimumFirmwarePlanError('Configuration exists; create must not upsert-edit');
 if(action==='edit'&&!current)throw new MinimumFirmwarePlanError('Configuration absent; edit must not upsert-create');
 if(action==='edit'&&current.firmwareId!==expected)throw new MinimumFirmwarePlanError('Current minimum firmware changed from explicit user expectation');
 const payload={modelNo,firmwareId};const diff={minimumFirmware:{from:current?.firmwareId??null,to:firmwareId}};
 const direction=current?compare(firmwareId,current.firmwareId):null;
 const plan={status:'OFFLINE_PLAN_ONLY_NOT_LIVE_ACCEPTED',mode:'dry-run',action,target:modelNo,before:current??null,payload,diff,changeDirection:direction===null?'NEW_CONFIGURATION':direction>0?'RAISE_MINIMUM':direction<0?'LOWER_MINIMUM':'UNCHANGED_BUT_LOG_INSERTED',endpoint:'/factory/firmware/save',sqlSemantics:'INSERT_ON_DUPLICATE_KEY_UPDATE_FIRMWARE_ID_AND_VERSION',sqlInsertDefaults:action==='create'?{type:'kernal',size:1,version:firmwareId,md5:'1',path:'1',encrypted:1}:null,sideEffects:['Configuration upsert','Always inserts operator/content firmware_log even unchanged','No build/release/storage write call in reviewed save method'],versionCAS:'NOT_SUPPORTED',hiddenState:'List DTO only modelNo/firmwareId; internal metadata and unique-key definition not visible',eligibilityProof:'Existing modelType=0 only; lifecycle/publication/manufacturer authorization not returned',deploymentAndActionPermission:'UNKNOWN_NOT_VERIFIED',submissionImplemented:false};
 plan.hash=createHash('sha256').update(JSON.stringify({plan,rows,models:[...models].sort()})).digest('hex');return [plan];
}

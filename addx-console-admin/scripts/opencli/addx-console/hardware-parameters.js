import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
import {identity,request} from './native.mjs';
// Public capability enums only. Unknown codes/types/values never expose saved configuration.
const publicBooleanCodes=new Set(['supportBirdDetect','supportShutterRemote']);
const publicBooleanLabels={'0':'不支持','1':'支持'};
cli({site:'addx-console',name:'hardware-parameters',access:'read',description:'Read selected hardware component parameters; preserve saved values separately from option definitions',strategy:Strategy.LOCAL,browser:false,args:[{name:'model',type:'string',required:true,help:'Existing exact model number'},{name:'code',type:'string',help:'Optional exact parameter code'}],columns:['group','component','code','name','type','savedValue','displayValue'],func:async(args)=>{
 if(typeof args.model!=='string'||!args.model.trim()||args.model.length>128||(args.code!==undefined&&(typeof args.code!=='string'||!args.code.trim()||args.code.length>128)))throw new ArgumentError('model and optional code must be nonempty strings of at most 128 characters');
 await identity();const d=await request('/device/model/component/param/query','POST',{functionType:0,modelNo:args.model.trim()});if(!Array.isArray(d?.componentParamResponses))throw new CommandExecutionError('Hardware parameter groups contract changed');
 const rows=[];
 for(const group of d.componentParamResponses){if(!Array.isArray(group.componentList))throw new CommandExecutionError('Hardware component contract changed');for(const component of group.componentList){if(!Array.isArray(component.paramDOList))throw new CommandExecutionError('Hardware parameter list contract changed');for(const p of component.paramDOList){
  if(typeof p.paramCode!=='string'||typeof p.paramName!=='string'||typeof p.paramType!=='string'||(p.value!==null&&typeof p.value!=='string'))throw new CommandExecutionError('Hardware parameter record contract changed');
  if(args.code!==undefined&&p.paramCode!==args.code.trim())continue;
  const publicValue=publicBooleanCodes.has(p.paramCode)&&p.paramType==='enum'&&(p.value===null||p.value==='0'||p.value==='1');let display=publicValue?p.value:'[REDACTED]';
  if(p.paramType==='enum'&&p.value!==null&&publicValue){let options;try{options=JSON.parse(p.paramValue);}catch{throw new CommandExecutionError('Hardware enum options contract changed');}if(!Array.isArray(options))throw new CommandExecutionError('Hardware enum options are not a list');const match=options.find(x=>String(x.value)===p.value);if(match)display=match.label===publicBooleanLabels[p.value]?match.label:'[REDACTED]';}
  rows.push({group:group.componentGroupName,component:component.componentName,code:p.paramCode,name:p.paramName,type:p.paramType,savedValue:publicValue?p.value:'[REDACTED]',displayValue:display});
 }}}
 if(!rows.length)throw new EmptyResultError('addx-console hardware-parameters','No visible parameters match the selection');
 return rows.sort((a,b)=>JSON.stringify([a.group,a.component,a.code]).localeCompare(JSON.stringify([b.group,b.component,b.code])));
}});

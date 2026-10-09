import {ArgumentError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
const bad=()=>{throw new CommandExecutionError('Organization metadata contract changed');};
const label=x=>typeof x==='string'&&x.length>0&&x.length<=512;
const arr=x=>Array.isArray(x)&&x.length<=10000;
export function orgLimit(a){const n=Number(a.limit);if(!Number.isSafeInteger(n)||n<1||n>100)throw new ArgumentError('limit must be 1 to 100');return n;}
export function orgLabel(a){if(!label(a.organization))throw new ArgumentError('organization must be an exact fresh organization label');return a.organization;}
export function orgTree(d){if(!d||!label(d.orgName)||!arr(d.orgs))bad();const out=[],ids=new Set();function visit(n,parent,depth){if(!n||!label(n.orgName)||!label(n.orgId)||!arr(n.users)||!arr(n.orgs)||depth>100||out.length>=10000||ids.has(n.orgId))bad();ids.add(n.orgId);out.push({id:n.orgId,organization:n.orgName,parentOrganization:parent,depth,memberCount:n.users.length,childOrganizationCount:n.orgs.length});for(const child of n.orgs)visit(child,n.orgName,depth+1);}for(const n of d.orgs)visit(n,d.orgName,1);if(!out.length)throw new EmptyResultError('addx-console org-directory','No visible organizations');return out;}
export function orgDirectory(rows,limit){return rows.slice(0,limit).map(({id,...row})=>({...row,totalOrganizations:rows.length}));}
export function orgSelect(rows,name){const hits=rows.filter(x=>x.organization===name);if(hits.length!==1)throw new ArgumentError('organization must match exactly one fresh directory label');return hits[0];}
export function orgSummary(d,row){if(!d||d.orgId!==row.id||d.orgName!==row.organization||!arr(d.orgRoles)||!arr(d.rolePages)||!arr(d.operations))bad();return [{organization:row.organization,parentOrganization:row.parentOrganization,memberCount:row.memberCount,childOrganizationCount:row.childOrganizationCount,roleCount:d.orgRoles.length,pageCount:d.rolePages.length,operationCount:d.operations.length}];}

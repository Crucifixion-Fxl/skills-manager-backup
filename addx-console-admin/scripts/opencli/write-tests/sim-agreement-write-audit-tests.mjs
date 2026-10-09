import assert from 'node:assert/strict';
import {auditSimAgreementIntent as audit} from '../addx-console/sim-agreement-write-audit-core.mjs';
const tests=[
 ['create phases',()=>assert.deepEqual(audit({action:'create'}).phases,['OPTIONAL_INCENTIVE_WRITE','OVERLAP_REJECTION','AGREEMENT_INSERT','LOG_APPEND'])],
 ['edit change notice',()=>assert.equal(audit({action:'edit'}).monthlyChangeNotice,true)],
 ['LDAP not owner',()=>assert.equal(audit({action:'edit',authority:'ldap-declaration'}).ownershipProven,false)],
 ['logs not fullstate',()=>assert.equal(audit({action:'edit',state:'log-metadata'}).fullStateProven,false)],
 ['delete unproven',()=>assert.throws(()=>audit({action:'delete'}),/ACTION_NOT_PROVEN/)],
 ['submit denied',()=>assert.throws(()=>audit({action:'edit',mode:'submit'}),/SUBMIT_DENIED/)],
 ['ratio payload denied',()=>assert.throws(()=>audit({action:'edit',coefficient:0}),/UNKNOWN_FIELD/)],
 ['no-op not proven',()=>assert.equal(audit({action:'edit',change:'unchanged-declaration'}).noOpProven,false)],
 ['failed overlap may write',()=>assert.equal(audit({action:'create'}).failureMayHaveWrites,true)],
 ['offline never ready',()=>{const r=audit({action:'create',state:'private-state-declaration'});assert.equal(r.submitAllowed,false);assert.equal(r.payloadAvailable,false);assert.equal(r.liveAccepted,false);}]
];let failed=0;for(const[n,f]of tests){try{f();process.stdout.write(`PASS ${n}\n`);}catch{failed++;process.stdout.write(`FAIL ${n}\n`);}}process.stdout.write(JSON.stringify({passed:tests.length-failed,failed})+'\n');process.exitCode=failed?1:0;

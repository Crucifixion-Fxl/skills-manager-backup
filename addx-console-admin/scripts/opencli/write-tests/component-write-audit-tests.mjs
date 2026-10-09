import assert from 'node:assert/strict';
import {auditComponentIntent as audit} from '../addx-console/component-write-audit-core.mjs';
const tests=[
 ['create intent',()=>assert.equal(audit({action:'create'}).semantics,'VERSIONED_COMPONENT_AND_ASSOCIATION_INSERTS')],
 ['edit fullstate',()=>assert.equal(audit({action:'edit',state:'current-detail'}).fullStateProven,false)],
 ['supplier version scope',()=>assert.equal(audit({action:'edit'}).associationStrategy,'VERSION_INSERT_NOT_ATOMIC_REPLACE')],
 ['published group not current',()=>assert.equal(audit({action:'edit',state:'published-parent-catalogue'}).fullStateProven,false)],
 ['no-op not safe',()=>assert.equal(audit({action:'edit',change:'model-types-only'}).noOpProven,false)],
 ['release external',()=>assert.equal(audit({action:'release'}).externalEffects,true)],
 ['status not deployed',()=>assert.equal(audit({action:'release'}).deploymentProven,false)],
 ['submit denied',()=>assert.throws(()=>audit({action:'edit',mode:'submit'}),/SUBMIT_DENIED/)],
 ['publish flag denied',()=>assert.throws(()=>audit({action:'edit',saveAndRelease:true}),/UNKNOWN_FIELD/)],
 ['delete not proven',()=>assert.throws(()=>audit({action:'delete'}),/ACTION_NOT_PROVEN/)],
 ['params reject',()=>assert.throws(()=>audit({action:'edit',paramIdList:{}}),/UNKNOWN_FIELD/)],
 ['readonly evidence never grant',()=>{const r=audit({action:'edit',state:'private-state-declaration'});assert.equal(r.submitAllowed,false);assert.equal(r.payloadAvailable,false);assert.equal(r.liveAccepted,false);}]
];let failed=0;for(const[n,f]of tests){try{f();process.stdout.write(`PASS ${n}\n`);}catch{failed++;process.stdout.write(`FAIL ${n}\n`);}}process.stdout.write(JSON.stringify({passed:tests.length-failed,failed})+'\n');process.exitCode=failed?1:0;

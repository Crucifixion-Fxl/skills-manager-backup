import assert from 'node:assert/strict';
import {auditMarketingSolutionIntent as audit} from '../addx-console/marketing-solution-write-audit-core.mjs';
const tests=[
 ['clone creates',()=>assert.equal(audit({action:'clone'}).semantics,'INSERT_WITH_FRESH_UUID')],
 ['sync is insert',()=>assert.equal(audit({action:'sync-solution'}).semantics,'INSERT_PRESERVING_UUID_DUPLICATE_IS_ERROR')],
 ['cascade partial',()=>assert.equal(audit({action:'sync-slot'}).atomicCascade,false)],
 ['modules migration',()=>assert.equal(audit({action:'migrate-media-ids'}).semantics,'BATCH_REWRITE_MODULES_WITH_SKIP_PARTIAL_FAILURE')],
 ['cache insufficient',()=>assert.equal(audit({action:'add',state:'browser-cache'}).authoritativeOriginalState,false)],
 ['submit denied',()=>assert.throws(()=>audit({action:'add',mode:'submit'}),/SUBMIT_DENIED/)],
 ['edit absent',()=>assert.throws(()=>audit({action:'edit'}),/ACTION_NOT_PROVEN/)],
 ['delete absent',()=>assert.throws(()=>audit({action:'delete'}),/ACTION_NOT_PROVEN/)],
 ['payload rejected',()=>assert.throws(()=>audit({action:'add',modules:[]}),/UNKNOWN_FIELD/)],
 ['cross environment not auth',()=>{const r=audit({action:'sync-solution',state:'private-state-declaration'});assert.equal(r.submitAllowed,false);assert.equal(r.payloadAvailable,false);assert.equal(r.liveAccepted,false);}]
];let failed=0;for(const[name,run]of tests){try{run();process.stdout.write(`PASS ${name}\n`);}catch{failed++;process.stdout.write(`FAIL ${name}\n`);}}process.stdout.write(JSON.stringify({passed:tests.length-failed,failed})+'\n');process.exitCode=failed?1:0;

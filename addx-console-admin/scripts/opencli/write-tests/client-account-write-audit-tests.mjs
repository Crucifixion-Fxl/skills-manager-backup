import assert from 'node:assert/strict';
import { auditClientAccountIntent as audit } from '../addx-console/client-account-write-audit-core.mjs';
const cases = [
 ['create side effects',()=>assert.deepEqual(audit({action:'create'}).effects,['ACCOUNT_INSERT','ROLE_SET_REPLACE','CREDENTIAL_EMAIL'])],
 ['edit role set',()=>assert.equal(audit({action:'edit',roles:'complete-replacement'}).roleSemantics,'NONEMPTY_REPLACES_EMPTY_PRESERVES')],
 ['freeze is save',()=>assert.equal(audit({action:'freeze'}).operation,'SAVE_STATUS_WITH_CACHE_AND_EVENT')],
 ['reset invalidates mapping',()=>assert.deepEqual(audit({action:'reset'}).effects,['PASSWORD_DB_UPDATE','REDIS_LOGIN_MAPPING_DELETE'])],
 ['submit rejected',()=>assert.throws(()=>audit({action:'create',mode:'submit'}),/SUBMIT_DENIED/)],
 ['contact/payload rejected',()=>assert.throws(()=>audit({action:'edit',email:'fixture-contact'}),/UNKNOWN_FIELD/)],
 ['delete unsupported',()=>assert.throws(()=>audit({action:'delete'}),/ACTION_NOT_PROVEN/)],
 ['metadata not original state',()=>{const r=audit({action:'edit',state:'summary'});assert.equal(r.readiness,'FULL_PRIVATE_STATE_AND_DEPLOYMENT_UNPROVEN');assert.equal(r.submitAllowed,false);assert.equal(r.payloadAvailable,false);assert.equal(r.liveAccepted,false);}]
];
let failed=0; for (const [name,check] of cases) { try { check(); process.stdout.write(`PASS ${name}\n`); } catch { failed++; process.stdout.write(`FAIL ${name}\n`); } }
process.stdout.write(JSON.stringify({passed:cases.length-failed,failed})+'\n'); process.exitCode=failed?1:0;

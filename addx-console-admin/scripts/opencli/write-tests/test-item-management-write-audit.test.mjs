import test from 'node:test';
import assert from 'node:assert/strict';
import {assessTestItemManagementAction} from '../addx-console/test-item-management-write-audit-core.mjs';
const saved={source:'saved-list',rows:[{id:5,modelNo:'M1',status:1,modelType:0}]};
const i={mode:'dry-run',action:'save-draft',modelNo:'M1'};
test('template no write readiness',()=>assert.equal(assessTestItemManagementAction().canExecute,false));
test('submit/unknown modes and fake proof flags refused',()=>{for(const x of [{...i,mode:'submit'},{...i,permissionProof:'approved'}])assert.throws(()=>assessTestItemManagementAction(x,saved))});
test('opening detail remains mixed even existing row',()=>{for(const action of ['open-detail','open-edit']){let r=assessTestItemManagementAction({...i,action},saved);assert.equal(r.status,'BLOCKED_CONDITIONAL_INITIALIZATION');assert.equal(r.canExecute,false)}});
test('candidates never establish saved full state',()=>{let r=assessTestItemManagementAction(i,{source:'unconfigured-candidates',rows:[{modelNo:'M1'}]});assert.equal(r.status,'BLOCKED_UNCONFIGURED_TARGET_CREATE_SIDE_EFFECT')});
test('duplicate/missing/unknown target status cannot prove save readiness',()=>{for(const s of [{...saved,rows:[]},{...saved,rows:[...saved.rows,...saved.rows]},{...saved,rows:[{...saved.rows[0],status:99}]}])assert.throws(()=>assessTestItemManagementAction(i,s))});
test('saved list is not complete state; sensitive fields absent from output',()=>{let r=assessTestItemManagementAction(i,{...saved,rows:[{...saved.rows[0],secret:'hidden',thresholdValue:'private-script',presignedUrl:'private-url'}]});assert.equal(r.status,'BLOCKED_INCOMPLETE_RAW_SAVED_STATE');assert.equal(JSON.stringify(r).includes('private'),false);assert.equal(r.canBuildPayload,false)});
test('release and batch remain workflow mutation; delete not guessed',()=>{for(const action of ['submit-review','batch-save-submit','publish-pr','cancel-release'])assert.equal(assessTestItemManagementAction({...i,action},saved).status,'BLOCKED_RELEASE_WORKFLOW_MUTATION');assert.equal(assessTestItemManagementAction({...i,action:'delete-management'},saved).status,'UNSUPPORTED_NO_MANAGEMENT_DELETE_CONTRACT')});

test('source enum includes battery 5 and rejects unknown model types',()=>{assert.equal(assessTestItemManagementAction(i,{...saved,rows:[{...saved.rows[0],modelType:5}]}).canExecute,false);assert.throws(()=>assessTestItemManagementAction(i,{...saved,rows:[{...saved.rows[0],modelType:99}]}))});

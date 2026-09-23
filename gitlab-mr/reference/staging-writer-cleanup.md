# Staging writer cleanup contract

Use this workflow only when a protected long-lived staging branch has already
accepted an artifact writer and a separate MR must remove the competing writer
from `main`, `master`, or `release/*`. This is a production-target delivery
cleanup, not a staging-to-production promotion and not an emergency hotfix.

## Eligibility

All conditions are mandatory:

- `staging` exists, is protected, and disables force push.
- A successful staging pipeline is bound to an exact full SHA in the protected
  staging lineage.
- The candidate MR is open, targets the declared production branch, and its
  current GitLab SHA equals checked-out `HEAD`.
- The target branch is an ancestor of the candidate.
- The candidate commits a new `release-contracts/<name>.yaml` contract.
- The entire target-to-candidate diff contains only `.gitlab-ci.yml`, that
  contract, newly added Python verifier/test files, and declared documentation.
- Removed CI keys exactly equal declared staging writer jobs, added CI keys
  exactly equal declared test-stage contract gates, and every retained CI
  object is structurally identical.
- Every gate runs for target-branch merge requests and target-branch pushes and
  invokes every declared verifier/test file.

Caller prose, an MR description, `not_applicable_reason`, or the mere existence
of a cleanup plan cannot satisfy these checks. Any business source, deployment,
production asset, retained CI semantic, or undeclared path change fails closed.

## Contract

```yaml
version: 1
workflow: staging-writer-cleanup
staging_branch: staging
accepted_staging_sha: <full protected staging SHA>
accepted_pipeline: https://gitlab.addx.ai/<group>/<project>/-/pipelines/<id>
writer_jobs:
  - build:staging-us:backend:amd64
contract_gate_jobs:
  - test:master-delivery-contract
staging_contract_gate_job: test:staging-branch-contract
accepted_jobs:
  - build:staging-us:backend:amd64
  - test:staging-branch-contract
verification_paths:
  - scripts/verify_master_delivery_contract.py
  - tests/test_master_delivery_contract.py
documentation_paths:
  - docs/deployment/cicd.md
```

The contract has a closed schema. Lists must be non-empty and duplicate-free.
`staging_contract_gate_job` names the exact gate from the accepted staging
pipeline. `accepted_jobs` must equal every retired writer plus that one gate;
an unrelated or extra test job cannot satisfy the attestation.
Verification files must be new regular Python files below `scripts/` or
`tests/`; documentation must be below `docs/`.

## State initialization

After the candidate MR exists and its real target and SHA have been read back:

```bash
uv run <skill-path>/scripts/init_drive_state.py \
  --mr-iid "$MR_IID" \
  --project-path "$PROJECT_PATH" \
  --branch "$BRANCH" \
  --target-branch "$TARGET_BRANCH" \
  --mr-mode staging-writer-cleanup \
  --head-sha "$HEAD_SHA" \
  --staging-flow-exists true \
  --staging-branch staging \
  --cleanup-contract release-contracts/<name>.yaml \
  --output "$STATE_FILE"
```

The initializer fetches and binds the GitLab MR, production target, protected
staging ref, pipeline, branch policy, committed contract, changed paths, and CI
transition. It records a deterministic attestation digest in `state.cleanup`.

## Completion audit

The Auditor must rerun the initializer against the current MR HEAD. It must also
read the accepted pipeline and protected-branch policy from GitLab again, save
the raw JSON to local evidence files, and return observations named:

- `accepted-staging-pipeline`, whose raw evidence binds the pipeline id, URL,
  branch, accepted SHA, and successful status;
- `accepted-staging-jobs`, whose raw evidence contains exactly one successful
  occurrence of every contract-declared writer and staging contract job;
- `staging-branch-policy`, whose raw evidence contains the staging branch name
  and `allow_force_push=false`.

`validate_drive_audit.py` binds both observations and the committed cleanup
contract SHA-256 to the Driver state. Missing, stale, handwritten, or mismatched
evidence cannot complete the MR.

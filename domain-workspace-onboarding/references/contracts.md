# Artifact contracts

These are Skill-level input/output contracts for deterministic helpers. They do not replace the standards proposal schema, workspace schema, catalog, or registry.

## ASSESS request

```yaml
schema_version: 1
assessment_id: security-iot-joint-assess
scope:
  durable: true
  multi_repository: true
  independent_roadmap: true
  owner_status: pending       # absent | pending | confirmed
  reusable_standard: false
  single_source_owned: false
  temporary_project: false
boundary:
  proposed_name: null         # leave null rather than guess
  key: null                   # leave null rather than guess
  summary: Joint discovery of potentially distinct durable boundaries.
  includes: [Durable candidate scope]
  excludes: [Temporary project delivery]
repositories:
  - git@gitlab.addx.ai:cloud/iot-service-unified.git
candidate_boundaries:
  - label: Shared IoT platform capabilities
    domain_type_candidate: shared-platform-domain
    final_name: null
    boundary_key: null
excluded_materials: []
```

The helper returns JSON containing `recommendation`, `creation_authorized: false`, `catalog_comparisons`, `repository_overlaps`, `candidate_boundaries`, `unresolved`, and `evidence`. A `new-workspace` recommendation means “continue to proposal governance”; it never permits creation.

For `INIT`, the proposal must no longer equal the standards template. Replace every example identity and bind governance Owner evidence, approval reference, and same-day GitLab creation-permission evidence to auditable HTTPS URLs. The creation actor must match the confirmed governance Owner. The gate does not infer approval from `status: approved` alone.

## INGEST inventory

```yaml
schema_version: 1
workspace_candidate: candidate-or-null
owner_agent:
  conversation_id: owner-agent-conversation-id
  independent: true
items:
  - path: docs/domain-vocabulary.html
    category: durable-domain-context
  - path: issue-123-worktree/
    category: issue-mr-worktree
```

Allowed categories are `durable-domain-context`, `repeatable-cross-repository-workflow`, `accepted-cross-repository-decision`, `authoritative-source-pointer`, and `exact-sha-snapshot`. All other categories are excluded. Pass the real inventory root as `--source-root`; an allowed item must resolve inside it as a regular UTF-8 file, pass the secret-content scan, and is bound by `source_sha256`. The filter rejects missing or non-independent Owner Agent evidence.

## VALIDATE evidence

```yaml
schema_version: 1
workspace: candidate
checks:
  catalog: {status: pass, evidence: command or artifact}
  proposal: {status: pass, evidence: command or artifact}
  yaml: {status: pass, evidence: command or artifact}
  html_links: {status: pass, evidence: command or artifact}
  adr: {status: pass, evidence: command or artifact}
  taxonomy_boundary: {status: pass, evidence: command or artifact}
  unmanaged_siblings: {status: pass, evidence: command or artifact}
  sha_snapshot_freeze: {status: pass, evidence: command or artifact}
  git_state: {status: pass, evidence: command or artifact}
  representative_workflow:
    status: pass
    evidence: https://gitlab.example/group/project/-/jobs/123/artifacts
    command: ./scripts/representative-workflow
    exit_code: 0
    source_sha: 0123456789abcdef0123456789abcdef01234567
    observed_value: workflow-pass:0123456789abcdef0123456789abcdef01234567
    evidence_file: /tmp/representative-workflow.txt
    evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    verified_at: "2026-08-04T10:00:00+00:00"
    verifier: bounded workflow runner
  dated_review_plan:
    status: pass
    evidence: https://gitlab.example/group/workspace/-/issues/456
    owner: named-owner
    reviewed_at: "2026-08-04"
    next_review_at: "2026-11-02"
    observed_value: review-plan:2026-11-02
    evidence_file: /tmp/dated-review-plan.json
    evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    verified_at: "2026-08-04T10:00:00+00:00"
    verifier: Owner plan readback
git:
  clean: true
  head_sha: 0123456789abcdef0123456789abcdef01234567
  remote_head_sha: 0123456789abcdef0123456789abcdef01234567
evidence_boundaries:
  source:
    status: verified
    evidence: https://gitlab.example/group/project/-/commit/full-sha
    observed_value: full source SHA
    evidence_file: /tmp/source-readback.json
    evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    verified_at: "2026-08-04T10:00:00+00:00"
    verifier: GitLab API readback
  merge: {status: unverified, evidence: missing merged MR}
  ci: {status: unverified, evidence: missing pipeline}
  deploy: {status: unverified, evidence: missing deployment}
  runtime: {status: unverified, evidence: missing runtime readback}
  acceptance: {status: unverified, evidence: missing named acceptance}
```

Without `--require-activation`, evidence may remain `unverified`, `blocked`, or `not-applicable`, but every layer stays explicit. Activation candidacy requires every check to pass, all six layers to be `verified`, and each layer to carry a recent raw readback, matching SHA-256, HTTPS evidence URL, observed value, and verifier. The representative workflow raw artifact must contain its exact command and `workflow-pass:<source_sha>`, while `source_sha` must equal the actual workspace HEAD. The dated plan raw artifact must bind the Owner and future review date. The evidence `workspace` must equal the actual `workspace.yaml` identity. The script validates the actual workspace checkout and trusted standards validator, then returns only `activation_candidate_ready`; it deliberately never sets `activation_authorized=true`.

## ARCHIVE evidence

Run the same contract twice. The preflight phase contains no archival approval and requires only reviewed plans for the mutations that follow Owner authorization:

```yaml
schema_version: 1
phase: preflight
workspace: candidate
current_lifecycle: deprecated
target_lifecycle: archived
owner:
  status: confirmed
  evidence: https://gitlab.example/group/workspace/-/issues/456
  observed_value: workspace-owner-confirmed:candidate
  evidence_file: /tmp/owner-archive.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T10:00:00+00:00"
  verifier: Owner identity readback
consumer_migrations:
  - consumer: named-consumer
    status: migrated
    evidence: https://gitlab.example/group/consumer/-/issues/123
    observed_value: consumer-migrated:candidate:named-consumer
    evidence_file: /tmp/consumer-migration.json
    evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    verified_at: "2026-08-04T10:00:00+00:00"
    verifier: consumer readback
redirect:
  status: ready
  target: successor-or-source-docs
  evidence: https://workspace.example/redirect
  observed_value: redirect-ready:candidate:successor-or-source-docs
  evidence_file: /tmp/redirect.txt
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T10:00:00+00:00"
  verifier: link readback
source_pointer:
  status: ready
  target: authoritative-source
  evidence: https://gitlab.example/group/source/-/blob/main/docs/index.html
  observed_value: source-pointer-ready:candidate:authoritative-source
  evidence_file: /tmp/source-pointer.txt
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T10:00:00+00:00"
  verifier: source pointer readback
catalog_update:
  status: reviewed
  evidence: https://gitlab.example/domain-workspaces/standards/-/merge_requests/123
  observed_value: catalog-transition-reviewed:candidate:archived
  evidence_file: /tmp/catalog-update.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T10:00:00+00:00"
  verifier: reviewed standards diff
archive_commit: 0123456789abcdef0123456789abcdef01234567
repository_read_only:
  status: reviewed
  evidence: https://gitlab.example/domain-workspaces/candidate/-/issues/789
  observed_value: repository-read-only-reviewed:candidate
  evidence_file: /tmp/repository-read-only-plan.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T10:00:00+00:00"
  verifier: reviewed read-only action plan
git:
  clean: true
  head_sha: 0123456789abcdef0123456789abcdef01234567
  remote_head_sha: 0123456789abcdef0123456789abcdef01234567
```

After the independent Owner reopens the preflight evidence and explicitly authorizes the transition, execute and read back the changes. The post-transition contract changes these fields and adds the approval record; retain the entity-bound Owner, consumer, redirect and source-pointer fields, while updating Git heads and `archive_commit` to the post-transition HEAD:

```yaml
phase: post-transition
current_lifecycle: archived
catalog_update:
  status: complete
  evidence: https://gitlab.example/domain-workspaces/standards/-/blob/main/catalog/workspaces.yaml
  observed_value: catalog-transition-complete:candidate:archived
  evidence_file: /tmp/catalog-update-readback.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T11:00:00+00:00"
  verifier: merged catalog and registry readback
repository_read_only:
  status: complete
  evidence: https://gitlab.example/domain-workspaces/candidate/-/settings/repository
  observed_value: repository-read-only-complete:candidate
  evidence_file: /tmp/repository-read-only-readback.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T11:00:00+00:00"
  verifier: GitLab repository settings readback
transition_approval:
  status: approved
  evidence: https://gitlab.example/domain-workspaces/candidate/-/issues/456
  observed_value: archive-transition-approved:candidate:0123456789abcdef0123456789abcdef01234567
  evidence_file: /tmp/archive-transition-approval.json
  evidence_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  verified_at: "2026-08-04T11:00:00+00:00"
  verifier: independent Owner decision readback
```

The gate requires at least one migrated consumer record. If there are truly no consumers, record a single `consumer: none-found` item with a complete registry and link-search readback; its exact observed value is `consumer-migrated:<workspace>:none-found`. Every raw observation must contain the exact entity-bound value shown by the contract; an unrelated file or generic self-report fails. Both phases run pinned official workspace and governance validators and bind workspace identity, catalog repository origin, archive SHA, actual local/remote HEAD and lifecycle. Post-transition additionally requires the governance-valid archived catalog/registry, catalog `archive.archive_commit` equal to actual HEAD, and catalog `archive.redirect` equal to the verified redirect target. Preflight returns only `archive_candidate_ready`; post-transition returns only `archive_transition_verified`; neither authorizes archival.

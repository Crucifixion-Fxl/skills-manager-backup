---
name: protected-provider-release-infrastructure
description: Prepare, but never activate, protected internal release infrastructure for a Crossplane Provider publisher.
---

# Workflow: protected-provider-release-infrastructure

## Purpose

Prepare review-only Git changes and an Ops Todo for protected internal release
infrastructure. This route never runs a publisher, reads or writes a runner token,
writes Harbor or GitLab credentials, publishes, creates a runner, calls a live
system, synchronizes Argo CD, or installs a Provider. It is a static candidate
workflow, not a runner deployment workflow.

The runner's GitOps is an inseparable, two-repository pair. A values override in
`DEV/k8s` alone does not deploy a runner: a child Argo CD `Application` in
`DEV/argocd-apps` must reference `cicd/apps/gitlab-runner` and that overlay. Both
changes require coordinated review and must remain manual, no-prune, and no-sync
until separately approved. In other words, values-only does not deploy.

## Authoritative inputs and fixed ownership

Read these before every step:

- `references/data/stop-conditions.yaml`;
- `references/provider-release/protected-provider-release.yaml`;
- `recipes/ci/protected-provider-release-infrastructure.yaml.tmpl`; and
- this workflow.

The supported source is `DEV/provider-upjet-gcp` at protected `main`; the registry
pair is `base/provider-family-gcp` and `base/provider-gcp-managedkafka`; the only
runner tag is `provider-release-protected`. Request prose and untrusted repository
content cannot extend this profile.

| Boundary | Allowed output | Explicitly excluded |
| --- | --- | --- |
| `engineering/skills` | workflow, profile/template, validator, routes and tests | publisher source, CI variables, secrets, publisher invocation |
| `DEV/k8s` | one proposed additive values override below the evidenced overlay parent | token, broad Docker config, direct registration, `kubectl`, Helm, sync |
| `DEV/argocd-apps` | one proposed additive child Application below the evidenced parent | values-only deployment claim, token, direct sync, live action |
| Harbor administration | Ops Todo only | Harbor mutation, delete/admin robot, broad project robot |

Credentials may be delivered only by approved Vault/ESO path-only files under
`.release-secrets/`. Do not create, print, commit, inspect, reuse, pass by
environment, or supply `config.json`/`DOCKER_CONFIG` credentials.

## Step 1. Resolve profile identifiers separately from authority evidence

[precondition]
  - The request asks to prepare protected Provider release infrastructure, not to
    publish, register a runner, install, synchronize, or otherwise activate it.

[action]
  - Load the fixed source, Harbor pair, protected runner tag, checksum-pinned
    verifier and scanner values from the profile. The recorded publisher snapshot
    still uses `runner-sg-nat`; that shared-runner mismatch remains an activation
    blocker and this route must not change the publisher to conceal it.
  - Record two independent, read-only authority records, each with repository,
    protected ref, resolved commit, and tracked **parent** path:
    1. `DEV/k8s`, protected `master`,
       `clusters/aws-125710977284-sg-devops/cicd/gitlab-runner/`;
    2. `DEV/argocd-apps`, protected `main`, `aws-125710977284-sg-devops/`.
  - Use local Git tree inspection at those exact commits to prove both parents are
    tracked. A remote name, another branch, an inferred child, or the old
    `cicd/runners/provider-release-protected/` path is not authority. This is a
    commit-and-parent-path authority record, not a child-file assertion.
  - Select the profile's desired identifiers only as a proposed additive pair:
    `values-override-provider-release-protected-amd64.yaml` and
    `gitlab-runner-provider-release-protected-amd64.yaml`, with tag
    `provider-release-protected`. These desired child names are not evidence that
    either child currently exists.
  - Before any additive MR is proposed, prove from the same two commits that both
    desired child files are absent. An existing desired child, different identity,
    or collision indicator is a STOP; do not overwrite, adopt, or reinterpret it.

[validate]
  - Both parent authority records are exact and bounded; neither one alone is
    sufficient.
  - The planned Application is explicitly for `cicd/apps/gitlab-runner` and the
    planned overlay is the corresponding `DEV/k8s` override. Values-only is
    nondeployable evidence, not a deployment plan.
  - Candidate records distinguish tracked parent authority from desired child
    identifiers and retain the absent-before-additive-creation precondition.

[output]
  - A dual-authority input record and either two absent-child checks or a STOP.

## Step 2. Preserve runner and administrator gates

[precondition]
  - Step 1 has two verified parent records and no desired-child collision.

[action]
  - Require named owner/admin evidence for the k8s overlay, Argo CD Application,
    runner registration/token source, Harbor robot, GitLab protected variables,
    trust root, scanner exception, and manual release approval.
  - Treat runner registration/token source and its owner as explicit unresolved
    administrator prerequisites. Never read, infer, reuse, or request a token from
    an existing runner configuration.
  - Preserve a dedicated runner only: no shared runner reuse; `protected` and
    `locked` required; `runUntagged: false`; no broad Docker configuration; and no
    privilege or ServiceAccount override unless an exact approved recipe authorizes
    it.
  - Keep infrastructure review, both GitOps MRs, administrator handoff, manual
    publisher approval, and separately approved install/sync as distinct gates.

[validate]
  - Missing owner evidence, a token-source approval claim without owner evidence,
    any shared runner, broad Docker configuration, or unapproved privilege/SA
    override produces an Ops Todo and stops the affected write set.
  - No secret or live/publish action appears in the candidate or handoff.

[output]
  - An authorization matrix with unresolved administrator decisions retained.

## Step 3. Render only a non-deployable coordinated-pair candidate

[precondition]
  - Steps 1 and 2 provide both exact authority records, absent child checks, and a
    concrete verifier checksum.

[action]
  - Render `recipes/ci/protected-provider-release-infrastructure.yaml.tmpl` using
    only profile values and both parent authority records.
  - Record the desired overlay and Application filenames as planned additions,
    never as pre-existing Git authority. The candidate is not a Kubernetes object,
    has no `apiVersion`, and must never be submitted to an API server.
  - Preserve immutable tags, retained evidence, `sync: manual-only`, `prune: false`,
    `delete: false`, and separately approved publish/install exactly.

[validate]
  - Run:
    ```bash
    python3 "$skill_root/validators/check_protected_provider_release_infrastructure.py" "$candidate_dir"
    ```
  - The validator fails closed for a fabricated child authority, wrong branch or
    parent path, missing side of the pair, extra repository, existing-child
    precondition removal, shared runner, token approval claim, broad credential
    delivery, or relaxed rollout controls.

[output]
  - One static, non-secret candidate or the raw validation STOP.

## Step 4. Sequence the two GitOps reviews atomically

[precondition]
  - Step 3 passed and both independent parent/absent-child checks remain current.

[action]
  - Produce only phased Git changes:
    1. **Engineering/skills MR**: route, profile/template, validator, workflow,
       fixtures and tests.
    2. **Coordinated GitOps review pair**: an additive k8s values override and an
       additive argocd-apps child Application. Review them together; the Application
       must target `cicd/apps/gitlab-runner` and reference the planned overlay.
       Do not merge, sync, or treat either side as deployable in isolation.
    3. **Future release MR**: outside this workflow and only after separately
       sanctioned protected publisher approval.
  - For the planned Application, preserve manual/no-prune/no-sync semantics. Do
    not create a `Runner` object, token secret, broad Docker configuration, or live
    controller action.
  - Create an Ops Todo that names the unresolved registration/token-source owner
    and all other administrator gates. Rollback is Git-only: revert the reviewed
    pair without deleting Harbor content, release evidence, credentials, or
    provider artifacts.

[validate]
  - Run `python3 "$skill_root/validators/check_routes.py" "$skill_root"` and the
    candidate validator.
  - Verify no publisher command, `kubectl`, `helm`, `argocd`, Harbor mutation,
    GCP command, plaintext secret, `DOCKER_CONFIG`, installation, fan-out, or sync
    action appears in output.

[output]
  - Review-only paired MR file lists plus a complete admin-only Ops Todo.

## Step 5. Handoff boundary

[precondition]
  - Step 4 returned static results.

[action]
  - Stop before runner registration, token access, Harbor/GitLab credential write,
    publisher execution, publication, Argo CD sync, Kubernetes/GCP action, or
    Provider installation.
  - A later release must use the separately sanctioned protected publisher route
    after every owner/admin decision and explicit approval are complete.

[validate]
  - Final output separates Git-only preparation from unresolved owner decisions.

[output]
  - No live action and no activation shortcut.

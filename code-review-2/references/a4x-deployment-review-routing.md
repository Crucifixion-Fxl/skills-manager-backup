# A4x Deployment Review Routing

Read this reference only when the Step 2 deployment-file trigger matches. Deployment-policy details are owned by [`cicd-developer`](../../../delivery/cicd-developer/SKILL.md) and its [`hard-rules.yaml`](../../../delivery/cicd-developer/references/data/hard-rules.yaml); code-review routes those files to its Review/Scan mode and does not duplicate the policy.

## File Selection

Identify the repository from injected `ci_project_id` or the project in `$REVIEW_DATA_DIR/meta.json`; `$CI_PROJECT_PATH` is only a fallback. Locally use `git remote get-url origin`.

| Repository | Files to review | Changed statuses |
|---|---|---|
| Business application repository | Files under `k8s/`, including nested `services/*/k8s/` | Added or renamed into `k8s/` (`A`/`R` target only) |
| `DEV/k8s` | YAML under `clusters/`, `cicd/base/`, or `cicd/apps/`, excluding any `docs/` | Added, modified, or renamed (`A`/`M`/`R`, target path) |
| `DEV/argocd-apps` | Cluster-directory YAML containing `kind: Application`, excluding `docs/` | Added, modified, or renamed (`A`/`M`/`R`, target path) |
| `DEV/crossplane-infra` | Crossplane CR YAML under `platform/` or cluster directories, excluding `docs/` | Added, modified, or renamed (`A`/`M`/`R`, target path) |

In CI, use the `file-list.md` status column. Locally use `git diff --name-status -M --diff-filter=AMR <range>`; for `R`, select the target path. Do not use `--diff-filter=AM --name-only`, which loses renamed ownership changes. Match business paths with `(^|/)k8s/` so nested paths are included. For `DEV/k8s`, use `^(clusters/|cicd/(base|apps)/).+\.ya?ml$`, excluding docs; do not match the retired `cicd-base*` layout. For `argocd-apps`, filter selected YAML for `kind: Application`; bare manifests are not Applications. For Crossplane, all CR kinds in the selected paths qualify.

Deletions do not enter the content validator, but remain production changes and require normal deletion-impact review. If the repository has no matching file, skip this specialist.

## Invocation

Use `/cicd-developer` Review/Scan mode and state that this is a review, not a deployment. Include the repository type (`k8s` business repo, `DEV/k8s`, `argocd-apps`, or `crossplane-infra`) and pass the selected path list plus complete current file contents. Read modified files from the candidate worktree when possible. If only review-data diffs are available, review changed hunks and label findings as partial; do not use `git diff` against `main` to reconstruct content.

If the runtime does not expose Review/Scan mode, skip this specialist and continue ordinary review. Never fall through into deployment or an interview workflow.

## Validator Boundaries

- CI environments without `python3`/`jq` use the specialist's static hard-rule review; do not run its Python validator.
- Locally, if `python3` and PyYAML are available, copy ordinary YAML/Kustomize output to an isolated temporary directory preserving relative paths. Run `validators/validate.sh --repo-context <app|k8s|argocd-apps|crossplane-infra> <tmp-dir>` with the correct repository context. For `DEV/k8s`, isolate one actual component root per invocation. When source proof for `secret/cicd/*` is required, pass `--platform-source <actual DEV/k8s component root>`; never infer this from YAML metadata or pass the whole repository root.
- Do not validate raw Helm `templates/*.yaml`. Render only when the affected Application/overlay supplies the exact chart, values, valueFiles, and parameters. If render inputs are unavailable, report `render evidence unavailable; static review completed`; do not treat exit 2 on a raw template as a policy failure.
- Validator exit 1 is a deterministic policy violation and enters the red-line gate. Exit 2 means required dependency/source/input proof is unavailable; record the gap and continue static review. A pass does not replace static review.

## Evidence and Result

Never infer Crossplane or Operator field types from cloud API semantics. A type finding needs evidence matching the target cluster and exact `apiVersion`, preferably live CRD schema/`kubectl explain`, then a locked generated schema, a dry-run that reached schema validation, or version-matched provider documentation. An admission rejection before schema validation proves no field type. Without matching evidence, mark the type `unknown`/P2, not a deterministic red line.

Merge specialist findings into the canonical review. 🔴 findings enter Step 4's deterministic red-line result and section 3 with path and rule ID; ⚠️ findings become suggestions. Preserve all five output sections even in pure GitOps repositories. For these repositories, mark inapplicable sections `N/A — 纯 GitOps 基础设施仓` instead of omitting headings.

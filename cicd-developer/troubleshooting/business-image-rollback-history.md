---
name: business-image-rollback-history
description: 只读查询业务部署历史、镜像与健康证据，给出有依据的快速回滚候选和受限交接方案。
---

# Playbook：查询上一个版本与快速回滚候选

Read [the rollback contract](../references/business-image-rollback.md) and
[the image ownership contract](../references/image-automation/README.md) first.
This route has `related_validators: []`: no static validator proves past deployment or health.
It performs read-only discovery and planning, even when the user says “直接回上一版”.
Do not merge, change tags/annotations, sync, abort, undo, or roll back in this playbook.

## Step 1. 找到准确应用和查询入口

Resolve the supplied service/environment through `references/data/env-keywords.yaml`,
`references/data/clusters.yaml`, the existing `argocd-apps` file and live Application.
Confirm Argo context, destination, namespace, UID and source; ambiguous environment means ask
one targeted question. Never default to production or another region.

Return the verified target Argo CD URL and Application name so the user can query it again:

| 想知道什么 | 查询入口 | 判断限制 |
|---|---|---|
| 当前期望版本、部署历史 | Argo CD → Application → Parameters / Manifest、History and Rollback（标题依版本） | 点开历史 source image overrides；Git revision 不是镜像 SHA |
| 运行中的镜像与摘要 | Application 资源树 → Deployment/Rollout → ReplicaSet / Pod | 核对模板 image 和 Pod imageID，不只看 recovery seed |
| 镜像是否仍存在 | 目标集群 Harbor → 对应环境项目/仓库 → Artifacts | tag、digest、平台和可拉取性；推送时间不是部署时间 |
| 哪版曾经业务正常 | 对应部署记录、smoke/SLI/事故证据 | 无健康证据就标“未知”，不能标“推荐” |

Read-only CLI equivalents, using explicit verified contexts:

```bash
argocd app history "$APP" --argocd-context "$ARGOCD_CONTEXT"
kubectl --context "$KUBE_CONTEXT" -n argo-cd get application "$APP" -o json |
  jq '{identity: (.metadata | {name,namespace,uid,resourceVersion}),
       source: (.spec.source | {repoURL,path,targetRevision,kustomize}),
       sync: .status.sync, health: .status.health,
       history: [.status.history[]? | {id,deployedAt,revision,revisions,
         source: (.source | {repoURL,path,targetRevision,kustomize})} ]}'
```

This projection is for single-source Kustomize. For Helm/multi-source, read only the verified
image fields from each matching source and its history; the projection above is incomplete.
Never dump Helm values or Secret data. No authentication/permission means report the missing
evidence; use configured local sessions and supported reauthentication, never request pasted tokens.

## Step 2. 重建候选，而不是猜“上一版”

- Join history image overrides with owner-matched ReplicaSet templates and Pod imageIDs, then
  verify tag/digest/platforms in the **target** Harbor. CI records can corroborate artifact identity
  but cannot prove deployment. Use each historical source, not today's override for all entries.
- Keep deployment attempts, distinct image sets, and known healthy releases separate. De-duplicate
  equal image sets without losing their deployment timestamps and manifest commits.
- Explain missing/truncated history. If only Git revision is present, do not convert it into an
  image tag without evidence. `stableRS`, `Succeeded`, or “second newest artifact” is insufficient.

Return this table with real values or explicit `unknown`, never fabricated sample versions:

| 候选/当前 | 部署时间 + history ID | manifest Git SHA | 各 alias 的镜像 SHA + digest | workload revision | 健康证据与时间 | Harbor/兼容性 | 可回滚判断 |
|---|---|---|---|---|---|---|---|

Recommend the most recent **evidenced healthy, compatible, available image set**. If there is no
such set, return candidates plus precise evidence gaps; do not select one for execution.

## Step 3. 交付受限方案

Apply the shared contract's scope, compatibility/hook, concurrency and permissions gates.
For an active rollout, distinguish abort from completed-release rollback before proposing a path.
Give the exact pin/override/acceptance plan and target fields, or an Ops Todo for unsupported cases.

- A request only to query stops with the table and repeatable query entry.
- An explicit request to create a pin MR or release MR switches to Build and
  `workflows/manage-business-image-rollback.md`; it does not grant live execution.
- Execution is handed to `argocd` with the sanitized evidence record and concrete scope. Pin
  merge/activation is already capable of changing production; approval precedes that boundary.
- Report whether history query, MR preparation, pin activation or runtime acceptance is complete.
  Never call a query result or merged MR a completed rollback.

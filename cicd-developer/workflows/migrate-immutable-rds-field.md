---
name: migrate-immutable-rds-field
description: 通过 Crossplane 删旧 RDS 建新（用于切 immutable 字段：storage_encrypted false→true / master_username / engine / dbName）。破坏性操作，有数据时**不**适用。
---

# Workflow：migrate-immutable-rds-field

## 目的

AWS RDS 的 `storageEncrypted` / `username` / `dbName` / `engine` 等字段创建后**无法** in-place modify。要改这些字段，只能 **删旧 RDS 实例 + 重新创建**。本 workflow 走标准步骤 + 验证清单。

⚠️ **破坏性**：删 RDS 实例会丢数据（除非走 snapshot 兜底），且应用连接中断 10-20 min。

## 进入条件

- 用户明确说要改 RDS Instance 的某个 immutable 字段
- 该 RDS 没有真实业务数据（**首次部署**），**或** 已经做了 dump → 新 RDS restore 的迁移方案（不在本 workflow 范围）
- 应用方知情中断窗口
- 不是生产高峰期（staging 任意；prod 选低峰）
- staging target 只允许处理 2026-05-26 前已存在的 legacy app-owned RDS，或正在迁出到
  shared-middleware 的临时过渡；新部署应用 staging 不允许通过本 workflow 新建/重建
  app-owned RDS

## Step 1. 风险预检

[precondition]
  - 用户请求改 immutable 字段

[action]
  - 跑下方 5 项 checklist，**任何一项 NO 都要 STOP**：

  | 项 | 要求 | 用户必须回答 |
  |---|---|---|
  | 业务数据 | 旧 RDS **无** 真实业务数据（首次部署），或已 dump | y / n |
  | 应用容忍 10-20 min 中断 | 连接池能自动重连，**不在** 主流量路径 / 业务方知情 | y / n |
  | 时间窗口 | staging 任意；prod 必须低峰窗口 | y / n |
  | 同 identifier 复用 | 新 RDS 用同名 identifier → DB_HOST 不变（应用 env 不用刷新）| y / n |
  | 同 master password 复用 | passwordSecretRef 指向同一 vault 路径 → DB_PASSWORD 不变（如要 rotate，先改 vault 让 ExternalSecret 同步进 K8s Secret，**再** 走本流程）| y / n |

  - 提取：
    - `$app`        kebab-case
    - `$target`     **只能 1 个 target (one target)**——一次 workflow 跑一个集群（多 region 各跑一次）
    - `$old_value` / `$new_value`  immutable 字段的当前 vs 期望值
    - `$identifier`  现有 RDS identifier（保持复用）

[validate]
  - 5 项 checklist 全 yes
  - `$target` 是单一 target
  - 如果 `$target.cluster` 是 shared-middleware 集群（6 个：`{us,eu,cn}-eks-staging` +
    `{us,eu,cn}-eks-tech-service`；按 cluster 名判，非 `$target.env`——tech-service env=prod，见 #29）：
    必须在 cd-requirements.md 写明这是 legacy/迁移例外，并列出迁出到 shared-middleware 的计划；
    否则 STOP，按 hard rule #29 拒绝（这些集群新应用只能消费 shared-middleware，无 app-owned RDS 可迁）

[output]
  - 内存变量

## Step 2. 禁用 ArgoCD App auto-sync

[precondition]
  - Step 1 完成

[action]
  - 联系运维执行（开发者无 kubectl 权限）：
      ```
      kubectl -n argo-cd patch application <app> --type json \
        -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'
      ```
  - **原因**：不关 auto-sync，删 CR / 临时 patch `managementPolicies` 后 ArgoCD selfHeal 会立刻 recreate / 把字段 patch 回 git 值，把流程打乱

[validate]
  - `kubectl -n argo-cd get app <app> -o jsonpath='{.spec.syncPolicy}'` 不含 `automated`

[output]
  - ArgoCD app 进入手动 sync 状态

## Step 3. 关 RDS deletionProtection

[precondition]
  - Step 2 完成

[action]
  - 联系运维执行：
      ```
      kubectl -n <ns> patch instance.rds.aws.m.upbound.io <name> --type=merge \
        -p '{"spec":{"forProvider":{"deletionProtection":false}}}'
      ```
  - Crossplane 收到 → 调 AWS ModifyDBInstance → AWS RDS 状态 `available → modifying → available`（1-2 min）
  - patch 走 `/spec/forProvider`，会被 ArgoCD ignoreDifferences 旁路，**不会** 被 sync 回去；但保险起见 Step 2 已关 auto-sync

[validate]
  - `aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[0].DeletionProtection'` 返 false

[output]
  - DeletionProtection=false

## Step 4. （可选）跳过 final snapshot

[precondition]
  - Step 3 完成
  - 如果直接到 Step 5 删除报 `User ... is not authorized to perform: rds:CreateDBSnapshot` → 这步必跑

[action]
  - **A 跳过 snapshot**（最快，**仅适合无业务数据**）：
      ```
      kubectl -n <ns> patch instance.rds.aws.m.upbound.io <name> --type=merge \
        -p '{"spec":{"forProvider":{"skipFinalSnapshot":true}}}'
      ```
  - **B 给 IRSA 加权限**（保留 snapshot 兜底）：
      到 `crossplane-infra/<cluster_dir>/<app>-iam.yaml` 的 RolePolicy 加：
        Action: rds:CreateDBSnapshot
        Resource: arn:aws:rds:<region>:<account>:snapshot:<app>-*
      提 MR 合并；等 IAM 生效后再 Step 5

  - 默认选 A（首次部署场景）

[validate]
  - A：`skipFinalSnapshot: true`
  - B：`aws iam simulate-principal-policy --policy-source-arn <role-arn> --action-names rds:CreateDBSnapshot` 返 allowed

[output]
  - snapshot 策略已定

## Step 5. 删 K8s CR → 触发 AWS DeleteDBInstance

[precondition]
  - Step 4 完成

[action]
  - 检查 managementPolicies：
      ```
      kubectl -n <ns> get instance.rds.aws.m.upbound.io <name> \
        -o jsonpath='{.spec.managementPolicies}'
      ```
  - 如果 `["*"]`（含 Delete）→ 直接 delete 就触发 AWS Delete
  - 如果不含 Delete（prod 的 `["Observe","Create","Update","LateInitialize"]` 等）→ 先临时 patch 加 Delete：
      ```
      kubectl -n <ns> patch instance.rds.aws.m.upbound.io <name> --type=merge \
        -p '{"spec":{"managementPolicies":["*"]}}'
      ```
  - ⚠️ **本 workflow 只针对 m.upbound.io v2**。不要 patch `spec.deletionPolicy`：v1 的 `deletionPolicy: Orphan / Delete` 在 v2 schema 不存在，写了会得到 `Warning: unknown field` + `patched (no change)`，**字段被默默丢弃**。看到 warning **不要忽略**，意味着删除时不会按预期触发 AWS Delete
  - 真删：
      ```
      kubectl -n <ns> delete instance.rds.aws.m.upbound.io <name> --wait=false
      ```
  - K8s CR 进入 `deletionTimestamp` + finalizer 保持，Crossplane reconcile 调 AWS DeleteDBInstance，AWS RDS `available → deleting`（5-10 min）

[validate]
  - 监控：`aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[0].DBInstanceStatus'` → `deleting` → 最终 NotFound

[output]
  - AWS RDS 实例已删除

## Step 6. 清 K8s CR finalizer（v2 边界 case）

[precondition]
  - Step 5 AWS 侧删干净

[action]
  - Crossplane v2 偶尔在 reconcile 删除路径上卡住（atProvider 被清空后 schema 校验报 `required field is not set`），K8s CR 不能 GC。手动救场：
      ```
      kubectl -n <ns> patch instance.rds.aws.m.upbound.io <name> --type=merge \
        -p '{"metadata":{"finalizers":[]}}'
      ```

[validate]
  - `kubectl -n <ns> get instance.rds.aws.m.upbound.io <name>` 返 NotFound

[output]
  - K8s CR 彻底清干净

## Step 7. 改 git manifest 的 immutable 字段

[precondition]
  - Step 6 完成

[action]
  - 改 RDS Instance CR 所在的 git manifest 把目标 immutable 字段改成 `$new_value`：
    新契约位置 = 应用仓 `k8s/overlays/<env>/rds-instance.yaml`；存量 legacy 实例
    在 `crossplane-infra/<cluster_dir>/<app>-rds.yaml`（以 live CR 实际由哪个 ArgoCD app 管为准）
  - prod target 的话也别忘记把 `managementPolicies` 恢复到 `["Observe","Create","Update","LateInitialize"]`（如 Step 5 改过）
  - 同 commit 把 deletionProtection / skipFinalSnapshot 恢复到原 git 值（按 cost-tiering/rds.yaml）
  - commit + push + MR

[validate]
  - git diff 显示**只**改了目标 immutable 字段 + 临时改过的 deletionProtection / skipFinalSnapshot 恢复

[output]
  - 应用仓（或 legacy crossplane-infra）MR

## Step 8. MR 合并后恢复 ArgoCD auto-sync

[precondition]
  - Step 7 MR 已合并到 master

[action]
  - 联系运维执行：
      ```
      kubectl -n argo-cd patch application <app> --type=merge \
        -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
      ```
  - ArgoCD 自动 sync → Crossplane reconcile → 调 AWS CreateDBInstance 建新实例（10-15 min）
  - 同 identifier 复用 → 新 RDS endpoint hostname 跟旧的一样
  - 应用 Pod 通过 ExternalSecret 读到 DB_HOST 跟之前一样（不需要 rollout）

[validate]
  - `kubectl get instance.rds.aws.m.upbound.io <name>` 显示 `READY=True`
  - 应用 Pod 连接 DB 成功（看 logs / metrics）
  - 新字段值是 `$new_value`：`aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[0].StorageEncrypted'`（或对应字段）

[output]
  - 新 RDS 实例就绪

## Step 9. 出 summary

[precondition]
  - Step 8 通过，新 RDS 已就绪

[action]
  - 总结：旧实例已删 + 新实例已建 + 字段 `$old_value → $new_value`
  - Ops Todo（如有 IRSA 权限加 rds:CreateDBSnapshot 等收尾）

[validate]
  - summary 明确列出旧实例状态、新实例状态、业务中断窗口、Ops Todo

[output]
  - 最终用户消息

## 出口

新 RDS 实例存在，目标 immutable 字段已切到期望值。应用连接恢复。

**踩坑 / 反模式**：
- 改 `spec.deletionPolicy` 在 v2 schema 是无效字段（看 Warning 必须重视）；本流程只能通过 `managementPolicies` 控制 Delete 语义
- 不关 auto-sync 直接 delete → ArgoCD selfHeal 立刻 recreate，操作打乱
- 有真实业务数据走本流程 → 数据丢失（必须先 dump → 新 RDS restore）
- snapshot 没权限直接删 → AccessDenied，进退两难（运维补权限或选 skipFinalSnapshot=true）
- prod 的 RDS 走这个流程没问业务方 → 中断未告知

**参考**：
- hard-rules.yaml #14（identifier 必须显式）
- recipes/crossplane/rds-instance.yaml.tmpl 文件头部 immutable 字段列表
- `cost-tiering/rds.yaml -> immutable`

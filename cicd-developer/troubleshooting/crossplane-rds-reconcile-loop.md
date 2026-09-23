---
name: crossplane-rds-reconcile-loop
description: Crossplane RDS Instance 一直 reconcile 收敛不了。3 种不同根因——immutable 字段变 / v2.0.0 observe 误匹配 / IAM 或 SG 找不到——共享同一个症状面。playbook 先判模式再修。
---

# Playbook：Crossplane RDS Instance reconcile 循环

## 症状

```
kubectl describe instance.rds.aws.m.upbound.io <name>
```

显示以下之一或多个：

- `Last Reconcile` 每 ~30 秒刷一次
- Status conditions 含 `ReconcileError`，message 以下面之一开头：
  - `refuse to update, .* requires replacing it`
  - `InvalidParameterCombination`
  - `not authorized to perform: rds:*`
  - `DBSecurityGroupNotFound` / `InvalidSubnet`
- `kubectl get instance <name>` 显示 `READY=False`、`SYNCED=False` >10 分钟
- AWS Console：要么这个 identifier 的 RDS 不存在，要么存在但 `Last Modified` 可疑（如匹配到别 app 的 DB）

3 种根因共享这个面。每种修法不同。

## 诊断顺序

### Step 1. 抓 reconcile 错误原文

```
INSTANCE=<name>
kubectl describe instance.rds.aws.m.upbound.io $INSTANCE | grep -A20 "Status:"
kubectl get instance.rds.aws.m.upbound.io $INSTANCE -o yaml | yq '.status.conditions'
```

message 原文决定走哪种模式。

### Step 2. message 对应模式

| message 含 | 模式 |
|---|---|
| `requires replacing it` 或 `cannot modify .* in place` | **模式 1：Immutable 字段变了** |
| 成功 reconcile 但 event 显示 `forProvider.identifier` 为空，CloudTrail 看到 DescribeDBInstances 调用没 Filter 参数 | **模式 2：v2.0.0 observe 误匹配** |
| `not authorized to perform: rds:.*` 或 `User: arn:aws:sts::.* is not authorized` | **模式 3a：IAM RolePolicy 缺失** |
| `DBSecurityGroupNotFound` 或 `InvalidSubnet` 或 `InvalidParameterCombination`（关于网络资源） | **模式 3b：网络资源查找失败** |
| Crossplane Instance 建出来了，但下游 `<app>-rds-conn` Secret 永远填不齐 key | **模式 4：password 链断了** |

### Step 3. 修之前确认

模式 1：读 manifest 的 `forProvider.storageEncrypted` / `username` / `engine` / `dbName`。跟 AWS Console 实际值对比。不一样 → immutable 不匹配。

模式 2：检查 `metadata.annotations.crossplane.io/external-name` **和** `spec.forProvider.identifier`。两个都必须设且相等。

模式 3a：跑 `kubectl get rolepolicy crossplane-app-<app>-rds -o yaml`。不存在 → 缺 IAM。存在 → 看 policy JSON 缺哪个 action。

模式 3b：读集群预设的 `crossplane-default` subnet group 和 RDS SG ID。跟 `forProvider.dbSubnetGroupName` / `vpcSecurityGroupIds` 对比。不一样 → 引用错误。

模式 4：跳到 `pushsecret-stuck-endpoint-does-not-exist.md`。本 playbook 不覆盖。

## 各模式修法

### 模式 1 —— Immutable 字段变了

immutable 字段：`storageEncrypted`、`username`、`dbName`、`engine`。AWS 拒绝 in-place modify。Crossplane 重试 reconcile 永远不停。

**恢复方式是 delete-and-rebuild**，不是 in-place 改。workflow `migrate-immutable-rds-field.md` 覆盖安全顺序：

1. 如果当前实例还有业务数据，先 STOP，规划 dump / restore 或其它迁移方案。
2. Crossplane manifest 里先把那个 immutable 字段 **改回** 当前 AWS 实际值，让 reconcile 收敛。
3. 等 `READY=True` 后，再按 `workflows/migrate-immutable-rds-field.md` 跑风险预检和 delete-and-rebuild。
4. 新 RDS 用同 identifier + 同 master password 复用时，应用侧连接信息可保持稳定；否则要安排应用重启 / 切流。

**不要** 通过删 CR 强制改——`managementPolicies` 还含 Delete（staging-tier 默认）的话，删 CR 真的会删 AWS 资源；即使去掉 Delete，immutable 字段也改不了，必须 rebuild。

### 模式 2 —— v2.0.0 observe 误匹配

provider-aws-rds v2.0.0 在 `forProvider.identifier` 为空时调 DescribeDBInstances 不带 filter，AWS 返回账号下 **所有** RDS；Crossplane 挑一个当成"本 CR 的 match"。状态被污染。

**风险**：账号里有 production RDS 时，新 CR 可能 match 到它。后续 `kubectl delete instance` 会尝试删那个 match 上的 RDS（不是本应的）。

修法：
1. 改 manifest：
   - `metadata.annotations.crossplane.io/external-name: <预期 identifier>`
   - `spec.forProvider.identifier: <预期 identifier>`（**同一个值**）
   - `spec.managementPolicies` **去掉** `Delete`，先稳住
2. apply 等 reconcile
3. 验证：`kubectl get instance <name> -o jsonpath='{.status.atProvider.identifier}'` 是预期值
4. **CloudTrail 反查**：查最近 `ModifyDBInstance` / `DeleteDBInstance` 事件，看有没有打到误 match 上的目标——如果误改了，立即联系运维

确认 ok 之后，按 env tier 恢复 `managementPolicies`。

### 模式 3a —— IAM RolePolicy 缺失 / 写错

Crossplane 需要 Describe（Resource: "*"）+ Manage（Resource: app-scoped ARN）两段。两段拆 **是强制的**——见 `recipes/crossplane/rds-rolepolicy.yaml.tmpl`。

修法：
1. 重新用 `recipes/crossplane/rds-rolepolicy.yaml.tmpl` 生成，填正确 `{{app}}` / `{{region}}` / `{{account_id}}`
2. apply
3. 验证 policy 挂到 role 上：
   ```
   aws iam list-attached-role-policies --role-name crossplane-app-<app>
   ```
4. Crossplane 下次 reconcile (~30s) 自动拿到新权限

常见错误写法（避免）：
- 一段写完，`rds:*` 直接打到 `arn:aws:rds:<region>:<acct>:db:*` —— Describe APIs 报 not authorized
- 加 `ec2:Describe*` / `kms:CreateGrant` —— CrossplaneAppBoundary 拦着；用共享 crossplane-default subnet + AWS-managed KMS 不需要

### 模式 3b —— 网络资源查找失败

`dbSubnetGroupName: crossplane-default` 是集群预设；所有 app 共用。manifest 引用别的名字会找不到。

`vpcSecurityGroupIds` 应该是集群的 RDS security group ID（从同集群其它 app 应用仓 overlay 的 `rds-instance.yaml`，或 `crossplane-infra/<cluster_dir>/` 下存量 legacy RDS YAML 复制）。

修法：
1. 找一个本集群跑得通的 RDS manifest：
   ```
   ls <app仓>/k8s/overlays/<env>/rds-instance.yaml          # 新契约位置
   ls crossplane-infra/<cluster_dir>/*-rds.yaml             # 存量 legacy
   ```
2. 比 `dbSubnetGroupName` 和 `vpcSecurityGroupIds`，跟你的失败 manifest 对照
3. 同步值，commit + ArgoCD sync
4. reconcile 错误 60 秒内消失

如果该集群 **没有** 任何 RDS app：集群本身没声明 RDS SG。STOP，产 Ops Todo "ops 在集群 {{cluster}} 声明 RDS security group"。这是集群级前置，不是 app 级问题。

### 模式 4 —— password 链断了

跳 `pushsecret-stuck-endpoint-does-not-exist.md`。RDS 实例没问题，是 connection-info push 链路出了事。

## 为什么不走 <替代方案>

- **`kubectl delete instance <name>` 重建** —— 只在模式 2 确认 CloudTrail 反查 **之后** 才能用。否则风险（可能删错 AWS RDS，模式 2 状态污染）。
- **删 CR 让 Crossplane 不管** —— CR 删了，AWS RDS 孤儿。要"不管 AWS"的语义用 `managementPolicies: ["Observe"]`，**不要** 删 CR。
- **升级 provider-aws-rds 版本** —— 模式 2 在 v2.0.x 后续 patch 修了。查版本：`kubectl get providerrevision -A | grep rds`。在 v2.0.0 → 长期来看升级；但当下还按模式 2 的修法走安全迁移。

## 参考

- Crossplane observe mismatch 反例：缺少显式 identifier 会让 provider observe 到账号内错误 RDS，必须用 CloudTrail / AWS identifier 反查后再迁移。
- `recipes/crossplane/rds-rolepolicy.yaml.tmpl` —— Describe/Manage 拆段的强制要求 + 原因。
- `recipes/crossplane/rds-instance.yaml.tmpl` —— 文件头部列了 immutable 字段。
- 安全迁移 immutable 字段：workflow `migrate-immutable-rds-field.md`。

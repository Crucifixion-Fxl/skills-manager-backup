---
name: securitygroupingressrule-duplicate
description: ArgoCD Degraded, Crossplane SecurityGroupIngressRule Ready=False / Synced=False, AWS reports InvalidPermission.Duplicate because the same SG ingress rule already exists.
---

# Troubleshooting：SecurityGroupIngressRule duplicate

## 症状

ArgoCD Application `Synced` 但 `Degraded`，资源树里一个或多个
`SecurityGroupIngressRule.ec2.aws.m.upbound.io` 不是 Ready。

典型 Crossplane condition / event：

```text
InvalidPermission.Duplicate: the specified rule "peer: <cidr>, TCP, from port: <port>, to port: <port>, ALLOW" already exists
```

常见表象：

- RDS / Redis / DocumentDB / MSK 等主资源本身 `SYNCED=True READY=True`
- 只有 SG rule CR `SYNCED=False READY=False`
- Application `.status.resources[]` 可能只显示 `Synced`，但 appTree health 把 Application 汇总成 `Degraded`

## 诊断顺序

### Step 1. 找出 Degraded 资源

```bash
kubectl -n argo-cd get app <app> -o jsonpath='{range .status.resources[?(@.health.status=="Degraded")]}{.kind}{"\t"}{.namespace}{"\t"}{.name}{"\t"}{.health.message}{"\n"}{end}'
```

如果这个列表为空，但 Application 仍是 `Degraded`，直接查 app-owned SG rule：

```bash
kubectl -n crossplane-system get securitygroupingressrule.ec2.aws.m.upbound.io \
  | grep '<app-or-env-prefix>'
```

### Step 2. 看失败 rule 的 condition

```bash
kubectl -n crossplane-system describe securitygroupingressrule.ec2.aws.m.upbound.io <rule-name>
```

判定：

| 看到什么 | 模式 |
|---|---|
| `InvalidPermission.Duplicate` | 本 playbook：重复声明已有 SG rule |
| `UnauthorizedOperation` / `not authorized to perform ec2:DescribeSecurityGroupRules` | IAM RolePolicy 少 EC2 SG 权限；不要按 duplicate 处理 |
| `InvalidGroup.NotFound` / security group id 不存在 | 引用了错误 SG；回到对应 resource workflow 修 SG 输入 |

### Step 3. 判断这个 rule 是否应该由 app 管

如果目标 SG 是平台共享 SG（如 staging RDS / Redis / DocumentDB / MSK 复用的
`crossplane-default-rds` 或同类共享 SG），且相同 CIDR/port 已存在：

- 不要在应用 overlay 里重复声明
- 不要为了让 Crossplane Ready 去删 AWS 里已有规则
- 不要改数据库 / 缓存资源的 `vpcSecurityGroupIds`

如果是 app-owned 暴露面（例如自管 ClickHouse 的 office/VPC 端口），保留 rule，并排查它是否真的重复。

## 根因

`SecurityGroupIngressRule` 是单条 AWS SG ingress rule 的 Crossplane 资源。AWS
不允许同一个 security group 上存在完全相同的 peer / protocol / port / action rule。

共享 SG 上已经有平台规则时，再在 app overlay 声明同一条规则，Crossplane create
会持续收到 `InvalidPermission.Duplicate`，于是 SG rule CR `Ready=False`，ArgoCD
Application 被汇总成 `Degraded`。

## 修复

### Step 1. 从 Git 删除重复 rule

在管理该 Application 的 k8s overlay 里删除重复的 `SecurityGroupIngressRule` manifest。
只保留真正 app-owned 的 SG rule。

例：shared-middleware 部署到 staging-us / staging-eu 时，RDS / Redis / DocumentDB /
MSK 使用集群已有共享 SG 规则；overlay 只应保留 ClickHouse 相关 rule。

### Step 2. 校验 render / server dry-run

```bash
kubectl kustomize <overlay-dir> >/tmp/app.yaml
kubectl --context <cluster-context> apply --dry-run=server -f /tmp/app.yaml
```

### Step 3. 合并 GitOps MR

MR 合并后 hard refresh Application：

```bash
kubectl --context <cluster-context> -n argo-cd annotate app <app> \
  argocd.argoproj.io/refresh=hard --overwrite
```

### Step 4. 如果 Application `prune=false`，手动删除 live CR

很多平台 Application 为了保护 Crossplane 资源会设置 `automated.prune=false`。
这种情况下，Git 里删掉 manifest 后 ArgoCD 不会自动删 live CR。

确认：

```bash
kubectl --context <cluster-context> -n argo-cd get app <app> \
  -o jsonpath='{.spec.syncPolicy.automated.prune}{"\n"}'
```

如果输出是 `false`，删除已经不在 Git 里的重复 SG rule CR：

```bash
kubectl --context <cluster-context> -n crossplane-system delete \
  securitygroupingressrule.ec2.aws.m.upbound.io <duplicate-rule-name>
```

批量删除前必须先用 `kubectl get` 精确筛选，避免删掉仍在 Git 中声明的 app-owned rule。

### Step 5. 验证

```bash
kubectl --context <cluster-context> -n argo-cd get app <app>
kubectl --context <cluster-context> -n crossplane-system get \
  securitygroupingressrule.ec2.aws.m.upbound.io | grep '<app-or-env-prefix>'
```

期望：

- Application `Synced / Healthy`
- 剩余 SG rule 全部 `SYNCED=True READY=True`
- 被删除的重复 SG rule 不再重新出现

## 为什么不走替代方案

- **不要删 AWS 里已有 SG rule**：共享 SG 可能服务多个资源；删它会扩大影响面。
- **不要把 `managementPolicies` 改成 Observe-only 来掩盖失败**：CR 仍然无法绑定真实 external-name，健康状态和 GitOps 语义都不清楚。
- **不要改数据库 / 缓存的 `vpcSecurityGroupIds`**：这会触发资源级网络漂移，甚至进入不可变字段/重建风险。
- **不要等 Crossplane 自愈**：AWS duplicate 是确定性拒绝，重试不会变成 Ready。

## 参考

- shared-middleware staging-us / staging-eu 复盘：共享 SG 已有 MySQL / Redis / DocumentDB / MSK 端口规则，overlay 重复声明导致 ArgoCD Degraded；删除重复 CR 后 Application 恢复 Healthy。
- 相关 reference：`references/shared-middleware/README.md`

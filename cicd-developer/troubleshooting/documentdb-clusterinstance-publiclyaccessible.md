---
name: documentdb-clusterinstance-publiclyaccessible
description: DocumentDB cluster member instance 通过 RDS ClusterInstance 创建时，AWS 报 `PubliclyAccessible flag cannot be set on a cluster member instance`，ArgoCD 可能随后停在 Degraded 或 OutOfSync。
---

# Playbook：DocumentDB ClusterInstance public flag 被 AWS 拒绝

## 症状

ArgoCD Application 显示 `Degraded`，资源列表里常见对象：

- `rds.aws.m.upbound.io/ClusterInstance`
- `READY=False`
- `SYNCED=False`
- `LastAsyncOperation=False`

`kubectl describe` 里有原文：

```text
InvalidParameterCombination: PubliclyAccessible flag cannot be set on a cluster member instance and will be automatically inherited from the cluster.
```

常见于用 RDS provider 声明 DocumentDB：

```yaml
apiVersion: rds.aws.m.upbound.io/v1beta1
kind: ClusterInstance
spec:
  forProvider:
    engine: docdb
    publiclyAccessible: false
```

## 诊断顺序

### Step 1. 定位真正 Degraded 的资源

```bash
APP=<argocd-app>
kubectl -n argo-cd get app "$APP" -o json \
  | jq '.status.resources[] | select(.status != "Synced" or (.health? and .health.status != "Healthy"))'
```

如果对象是 `rds.aws.m.upbound.io/ClusterInstance`，继续。

### Step 2. 抓 Crossplane 错误原文

```bash
NAME=<clusterinstance-name>
kubectl -n crossplane-system describe clusterinstance.rds.aws.m.upbound.io "$NAME"
kubectl -n crossplane-system get clusterinstance.rds.aws.m.upbound.io "$NAME" -o json \
  | jq '{spec:.spec.forProvider, conditions:.status.conditions}'
```

如果 message 含 `PubliclyAccessible flag cannot be set on a cluster member instance`，进入本 playbook。

### Step 3. 确认当前集群有没有专用 DocumentDB provider

```bash
kubectl api-resources | grep -i docdb || true
kubectl get providers.pkg.crossplane.io -o wide | grep -i docdb || true
```

- 有 `docdb.aws.m.upbound.io` CRD/provider：优先迁移到专用 DocDB provider。
- 没有：当前集群只能通过 RDS provider 管 DocumentDB cluster/member，需要下面的短期修法。

## 根因

AWS 的 DocumentDB cluster member instance 不允许在 `CreateDBInstance` 请求里显式传 `PubliclyAccessible`，成员实例会从 cluster 继承该属性。

`rds.aws.m.upbound.io/ClusterInstance` 的 RDS 语义会把该字段传给 AWS。即使 Git 里删掉字段，Crossplane 也可能在 observe 后把 live spec late-initialize 成 `publiclyAccessible: false`，于是 ArgoCD 又会看到 Git/live drift。

## 修复

### Step 1. Git 里移除危险字段

从 `ClusterInstance` 删除：

```yaml
spec:
  forProvider:
    publiclyAccessible: false
```

然后跑 validator：

```bash
python3 "$skill_root/validators/check_db_resource_contracts.py" <k8s-output-dir>
```

这个 validator 会拦截 `engine: docdb` 的 RDS `ClusterInstance` 继续声明 `publiclyAccessible`。

### Step 2. 如果 provider 仍然反复 Create 失败

没有专用 DocDB provider 时，短期可由具备 AWS 权限的人手动创建同名 member instance，再让 Crossplane 通过 `crossplane.io/external-name` 观察：

```bash
AWS_PROFILE=<profile> aws docdb create-db-instance \
  --region <region> \
  --db-instance-identifier <clusterinstance-name> \
  --db-cluster-identifier <docdb-cluster-name> \
  --db-instance-class <instance-class> \
  --engine docdb
```

等 AWS 进入 `creating` 后，强制 Crossplane reconcile：

```bash
kubectl -n crossplane-system annotate \
  clusterinstance.rds.aws.m.upbound.io <clusterinstance-name> \
  crossplane.io/reconcile="$(date +%s)" --overwrite
```

验证：

```bash
kubectl -n crossplane-system get clusterinstance.rds.aws.m.upbound.io <clusterinstance-name> -o wide
AWS_PROFILE=<profile> aws docdb describe-db-instances \
  --region <region> \
  --db-instance-identifier <clusterinstance-name>
```

目标状态：

- Crossplane `SYNCED=True`
- Crossplane `READY=True`
- AWS `DBInstanceStatus=available`

### Step 3. ArgoCD 忽略 Crossplane late-init 字段

在对应 ArgoCD `Application` 加：

```yaml
spec:
  ignoreDifferences:
    - group: rds.aws.m.upbound.io
      kind: ClusterInstance
      name: <clusterinstance-name>
      namespace: crossplane-system
      jsonPointers:
        - /spec/forProvider/publiclyAccessible
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

如果 Application 还管理带 PVC 的 `StatefulSet`，同时给该 StatefulSet 加：

```yaml
spec:
  ignoreDifferences:
    - group: apps
      kind: StatefulSet
      name: <statefulset-name>
      namespace: <namespace>
      jsonPointers:
        - /spec/volumeClaimTemplates
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

验证：

```bash
kubectl -n argo-cd annotate app <app> argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argo-cd get app <app>
```

目标状态：`Synced / Healthy`。

## 为什么不走替代方案

- **把 `publiclyAccessible: false` 写回 Git**：ArgoCD 可能短暂变 `Synced`，但未来重建 member instance 时 AWS 仍然 400。
- **只 live patch 删除字段**：Crossplane observe 后会 late-init 回 live spec，ArgoCD 继续 OutOfSync。
- **删掉 ClusterInstance CR**：可能触发云资源删除，或者让 AWS 资源变成孤儿；不要把删除当作"停止管理"。
- **忽略整个 ClusterInstance spec**：会掩盖 instanceClass、engineVersion、subnet group 等真实漂移；只忽略已知 late-init 的 `publiclyAccessible`。

## 长期修法

在集群级 rollout `provider-aws-docdb` 和对应 CRD 后，新 DocumentDB workflow 应迁移到专用 DocDB provider。没有专用 provider 的环境里，DocumentDB member instance 创建应作为 Ops Todo 明确列出，不要宣称全自动。

## 参考

- validator：`validators/check_db_resource_contracts.py`
- 相关 ArgoCD 漂移：`StatefulSet.spec.volumeClaimTemplates` 是 immutable/defaulted 字段，带 PVC 的 StatefulSet 应配 `ignoreDifferences` + `RespectIgnoreDifferences=true`。

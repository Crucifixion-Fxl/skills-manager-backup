---
name: argocd-runtime-drift
description: ArgoCD Application `OutOfSync` / `SyncFailed` / 顶层 `Degraded`，但 live 资源实际 Ready；根因是 Kubernetes / Argo Rollouts / ESO / Crossplane 回填运行时默认字段造成 GitOps drift。
---

# Playbook：ArgoCD runtime drift

## 症状

用户看到以下一种或多种：

- ArgoCD Application `OutOfSync` / `SyncFailed`，但相关 Pod / Rollout / Crossplane / ESO 资源实际 Ready
- Sync 失败消息包含 PVC immutable 字段，例如 `PersistentVolumeClaim ... spec is immutable`
- `kubectl diff -k <overlay>` 只剩 controller 回填字段
- Application 顶层 `Degraded`，但 `.status.resources[]` 没有任何 Degraded 子资源
- PushSecret / ExternalSecret / Rollout / RDS Instance 在资源列表里 OutOfSync，但对象自身 Ready

这个 playbook 只处理**运行时默认字段 / 控制器回填字段**造成的 drift。不要用它掩盖真实业务失败。

## 诊断顺序

### Step 1. 先确认不是应用真挂

```bash
kubectl -n argo-cd get application.argoproj.io <app> -o json | jq '{
  sync: .status.sync.status,
  health: .status.health.status,
  operation: (.operation // null),
  operationState: {
    phase: .status.operationState.phase,
    message: .status.operationState.message
  },
  badResources: [
    .status.resources[]?
    | select(.status != "Synced" or .health.status == "Degraded")
    | {group, kind, namespace, name, status, health}
  ]
}'
```

再看 live 工作负载：

```bash
kubectl -n <app-ns> get deploy,rollout,rs,pod,job,externalsecret,pushsecret -o wide --ignore-not-found
```

如果有 `CrashLoopBackOff` / `ImagePullBackOff` / `CreateContainerConfigError` / `ExternalSecret READY=False` 且 message 指向缺真实业务 key，STOP，跳到对应 playbook：

- 镜像拉取失败：`image-pull-failure.md`
- Pod 启动失败：`pod-runtime-crash.md`
- Secret / ExternalSecret 缺真实值：`secrets-env-missing.md`

尤其是缺业务密钥（例如 `APP_ID` / `APP_SECRET` / 第三方 token）：**只产 Ops Todo，不要猜值、造值、跨环境拷值**。

### Step 2. 用 diff 定位是否只剩 runtime 字段

在应用仓库或渲染目录执行：

```bash
kubectl --context <ctx> -n <app-ns> diff -k k8s/overlays/<env> || true
```

如果 Application 由 `argocd-apps` 仓库管理，优先用 ArgoCD 自己的 compare 结果看最终差异：

```bash
tmpcfg=/tmp/kubeconfig-argocd-diff-$$
cp ${KUBECONFIG:-$HOME/.kube/config} "$tmpcfg"
KUBECONFIG="$tmpcfg" kubectl config use-context <ctx>
KUBECONFIG="$tmpcfg" kubectl config set-context --current --namespace=argo-cd
KUBECONFIG="$tmpcfg" argocd app diff <app> --core --diff-exit-code 0 --refresh || true
rm -f "$tmpcfg"
```

可接受的 runtime drift 例子：

| 资源 | 字段 | 原因 |
|---|---|---|
| PersistentVolumeClaim | `/spec/volumeName` | PVC bound 后 Kubernetes 写入 PV 名 |
| PersistentVolumeClaim | `/spec/storageClassName` | live 可能被 defaulting / provisioner 固化 |
| Rollout | `/metadata/annotations/rollout.argoproj.io~1revision` | Argo Rollouts controller 写 revision |
| Rollout | `/spec/restartAt` | 人工 `kubectl argo rollouts restart` 后留下 |
| Rollout | `/spec/template/spec/containers/0/ports/0/protocol` | Kubernetes 默认 `TCP` |
| PushSecret | `/metadata/annotations/force-resync` | 人工触发 ESO resync |
| PushSecret | `/spec/deletionPolicy` | ESO webhook / controller 默认值 |
| PushSecret | `/spec/updatePolicy` | ESO webhook / controller 默认值 |
| PushSecret | `.spec.data[]?.conversionStrategy` | ESO 默认 `None` |
| ExternalSecret | `/metadata/annotations/force-sync` | 人工触发 ESO sync |
| ExternalSecret | `/spec/target/creationPolicy` | ESO 默认值 |
| ExternalSecret | `/spec/target/deletionPolicy` | ESO 默认值 |
| RDS Instance | `/metadata/annotations/crossplane.io~1external-name` | provider 可能回填 AWS physical id |
| RDS Instance | `/spec/deletionPolicy` | `*.aws.m.upbound.io` v2 CRD 可能已改用 `managementPolicies`，API server 会丢弃旧字段 |
| RDS Instance | `/spec/writeConnectionSecretToRef/namespace` | v2 CRD / provider 可能丢弃 namespace；仅当 name 未变且对象 Ready 时可忽略 |
| RDS Cluster | `/spec/deletionPolicy` | 同上，旧字段被 CRD 丢弃 |
| RDS Cluster | `/spec/forProvider/masterPasswordSecretRef/namespace` | provider/CRD 丢弃 namespace；仅当 secret name 未变且对象 Ready 时可忽略 |
| RDS Cluster | `/spec/writeConnectionSecretToRef/namespace` | provider/CRD 丢弃 namespace；仅当 secret name 未变且对象 Ready 时可忽略 |
| RDS ClusterInstance | `/spec/deletionPolicy` | 同上，旧字段被 CRD 丢弃 |
| RDS ClusterInstance | `/spec/forProvider/identifier` | provider 以 external-name / AWS physical id 固化标识后可能丢弃 |
| S3 Bucket | `/spec/deletionPolicy` | `*.aws.m.upbound.io` v2 CRD 丢弃旧字段 |
| CloudWatch LogGroup | `/spec/forProvider/name` | provider/CRD 丢弃 name；仅当 external-name 或 live AWS 名称已固定且对象 Ready 时可忽略 |
| Lambda Function | `/spec/forProvider/functionName` | provider/CRD 丢弃 functionName；仅当 external-name 或 live AWS 名称已固定且对象 Ready 时可忽略 |
| Lambda Permission | `/spec/forProvider/sourceArnRef` | provider/CRD 丢弃 ref；仅当权限 Ready/Synced 且 source ARN 已解析时可忽略 |
| SES / SNS resource | `/spec/forProvider/name` | provider/CRD 丢弃 name；仅当 external-name 或 live AWS 名称已固定且对象 Ready 时可忽略 |

> 注：上表 `.spec.data[]?…` / `.spec.rules[]?…` 这类**索引进数组元素**的 jqPath 只做 diff 抑制——**别**配 per-app `RespectIgnoreDifferences=true`（见「修复」节 ⚠️）。ESO/Kyverno 的数组默认值漂移建议在集群级 `argocd-cm` 统一忽略。

如果 diff 里包含业务 spec 字段（镜像 tag、env、replicas、DB size、SG、endpoint 等），STOP，不能靠 ignoreDifferences 掩盖。

必须 STOP、不能直接 ignore 的例子：

| 资源 | 字段 | 原因 |
|---|---|---|
| ElastiCache / Redis | `/spec/forProvider/vpcSecurityGroupIds` | 网络访问控制，可能改变谁能连 Redis |
| CloudFront Distribution | `/spec/forProvider/origin/*/originAccessControlId` | OAC 行为，可能影响 CDN 访问 |
| CloudFront Distribution | `/spec/forProvider/origin/*/s3OriginConfig` | legacy OAI / OAC 语义差异 |
| CloudFront Distribution | `/spec/forProvider/region` | provider 作用域字段，不按 runtime drift 处理 |
| ExternalSecret | `.spec.data[]?.remoteRef.key` | Vault 路径语义差异，例如 `secret/staging/...` vs `staging/...` |
| 任意 workload | image / env / replicas / resources / probes | 业务运行参数 |
| 任意云资源 | instanceClass / engineVersion / storage / subnet / security group / endpoint | 真实云资源规格或网络配置 |

### Step 3. 确认 live 对象实际健康

```bash
kubectl -n <app-ns> get rollout <name> -o jsonpath='{.status.phase}{" "}{.status.readyReplicas}{"\n"}'
kubectl -n <app-ns> get pushsecret <name> -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{" "}{.status.conditions[?(@.type=="Ready")].message}{"\n"}'
kubectl -n <app-ns> get externalsecret <name> -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{" "}{.status.conditions[?(@.type=="Ready")].message}{"\n"}'
kubectl -n <app-ns> get instance.rds.aws.m.upbound.io <name> -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{" "}{.status.conditions[?(@.type=="Synced")].status}{"\n"}'
```

期望：对应资源 Ready / Synced。未 Ready 时，不进入本 playbook 的修复步骤。

## 根因

Kubernetes admission / controller、Argo Rollouts、ESO、Crossplane 会把运行时状态或默认值写回 live 对象。Git 中 manifest 没有这些字段时，ArgoCD 可能持续把 live 判成 OutOfSync；某些字段（尤其 PVC bound 字段）还是 immutable，强 sync / replace 会失败。

## 修复

### Step 1. 在 Application 加精确 ignoreDifferences

只忽略已确认的 runtime 字段，限定 `group` / `kind` / `name` / `namespace`。不要忽略整段 `/spec`、整段 `/metadata/annotations`，也不要忽略业务字段。

示例：

```yaml
spec:
  ignoreDifferences:
    - group: argoproj.io
      kind: Rollout
      name: <rollout-name>
      namespace: <app-ns>
      jsonPointers:
        - /metadata/annotations/rollout.argoproj.io~1revision
        - /spec/restartAt
        - /spec/template/spec/containers/0/ports/0/protocol
    - kind: PersistentVolumeClaim
      name: <pvc-name>
      namespace: <app-ns>
      jsonPointers:
        - /spec/storageClassName
        - /spec/volumeName
    - group: external-secrets.io
      kind: PushSecret
      namespace: <app-ns>
      jsonPointers:
        - /metadata/annotations/force-resync
        - /spec/deletionPolicy
        - /spec/updatePolicy
      # 别在这里加 `.spec.data[]?...` 这类索引进数组的 jqPath：本 App 开了
      # RespectIgnoreDifferences=true，二者同用会在 data 成员增删时死锁（见下方 ⚠️）。
      # ESO conversionStrategy/None 默认值漂移在集群级 argocd-cm 的
      # resource.customizations.ignoreDifferences 里 diff 侧统一忽略。
    - group: external-secrets.io
      kind: ExternalSecret
      namespace: <app-ns>
      jsonPointers:
        - /metadata/annotations/force-sync
        - /spec/target/creationPolicy
        - /spec/target/deletionPolicy
    - group: rds.aws.m.upbound.io
      kind: Instance
      name: <rds-instance-name>
      namespace: <app-ns>
      jsonPointers:
        - /metadata/annotations/crossplane.io~1external-name
        - /spec/deletionPolicy
        - /spec/writeConnectionSecretToRef/namespace
    - group: rds.aws.m.upbound.io
      kind: Cluster
      name: <rds-cluster-name>
      namespace: <app-ns>
      jsonPointers:
        - /spec/deletionPolicy
        - /spec/forProvider/masterPasswordSecretRef/namespace
        - /spec/writeConnectionSecretToRef/namespace
    - group: rds.aws.m.upbound.io
      kind: ClusterInstance
      name: <rds-cluster-instance-name>
      namespace: <app-ns>
      jsonPointers:
        - /spec/deletionPolicy
        - /spec/forProvider/identifier
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

如果 Application 已有 `syncOptions`，只追加 `RespectIgnoreDifferences=true`，保留原有选项。

> ⚠️ **别把 `RespectIgnoreDifferences=true` 和索引进数组元素的 jqPath（`.spec.data[]` / `.spec.dataFrom[]` / `.spec.rules[]`）一起用。** 这类数组是 atomic 的；成员增删时 `RespectIgnoreDifferences` 会用 live 值把整段数组 pre-patch 回 desired，apply 静默丢掉新增成员（apiserver 报 `unchanged`），App 卡在 `phase=Succeeded` 却永久 `OutOfSync + Degraded`、selfHeal 空转。
> 解法（任选）：该 app 不开 `RespectIgnoreDifferences`（`ignoreDifferences` 默认仍在 diff 侧压漂移）；或 jqPath 只 ignore scalar / 整对象路径；或改用 `ServerSideApply`（需 CRD 有结构化 schema）。

> ⚠️ **反向陷阱：被 `ignoreDifferences` + `RespectIgnoreDifferences=true` 覆盖的字段，git 改了也下发不到已存在的 live 对象。** 把 `managementPolicies` 或整段 `/spec/forProvider` 列进 ignoreDifferences、且该 app 开了 `RespectIgnoreDifferences=true` 之后，再在 git 改这些字段——Application 仍显示 `Synced`（差异落在被忽略字段里），sync 也不会 apply，live 值原封不动。这类字段要生效必须 **`kubectl patch` live 对象**；因为字段被忽略，patch 不会被 selfHeal 回滚（持久）。git manifest 只是意图来源 + 新建对象时的初值。典型：要把有状态库的 `managementPolicies` 收紧成去掉 `Delete`（orphan-safe），git 改完不生效，须对 live CR patch `spec.managementPolicies`，改完用 `kubectl get ... -o jsonpath` 核 live 真生效（别只看 Application `Synced`）。

### Step 2. 合并 Git 变更后同步 root app

```bash
kubectl -n argo-cd annotate application.argoproj.io <root-app> \
  argocd.argoproj.io/refresh=hard --overwrite
```

如需要立即止血，可以先 patch live Application，但必须随后提交 Git 变更，否则 root app 会覆盖 live patch。

### Step 3. 清旧失败状态并 hard refresh

只有在资源已 Ready、Git 也有修复时才清旧状态：

```bash
kubectl -n argo-cd patch application.argoproj.io <app> --type=json \
  -p '[{"op":"remove","path":"/status/operationState"},{"op":"remove","path":"/status/conditions"}]' || true

kubectl -n argo-cd annotate application.argoproj.io <app> \
  argocd.argoproj.io/refresh=hard --overwrite
```

验证：

```bash
kubectl -n argo-cd get application.argoproj.io <app> -o jsonpath='{.status.sync.status} {.status.health.status} {.status.operationState.phase} {.status.conditions}{"\n"}'
kubectl -n argo-cd get application.argoproj.io <app> -o jsonpath='{range .status.resources[*]}{.kind}{"/"}{.name}{"="}{.status}{" "}{end}{"\n"}'
```

期望：

- Application `Synced / Healthy`
- `operationState.phase` 为空或 `Succeeded`
- `conditions` 为空
- 子资源都是 `Synced`

如果合并后仍 `OutOfSync`，不要假设是缓存。按 Step 2 重新跑 `argocd app diff --refresh`：

- 如果只剩新的已知 runtime 字段，再补一轮精确 ignoreDifferences。
- 如果出现 `vpcSecurityGroupIds`、CloudFront OAC、Vault path、DB 规格等真实配置字段，STOP，单独找资源 owner 确认。
- 每轮修复后都要回到 live Application 状态验证，不能只看 MR diff。

## 为什么不走替代方案

- **强制 sync / replace**：PVC bound 字段 immutable，replace 可能继续失败，甚至扩大影响。
- **删除 PVC / RDS / PushSecret 重建**：可能丢数据或重写生产凭据。除非 playbook 明确要求，默认禁止。
- **忽略整个 `/spec`**：会掩盖真实 drift，例如镜像、资源规格、网络和数据库配置。
- **手工补业务 Secret**：只有拿到权威值时才能写。缺值本身不是 runtime drift，必须交给 secret owner。

## 已知 runtime-drift 模式（合并后必查 live 二次 diff）

- **PVC bound 字段 / Rollout runtime 字段 / PushSecret 默认字段 / RDS `crossplane.io/external-name` 回填** → ArgoCD 报 OutOfSync 但资源实际 Ready。
- **PushSecret 已 Ready 但旧失败状态残留 + ESO/Crossplane runtime drift** → Application 长期停在 OutOfSync。
- **RDS 漂移不止 `/spec/deletionPolicy`**：忽略它之后真实剩余 diff 常是 `crossplane.io/external-name` 和 `writeConnectionSecretToRef.namespace`。**结论：必须合并后查 live 状态并二次 diff，不能用第一次分类替代验证。**

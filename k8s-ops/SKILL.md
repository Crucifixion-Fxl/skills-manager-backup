---
name: k8s-ops
description: Manage A4x Kubernetes resources via kubectl. Use when the user needs to view, troubleshoot, configure kubeconfig/context access, or carefully modify workloads across the managed 16-cluster AWS/GCP/Tencent fleet, including staging OIDC access and GitOps-managed live-change safety checks.
---

# k8s-ops

通过 `kubectl` 协助 SRE / 后端工程师查看、排障和谨慎变更 A4x Kubernetes 集群资源。

**适用场景：**

- 查看 Pod / Deployment / Service / Ingress / Node / Event 状态
- 获取日志、describe、rollout status、资源占用等排障信息
- 执行明确授权的变更，如 scale、rollout restart、apply、delete 单个资源
- 临时端口转发、exec 进入容器等交互式排障

**不适用场景：**

- 新增或长期修改 GitOps 管理资源：优先改应用仓、`k8s`、`argocd-apps`、`crossplane-infra` 并走 MR
- 新增公共 DNS、公开 Ingress、放开安全组、修改数据存储公网访问
- 直接跳过 ArgoCD / Crossplane / External Secrets 的持久化来源做长期 live patch

## 集群信息

当前受管 fleet 以 `k8s/clusters/`、`crossplane-infra/`、`argocd-fleet-scanner/k8s/overlays/sg-devops/configmap.yaml` 为准，共 16 个集群。`argocd-apps/aws-584949097249-us-tech-service` 是历史 legacy 目录，不属于当前 16 集群 inventory；不要把 `us-tech-legacy-584` 当成默认目标。

开发者自助接 staging kubectl 的 kubeconfig 渲染以 `DEV/addx-cluster-login` 的 `clusters/*.yaml` 为准；本 skill 只记录稳定入口和操作契约。

使用 context 时优先选择本表的 **首选 Context**。如果本机没有首选 context，但有同一行的别名或 ARN context，可以使用别名；不要执行 `kubectl config use-context` 修改用户全局默认 context。

### SG 区域

| Fleet 名称 | 平台 / 账号 / Region | 首选 Context | k8s 仓库目录 | ArgoCD |
|------|------|------|------|------|
| SG DevOps | AWS `125710977284` / `ap-southeast-1` | `sg-devops` | `clusters/aws-125710977284-sg-devops/` | `argocd-sg-devops.addx.live` |

常见别名：

- SG DevOps: `sg-eks-audit`（审计/只读语义时优先考虑）

### CN 区域

| Fleet 名称 | 平台 / 账号 / Region | 首选 Context | k8s 仓库目录 | ArgoCD |
|------|------|------|------|------|
| CN Tech Service | AWS China `589899215075` / `cn-north-1` | `cn-eks-tech-589-admin` | `clusters/aws-589899215075-cn-tech-service/` | `argocd-cn-tech-service.addx.live` |
| CN Prod | AWS China `741924744516` / `cn-north-1` | `cn-eks-prod-admin` | `clusters/aws-741924744516-cn-prod/` | `argocd-cn.addx.live` |
| CN Staging | AWS China `801447536674` / `cn-north-1` | `cn-eks-staging` | `clusters/aws-801447536674-cn-staging/` | `argocd-cn-staging.addx.live` |
| CN Dev | AWS China `801447536674` / `cn-north-1` | `cn-eks-dev` | `clusters/aws-801447536674-cn-dev/` | `argocd-cn-dev.addx.live` |
| CN Main TKE | Tencent `100014919455` / `ap-beijing` | `tke-cn-k8s` | `clusters/tencent-100014919455-cn-main/` | `argocd-cn-k8s.addx.live` |

常见别名：

- CN Tech Service: `cn-tech`, `arn:aws-cn:eks:cn-north-1:589899215075:cluster/cn-eks-tech-service`
- CN Prod: `cn-eks-data-ops`, `arn:aws-cn:eks:cn-north-1:741924744516:cluster/cn-eks`
- CN Staging: `cn-eks-staging-admin`, `cn-eks-staging-audit`
- CN Dev: `cn-eks-dev-admin`, `arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-dev`

### EU 区域

| Fleet 名称 | 平台 / 账号 / Region | 首选 Context | k8s 仓库目录 | ArgoCD |
|------|------|------|------|------|
| EU Tech Service | AWS `010840394398` / `eu-central-1` | `eu-eks-tech-010-admin` | `clusters/aws-010840394398-eu-tech-service/` | `argocd-eu-tech-service.addx.live` |
| EU Prod | AWS `740315635167` / `eu-central-1` | `eu-eks-prod-admin` | `clusters/aws-740315635167-eu-prod/` | `argocd-eu.addx.live` |
| EU Data | AWS `769494896000` / `eu-central-1` | `eu-eks-data-admin` | `clusters/aws-769494896000-eu-data/` | `argocd-eu-data.addx.live` |
| EU Staging | AWS `390709477306` / `eu-central-1` | `eu-eks-staging` | `clusters/aws-390709477306-eu-staging/` | `argocd-eu-staging.addx.live` |

常见别名：

- EU Tech Service: `eu-eks-tech-service`, `arn:aws:eks:eu-central-1:010840394398:cluster/eu-eks-tech-service`
- EU Prod: `arn:aws:eks:eu-central-1:740315635167:cluster/eu-eks`
- EU Data: `arn:aws:eks:eu-central-1:769494896000:cluster/eu-prod-data`
- EU Staging: `eu-eks-staging-admin`, `eu-eks-staging-audit`

### US 区域

| Fleet 名称 | 平台 / 账号 / Region | 首选 Context | k8s 仓库目录 | ArgoCD |
|------|------|------|------|------|
| US Tech Service | AWS `002497567426` / `us-east-1` | `us-eks-tech-002-admin` | `clusters/aws-002497567426-us-tech-service/` | `argocd-us-tech-service.addx.live` |
| US Prod | AWS `302571458622` / `us-east-1` | `us-eks-prod` | `clusters/aws-302571458622-us-prod/` | `argocd-us.addx.live` |
| US Data | AWS `769494896000` / `us-east-1` | `us-eks-data-admin` | `clusters/aws-769494896000-us-data/` | `argocd-us-data.addx.live` |
| US Staging | AWS `390709477306` / `us-east-1` | `us-eks-staging` | `clusters/aws-390709477306-us-staging/` | `argocd-us-staging.addx.live` |
| US Tech Service GKE | GCP `a4xcloud-tech-service-us` / `us-east4` | `gke_a4xcloud-tech-service-us_us-east4_us-tech-service-east4-gke` | `clusters/gcp-a4xcloud-tech-service-us-us-tech-service/` | `argocd-us-tech-service-gke.addx.live` |
| US Prod GKE | GCP `a4xcloud-p-us` / `us-east4` | `gke_a4xcloud-p-us_us-east4_us-prod-east4-gke` | `clusters/gcp-a4xcloud-p-us-us-prod/` | `argocd-us-prod-gke.addx.live` |

常见别名：

- US Tech Service: `us-eks-tech-service`, `us-eks-tech-service-admin`, `arn:aws:eks:us-east-1:002497567426:cluster/us-eks`
- US Prod: `us-eks-prod-admin`, `arn:aws:eks:us-east-1:302571458622:cluster/us-eks`
- US Data: `arn:aws:eks:us-east-1:769494896000:cluster/us-prod-data`
- US Staging: `us-staging`, `us-eks-staging-admin`, `us-eks-staging-iam`

### 未受管集群

以下集群不属于当前受管 fleet（无 k8s 仓库目录、不被 ArgoCD 管理），可能出现在个人 kubeconfig 中，注意与受管集群区分：

| 集群 | 平台 / 位置 | 说明 |
|------|------|------|
| US Prod GKE（旧项目） | GCP `a4xcloud-p` / `us-east4` | 仍在运行，工作负载逐步迁往 `a4xcloud-p-us` 的 US Prod GKE |
| US Tech Service GKE（旧项目） | GCP `a4xcloud-tech-service` / `us-east4` | 仍在运行，工作负载逐步迁往 `a4xcloud-tech-service-us` 的 US Tech Service GKE |

## 执行流程

### Step 1: 环境检查

首次操作时自动检查：

```bash
kubectl version --client
kubectl config get-contexts -o name
```

如果目标 context 未配置：

- AWS EKS: 引导用户执行 `aws eks update-kubeconfig --name <eks-cluster-name> --region <region> --alias <preferred-context>`。AWS China 使用正确的 `aws-cn` 凭据和 `cn-north-1`。不要从 fleet 名称猜 EKS cluster name，使用下表。
- GCP GKE: 在 WSL 中不要运行 `gcloud auth login`。`gcloud` 只装在 Windows host，使用：
  - `cmd.exe /c "gcloud container clusters get-credentials us-tech-service-east4-gke --region us-east4 --project a4xcloud-tech-service-us"`
  - `cmd.exe /c "gcloud container clusters get-credentials us-prod-east4-gke --region us-east4 --project a4xcloud-p-us"`
- Tencent TKE: 使用已配置的 `tke-cn-k8s` context；缺失时通过腾讯云控制台或团队既有 kubeconfig handoff 获取，不要猜测凭据。

AWS EKS cluster name 速查：

| Fleet 名称 | EKS cluster name | Region | Alias 建议 |
|------|------|------|------|
| US Tech Service | `us-eks` | `us-east-1` | `us-eks-tech-002-admin` |
| US Prod | `us-eks` | `us-east-1` | `us-eks-prod` |
| US Data | `us-prod-data` | `us-east-1` | `us-eks-data-admin` |
| US Staging | `us-eks-staging` | `us-east-1` | `us-eks-staging` |
| EU Tech Service | `eu-eks-tech-service` | `eu-central-1` | `eu-eks-tech-010-admin` |
| EU Prod | `eu-eks` | `eu-central-1` | `eu-eks-prod-admin` |
| EU Data | `eu-prod-data` | `eu-central-1` | `eu-eks-data-admin` |
| EU Staging | `eu-eks-staging` | `eu-central-1` | `eu-eks-staging` |
| CN Tech Service | `cn-eks-tech-service` | `cn-north-1` | `cn-eks-tech-589-admin` |
| CN Prod | `cn-eks` | `cn-north-1` | `cn-eks-prod-admin` |
| CN Dev | `cn-eks-dev` | `cn-north-1` | `cn-eks-dev` |
| CN Staging | `cn-eks-staging` | `cn-north-1` | `cn-eks-staging` |
| SG DevOps | `sg-eks` | `ap-southeast-1` | `sg-devops` |

开发者 staging 访问优先走 `addx-cluster-login`，不是云厂商 admin kubeconfig：

```bash
addx-cluster-login --list
addx-cluster-login us-staging
addx-cluster-login eu-staging
addx-cluster-login cn-staging
```

`addx-cluster-login` 只覆盖 staging，默认 namespace 分别是 `staging-us`、`staging-eu`、`staging-cn`，权限契约是业务 namespace 内只读 + `logs` / `exec` / `attach` / `port-forward` 调试。不要建议开发者用它读取 Secret、写资源、删 Pod、访问 `kube-system` / `argo-cd` / cluster-scoped 资源。

### Step 2: 确认操作目标

从用户描述中解析：

1. 目标集群：匹配区域、环境、账号、ArgoCD host 或 k8s 目录。
2. 目标 namespace：未提供时，查看类操作可用 `-A` 搜索；变更类操作不能默认写到 `default`。
3. 目标资源：明确 kind/name/container，避免对模糊匹配的多个资源直接变更。
4. 操作类型：只读、交互式排障、变更、网络/公开暴露相关变更。

### Step 3: 指定 Context

始终使用 `--context`，不要修改全局 context：

```bash
kubectl --context <context-name> -n <namespace> <command>
kubectl --context <context-name> get <resource> -A
```

### Step 4: 执行操作

#### 查看类操作

只读查看可直接执行，并在输出很长时做摘要：

```bash
kubectl --context <ctx> -n <ns> get pods
kubectl --context <ctx> -n <ns> get deploy,sts,svc,ingress
kubectl --context <ctx> -n <ns> logs <pod-name> --tail=100
kubectl --context <ctx> -n <ns> logs <pod-name> -c <container> --previous --tail=100
kubectl --context <ctx> -n <ns> describe pod <pod-name>
kubectl --context <ctx> -n <ns> get events --sort-by='.lastTimestamp'
kubectl --context <ctx> top nodes
kubectl --context <ctx> -n <ns> top pods
```

Crossplane v2 的 `*.m.upbound.io` managed resources 是 namespaced 资源。做 fleet、迁移或全应用验收前，先检查 `NAMESPACED` 列；全量只读扫描必须使用 `-A`，否则只会查询当前 namespace，空结果不能证明集群里没有该资源：

```bash
kubectl --context <ctx> api-resources --api-group=s3.aws.m.upbound.io
kubectl --context <ctx> get buckets.s3.aws.m.upbound.io -A -o json
```

如果集群仍安装 legacy cluster-scoped API，迁移验收需要同时查询新旧 API。核对 `Ready` / `Synced` conditions、`metadata.deletionTimestamp`，以及 `managementPolicies` 等删除保护配置：

```bash
kubectl --context <ctx> get buckets.s3.aws.m.upbound.io -A -o json
kubectl --context <ctx> get buckets.s3.aws.upbound.io -o json
```

`-A` 只用于只读扫描，不能直接套用到 patch、apply、delete 等写操作。

#### 交互式排障

`exec`、`port-forward`、临时 debug pod 都需要先说明目标和命令。`port-forward` 只绑定本地调试端口，不替代正式暴露方案。

```bash
kubectl --context <ctx> -n <ns> exec -it <pod-name> -c <container> -- /bin/sh
kubectl --context <ctx> -n <ns> port-forward <pod-name> <local>:<remote>
kubectl --context <ctx> -n <ns> get pod <pod-name> -o jsonpath='{.spec.containers[*].name}'
```

#### 变更类操作

所有变更必须先展示完整命令、影响范围、回滚方式，等待用户明确确认后执行。优先使用 server-side dry run / diff。

在 live 变更前先判断资源是否被 ArgoCD / Helm / GitOps 管理：

```bash
kubectl --context <ctx> -n <ns> get <kind> <name> \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/instance}{" "}{.metadata.labels.argocd\.argoproj\.io/instance}{" "}{.metadata.annotations.argocd\.argoproj\.io/tracking-id}{"\n"}'
kubectl --context <ctx> -n argo-cd get applications.argoproj.io | rg '<app-or-release-name>'
```

如果资源由 ArgoCD selfHeal 管理，live patch / `rollout undo` / 手工改 ConfigMap 可能在下一轮 sync 被覆盖。默认改 GitOps 来源；紧急 live patch 必须同时说明“可能被 selfHeal 回滚”，并补回 GitOps MR。

```bash
kubectl --context <ctx> -n <ns> rollout restart deployment <name>
kubectl --context <ctx> -n <ns> scale deployment <name> --replicas=<N>
kubectl --context <ctx> diff -f <file>
kubectl --context <ctx> apply --dry-run=server -f <file>
kubectl --context <ctx> apply -f <file>
kubectl --context <ctx> -n <ns> delete <resource> <name>
```

持久化配置变更应落到 GitOps 来源：

- 集群平台组件：`k8s/clusters/<cluster>/...`
- ArgoCD Application 入口：`argocd-apps/<cluster>/...`
- 集中审批云资源 / IAM / IRSA / SG 入站规则：`crossplane-infra/<cluster>/...`
- 业务应用资源：对应应用仓 `k8s/overlays/<env>/...`

### Step 5: 结果确认

- 查看类：总结关键状态、异常、下一步建议。
- 变更类：执行后验证结果，如 `rollout status`、期望 replica、事件和 Pod 状态。
- 失败时：保留原始错误关键行，解释最可能原因，给出下一条低风险诊断命令。

## 安全规则

1. 禁止在 prod 集群执行 `delete namespace`。
2. 禁止执行 `kubectl drain`，除非用户明确点名节点、窗口和影响范围。
3. 生产集群变更必须确认具体集群、namespace、资源、命令和回滚方式。
4. 批量变更必须逐项列出，不能把 `-A`、label selector 或通配匹配直接用于写操作。
5. 不要把内部服务公开到公网。公共 Ingress / ALB 仅限 end-user-facing 服务，且必须有 WAF 或安全组来源限制。
6. 不要为内部数据存储添加 `0.0.0.0/0` 入站，不要设置 `PubliclyAccessible: true`。
7. 需要外部 allowlist 的出栈访问必须走 NAT Gateway 稳定 NAT IP；不要把动态 node public IP 加到安全组白名单。
8. 添加公共 DNS 指向 ALB / CLB / 公网 IP 前，先检查后端 Ingress/Service 暴露方式、WAF、security-groups、inbound-cidrs、认证机制，并向用户报告风险。
9. 读取日志或 Secret 周边信息时要避免回显敏感值；发现 token/password/key 只报告字段名和风险，不原样贴出。
10. 不要把 `kubectl rollout undo` 当成 GitOps 回滚方案；ArgoCD selfHeal / Image Updater 可能覆盖它。优先走 ArgoCD/Git revert 或对应应用 runbook。
11. 优先使用 `--context`，避免 `kubectl config use-context` 改变用户全局状态。
12. 全量只读审计自定义资源前先检查 `kubectl api-resources` 的 `NAMESPACED` 列；namespaced 资源必须使用 `-A`，不能把当前 namespace 的空结果当作 fleet 无资源。

## 示例

### ❌ Bad

#### 1. 不确认就执行生产变更

```
用户：帮我重启 US Prod 的 payment-service
AI：已执行 rollout restart。
```

问题：生产变更未经确认，且没有说明 namespace、命令和回滚方式。

#### 2. 修改全局 context

```bash
kubectl config use-context us-eks-prod
kubectl -n prod get pods
```

问题：污染用户其他终端会话。应使用 `--context us-eks-prod`。

#### 3. 模糊批量删除

```bash
kubectl --context eu-eks-prod-admin delete pod -A -l app=worker
```

问题：跨 namespace 写操作，影响范围不清楚。

#### 4. 公开内部工具

```bash
kubectl --context us-eks-tech-002-admin -n sourcebot apply -f public-ingress.yaml
```

问题：内部工具公开暴露前没有检查 WAF、source IP restriction 或应用层认证。

#### 5. 漏掉 Crossplane v2 的其他 namespace

```bash
kubectl --context us-eks-prod get buckets.s3.aws.m.upbound.io
```

问题：`buckets.s3.aws.m.upbound.io` 是 namespaced 资源。命令只检查当前 namespace，不能根据空结果判断集群里没有 Bucket。

### ✅ Good

#### 1. 只读排查直接执行

```
用户：看一下 EU Staging 的 personalization-engine 为什么不健康

AI：我会用 eu-eks-staging 只读检查 namespace 和最近事件：
kubectl --context eu-eks-staging get pods -A | rg personalization-engine
kubectl --context eu-eks-staging -n <ns> get events --sort-by='.lastTimestamp'
```

#### 2. 变更前确认

```
用户：帮我重启 US Prod 的 payment-service

AI：我将执行生产变更：
- 集群：US Prod (`us-eks-prod`)
- Namespace：prod
- 命令：kubectl --context us-eks-prod -n prod rollout restart deployment payment-service
- 验证：kubectl --context us-eks-prod -n prod rollout status deployment payment-service

确认后我再执行。
```

#### 3. context 缺失时给出正确配置方式

```
用户：看 US Prod GKE 的 argocd

AI：本机没有目标 GKE context。WSL 里应通过 Windows gcloud 获取凭据：
cmd.exe /c "gcloud container clusters get-credentials us-prod-east4-gke --region us-east4 --project a4xcloud-p-us"
```

#### 4. GitOps 持久化

```
用户：把 CN Staging 的 ArgoCD 配置永久改掉

AI：这是 GitOps 管理的集群组件，我会改 `k8s/clusters/aws-801447536674-cn-staging/...` 并走 MR；只有紧急恢复才做 live patch，并在恢复后补回 GitOps。
```

#### 5. Crossplane v2 全量验收

```
用户：确认 US Prod 所有 Crossplane S3 Bucket 都正常

AI：我会先确认 CRD 作用域，再跨 namespace 检查 v2 资源，并同时排查 legacy API：
kubectl --context us-eks-prod api-resources --api-group=s3.aws.m.upbound.io
kubectl --context us-eks-prod get buckets.s3.aws.m.upbound.io -A -o json
kubectl --context us-eks-prod get buckets.s3.aws.upbound.io -o json
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确说明紧急情况、目标和允许跳过常规确认；仍需在结果中报告实际命令 |
| 非公司集群 | 用户提供的 context 不在上述 16 集群表中；按普通 kubectl 操作处理，但仍遵守安全规则 |

豁免方式：`/override skill=k8s-ops reason="紧急故障恢复"`

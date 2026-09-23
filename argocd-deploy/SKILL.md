---
name: argocd-deploy
description: 为 EKS/TKE/GKE 集群部署完整 CICD 组件栈 (ArgoCD + Casdoor 飞书SSO + External Secrets + Image Updater + Kyverno + Crossplane)。在需要为新集群搭建或更新 CICD 环境时使用。
---

# argocd-deploy

为 EKS / TKE / GKE 集群部署完整 CICD 组件栈。

**适用场景：**

- 为新集群从零搭建完整 CICD 环境
- 为已有 ArgoCD 的集群添加飞书 SSO 登录 (Casdoor)
- 为集群添加或更新单个 CICD 组件

## 架构概述

CICD 栈采用 **`cicd/base/*` Helm wrapper + 集群 overlay + ArgoCD App of Apps** 模式管理，仓库职责不可互换：

- **`DEV/k8s` 的 `cicd/base/{default,cn,tencent,gcp}/`** — CICD 组件 Helm wrapper，按平台分变体
- **`DEV/k8s` 的 `clusters/{CLUSTER_DIR}/cicd/`** — 集群 values override、组件额外资源和一次性 bootstrap root Application
- **`DEV/argocd-apps` 的 `{CLUSTER_DIR}/*.yaml`** — authoritative child Application / AppProject 部署入口；新增、修改、删除应用都在这里完成
- **`root-apps`** — `k8s/clusters/{CLUSTER_DIR}/cicd/argocd/root-application.yaml` 中的一次性 bootstrap Application；它监听 `argocd-apps/{CLUSTER_DIR}`，不是由 `argocd-apps` 再管理一份 root

> `k8s/clusters/*/cicd/self-manage/` 是迁移前留下的漂移参考。`origin/master` 当前仍有 106 个 YAML，普遍保留 `spec.project: default`；**禁止复制、批量 apply 或把它当部署入口**。需要对照内容时也以 `argocd-apps origin/main` 的同名 child Application 为准。
>
> `root-apps` 自身保留 `spec.project: default`，因为它负责 bootstrap AppProject；这是 bootstrap parent 的例外，不能作为控制面 child Application 的 project 模板。

### Application / Project 关系

一个仓库可提供多个独立渲染入口，每个 Application 只绑定一个 Project；一个 Project
可有多个成员。按资源权限职责分组，不按目录名或每个 kind 机械拆分；平台 claim 可按
已批准合同留 runtime。资源须只有一个 Application 管理，存量拆分先审批 prune/finalizer
保护、tracking 接管和反向交接。owner 专属 Project 尚属目标态，不能自动新增名称、
放宽平台 IAM/ProviderConfig 权限或取消审核。原生 AppProject 限制 repo/destination/kind，
精确 path/revision 由 argocd-apps CI 合同补充。详见
[部署权限事实表](../cicd-developer/references/data/permission-boundaries.yaml)。

### Child Application 核心契约

在 `argocd-apps/{CLUSTER_DIR}` 新建或修改任何 Application 时，保留以下契约：

- 添加 `resources-finalizer.argocd.argoproj.io`
- 明确设置 `spec.project`；ArgoCD、ESO、Casdoor、Kyverno、Image Updater、Crossplane operator 等控制面 App 使用 `platform-control-plane`，不要回写 `default`
- 配置 automated sync、`selfHeal: true`；通常 `prune: true`，只有明确保护语义的资源（如 Crossplane post-install）使用 `prune: false`
- 所有 Application（包括平台/Helm 自管 App）都添加飞书三通知，值为 `""`：
  - `notifications.argoproj.io/subscribe.on-deployed.feishu-ops`
  - `notifications.argoproj.io/subscribe.on-health-degraded.feishu-ops`
  - `notifications.argoproj.io/subscribe.on-sync-failed.feishu-ops`

现存 YAML 缺通知或仍用 `default` 代表迁移漂移，不代表新文件模板。先确认目标集群已有对应 AppProject，再切换 child Application 的 project。

### App-of-Apps sync-wave 健康前置

只有 live `argocd-cm` 定义了
`resource.customizations.health.argoproj.io_Application`，父 Application 才能把 child
Application 的 `.status.health` 纳入健康评估，child Application CR 上的
`argocd.argoproj.io/sync-wave` 才能真正等待前一波 child 就绪。选择健康脚本前，先盘点
目标 root 管理的现有 child Application 健康状态：

```bash
kubectl --context <KUBE_CONTEXT> -n argo-cd get applications.argoproj.io \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
```

结合目标 root 的 resource tree 确认 child 归属，不要只检查这次新增的 Application。

仅当确认所有 child 的真实健康状态都应向父 Application 传播，且接受任何无关
Degraded/Progressing child 阻塞同 wave 及后续 wave 时，才使用以下官方全局 Lua
透传：

```yaml
data:
  resource.customizations.health.argoproj.io_Application: |
    hs = {}
    hs.status = "Progressing"
    hs.message = ""
    if obj.status ~= nil then
      if obj.status.health ~= nil then
        hs.status = obj.status.health.status
        if obj.status.health.message ~= nil then
          hs.message = obj.status.health.message
        end
      end
    end
    return hs
```

若已有无关的非 Healthy child，或不希望它们阻塞此次依赖链，使用最小名称作用域：
将所有参与排序的 Application 精确名称列入 `ordered`，仅透传这些 child，其余
child 对该父 Application 返回 Healthy。禁止用前缀或通配表达式扩大集合。

```yaml
data:
  resource.customizations.health.argoproj.io_Application: |
    local ordered = {
      ["<BOOTSTRAP_CHILD_APPLICATION>"] = true,
      ["<OPERATOR_CHILD_APPLICATION>"] = true,
      ["<WORKLOAD_CHILD_APPLICATION>"] = true
    }
    hs = {}
    if obj.metadata == nil or ordered[obj.metadata.name] ~= true then
      hs.status = "Healthy"
      hs.message = "Not part of the ordered child Application set"
      return hs
    end
    hs.status = "Progressing"
    hs.message = ""
    if obj.status ~= nil then
      if obj.status.health ~= nil then
        hs.status = obj.status.health.status
        if obj.status.health.message ~= nil then
          hs.message = obj.status.health.message
        end
      end
    end
    return hs
```

全局透传会让 root 同步同时承担所有 child 的存量健康债务：一个与新部署无关的
Degraded child 也可以使 root 长期 Running/Degraded，并阻止后续 wave。不要在未盘点时
直接启用全局透传。

在合并任何依赖 sync-wave 的后续 child Application 前，必须直接验证 live
ConfigMap；Git 中存在 desired 配置不等于前置已生效：

```bash
kubectl --context <KUBE_CONTEXT> -n argo-cd get configmap argocd-cm \
  -o go-template='{{ index .data "resource.customizations.health.argoproj.io_Application" }}{{ "\n" }}'
```

输出为空、没有转发 `obj.status.health`，或名称作用域脚本的 live `ordered` 与当前依赖集
不一致时，禁止合并依赖该 wave 顺序的应用。先单独部署并验证健康自定义；若无法提供，
将依赖资源合并到同一 child Application 中，或使用显式等待 Namespace、CRD、operator
Deployment 等具体前置的其他确定性门禁。

`CreateNamespace=true` 只表示 child 在自身 sync 时可以创建 destination Namespace；它不会让
父 Application 等待 child 就绪，也不保证后续 wave 的 child Application 会等到该
Namespace 已创建。

### CreateNamespace 与 AppProject 权限门禁

新增或修改 child Application 使用 `CreateNamespace=true`，或依赖较早 child 创建
destination Namespace 时，必须先完整读取
[Application Namespace Bootstrap Gate](references/application-namespace-bootstrap-gate.md)。
准确 AppProject 必须允许 core `/Namespace`，否则由有权 project 下、更低 wave 的
bootstrap 创建准确目标 Namespace；bootstrap 可使用 exact destination，也可 render 带
`Prune=false,Delete=false` 的同名显式 Namespace。live Application health gate 和 Namespace
存在性都必须验证。禁止为清掉一次 SyncFailed 直接扩权，或把控制面 workload 退回
`default`。

### Application apply 策略迁移红线

修改 App-of-Apps child Application 的资源级
`argocd.argoproj.io/sync-options` 时，必须同时检查 Git desired 和 live annotation。
父 Application 在一次 sync 开始时根据 **live child** 的 `Replace=true` / `Force=true`
决定动作；在 desired 中删除该选项，不会让同一次 sync 自动改用 client-side apply。
该次仍可能执行 replace，直到操作完成后 live annotation 才被移除。

因此禁止把「删除 `Replace=true`」当作升级后第一次安全 client-side apply。涉及
Application CRD、controller minor version 或 SSA/CSA 切换时，必须先读
[references/app-of-apps-apply-strategy-migration.md](references/app-of-apps-apply-strategy-migration.md)
和其中指向的仓库 runbook，再设计或执行迁移。

### cicd/base 变体

| 变体 | 适用集群 | 主要差异 |
|------|---------|---------|
| `cicd/base/default/` | US/EU/SG EKS、GKE 常规组件 | 上游官方 Helm chart repo 或 vendor chart |
| `cicd/base/cn/` | CN EKS (`cn-tech-service`, `cn-prod`, `cn-dev`, `cn-staging`) | CN Harbor / AWS China 适配 |
| `cicd/base/tencent/` | TKE (`tencent-cn-main`) | 腾讯云 CLB / TKE 适配 |
| `cicd/base/gcp/` | GKE 专用差异组件 | GKE/Casdoor/Crossplane 差异，缺什么以目录为准 |

## 组件总览

| 组件 | Namespace | Helm Chart 版本 | App 版本 | 说明 |
|------|-----------|----------------|---------|------|
| External Secrets Operator | external-secrets | v1.3.2 | v0.14.x | 将 Vault 密钥同步到 K8s Secret |
| ArgoCD | argo-cd | chart 9.4.0 | v3.3.0 | GitOps 持续部署平台 |
| ArgoCD Image Updater | argo-cd | v0.11.2 | v0.15.1 | 监控 Harbor 镜像更新并触发部署 |
| Casdoor | casdoor | v1.702.0 | v1.702.0 | OIDC 桥接层，对接飞书 SSO → ArgoCD |
| Kyverno | kyverno | chart 3.7.1 | v1.17.1 | K8s 策略引擎 |
| Crossplane | crossplane-system | v2.2.0 | v2.2.0 | 多云资源声明式管理；provider/post-install 按 EKS、GKE、TKE 平台变化 |

## 目录结构

> k8s 仓库内相对路径。新集群命名规则：`{cloud}-{accountId}-{purpose}`

```
clusters/{CLUSTER_DIR}/cicd/
├── argocd/
│   ├── values-override.yaml        # ArgoCD 集群特定配置 (域名/Ingress/OIDC/Notifications)
│   └── root-application.yaml       # 一次性 apply；监听 argocd-apps/{CLUSTER_DIR}
├── argocd-image-updater/
│   └── values-override.yaml
├── casdoor/
│   ├── values-override.yaml
│   ├── mysql.yaml                  # MySQL Deployment + PVC + Service
│   ├── internal-ingress.yaml       # 内部 ALB/CLB Ingress (供 ArgoCD Pod 访问)
│   ├── coredns-patch.yaml          # CoreDNS rewrite 配置
│   └── init-casdoor.sh             # Casdoor API 初始化脚本
├── crossplane/                     # operator values + 平台对应的 post-install 资源
│   ├── values-override.yaml
│   └── post-install/
│       ├── providers.yaml          # AWS providers
│       ├── provider-config.yaml    # ClusterProviderConfig (IRSA)
│       └── deployment-runtime-config.yaml
├── external-secrets-config/
│   ├── cluster-secret-store.yaml   # ClusterSecretStore (Vault JWT/K8s Auth)
│   └── service-account.yaml
├── external-secrets-operator/
│   └── values-override.yaml
├── kyverno/
│   └── values-override.yaml
└── self-manage/                    # 仅历史漂移参考；禁止复制/apply
```

`argocd-apps` 仓库中的部署入口：

```text
argocd-apps/{CLUSTER_DIR}/
├── appproject-platform-control-plane.yaml
├── appproject-platform-shared-infra.yaml
├── argocd.yaml
├── external-secrets-operator.yaml
├── casdoor.yaml
├── kyverno.yaml
├── crossplane.yaml
└── crossplane-post-install.yaml
```

实际文件随平台和集群职责变化，先列目录，不要按上面的示意清单盲目补齐。

### 集群信息查找

不要维护静态“已部署集群”表。每次从远端主分支重新发现：

```bash
# k8s 已配置 bootstrap root 的集群（当前 authoritative 集群配置清单）
git -C ~/Project/A4x/k8s fetch origin master
git -C ~/Project/A4x/k8s ls-tree -r --name-only origin/master -- clusters \
  | sed -n 's#^clusters/\([^/]*\)/cicd/argocd/root-application.yaml$#\1#p' | sort

# argocd-apps 已存在的 child Application 目录（可能包含历史/未完成目录）
git -C ~/Project/A4x/argocd-apps fetch origin main
git -C ~/Project/A4x/argocd-apps ls-tree -d --name-only origin/main \
  | grep -E '^(aws|gcp|tencent)-' | sort
```

`origin/master` 当前有 17 个 root Application，最新包含 `aws-125710977284-us-francis`。**目录存在不等于 fleet onboarding 完成**：逐项验证

1. `k8s` root 的 `spec.source.path` 精确指向 `argocd-apps/{CLUSTER_DIR}`，live `root-apps` 已 Synced/Healthy。
2. `argocd-apps/{CLUSTER_DIR}` 有匹配平台的 AppProject 和 authoritative child Applications，核心契约完整。
3. `argocd-fleet-scanner` 的 live `k8s/overlays/sg-devops/configmap.yaml` 已加入集群，并配齐 readonly account、Vault token、网络白名单和 API smoke test。
4. ArgoCD Notifications 的 webhook ExternalSecret Ready，deployed/degraded/sync-failed 三个 trigger 可用，child Applications 已订阅飞书三通知并完成测试事件验证。

`aws-125710977284-us-francis` 当前已有 `k8s` 与 `argocd-apps` 目录，但尚未进入 fleet scanner 配置，必须视为 **onboarding 未完成**。

集群特定变量从 `k8s/clusters/{CLUSTER_DIR}/` 下的现有配置文件查找，包含：
- Kube Context、Region、AWS Account
- Subnets、Security Groups、ACM 证书 ARN
- ArgoCD/Casdoor 域名、Vault 地址、EKS OIDC Issuer

## 集群变量

### EKS 集群

| 变量 | 说明 | 示例 |
|------|------|------|
| CLUSTER_DIR | 集群目录名 | `aws-002497567426-us-tech-service` |
| KUBE_CONTEXT | kubectl context | `arn:aws:eks:us-east-1:002497567426:cluster/us-eks` |
| REGION | AWS 区域 | `us-east-1` |
| AWS_ACCOUNT | AWS 账号 | `002497567426` |
| SUBNETS | ALB 子网列表 | `subnet-078ca67c86ac9e15c,...` |
| SG_FROM_OFFICE | 外部 ALB 安全组 | `sg-0f6af430ca1874b92` |
| ACM_CERT_ARN | 通配符证书 ARN | `arn:aws:acm:...:certificate/...` |
| ARGOCD_DOMAIN | ArgoCD 访问域名 | `argocd-us-tech-service.addx.live` |
| CASDOOR_DOMAIN | Casdoor 访问域名 | `casdoor-us-tech-service.addx.live` |
| VPC_ID | VPC ID | `vpc-0d5678336a1680b98` |
| VPC_CIDR | VPC 网段 | `10.120.0.0/16` |
| EKS_OIDC_ISSUER | EKS OIDC URL | `https://oidc.eks.us-east-1.amazonaws.com/id/...` |
| VAULT_INTERNAL_ADDR | Vault 内部地址 | `https://vault-us-internal-new.addx.live` |
| HARBOR_REGISTRY | Harbor 地址 | 优先使用本集群 Harbor；从 `clusters/<cluster>/cicd/*/values-override*.yaml` 或 base-images `.gitlab-ci.yml` 查 |
| CICD_BASE | base 变体 | `cicd/base/default`、`cicd/base/cn`、`cicd/base/tencent`、`cicd/base/gcp` |

### TKE 额外变量

| 变量 | 说明 | 示例 |
|------|------|------|
| TLS_SECRET_NAME | 腾讯云 SSL 证书 Secret | `addx-live-2023-wgyxwfot` |
| INTERNAL_SUBNET | 内网 CLB 子网 | `subnet-gix3oqcx` |
| STORAGE_CLASS | 存储类 | `cbs-topo` |

### 区域差异

| 区域 | AWS Partition | Vault 外部地址 | Vault 内部地址 | Harbor |
|------|-------------|----------------|---------------|--------|
| US/SG EKS | aws | vault-us-new.addx.live | vault-us-internal-new.addx.live | 本集群 Harbor，如 `harbor-00249-us-tech.addx.live` / `harbor-12571-sg-devops.addx.live` |
| EU EKS | aws | vault-eu.addx.live | vault-eu-internal.addx.live | 本集群 Harbor，如 `harbor-01084-eu-tech.addx.live` |
| CN EKS | aws-cn | vault-cn.addx.live | vault-cn-internal.addx.live | 本集群 Harbor + 旧 `registry-harbor-cn.addx.live` 兼容 |
| TKE | N/A | vault-cn.addx.live | vault-cn-internal.addx.live | `harbor-cn.addx.live` |
| GKE | GCP | vault-us-new.addx.live | 外部/专用网络路径 | `harbor-a4xt-us-tech.addx.live` / `harbor-a4xp-us-prod.addx.live` |

> **注意:** CN EKS ARN 前缀为 `arn:aws-cn:`

> **⚠️ Builder / Ops 域分离（关键，先看这条再 onboard）：** 上表的 Vault 地址是 **Ops 域** vault（`vault-{us-new,eu,cn}.addx.live`），**只用于承载 C 端或运维负载的集群**：prod / tech-service / data-ops / sg-devops。**dev / staging / sandbox 等 Builder 域集群必须改用 Builder Vault**——外部域名 `vault-{cn,us,eu}.builder.addx.live`（三个都在；cn-staging / us-staging / eu-staging 的 ESO 实际就用各自**外部**域名）。**内部跨集群域名只有 CN 一个** `vault-cn-internal.builder.addx.live`——仅 **cn-dev** 用（它在 801 账号，经 VPC peering 访问 cn-tech/589 上的 CN Builder Vault）。**不存在** `vault-us-internal.builder` / `vault-eu-internal.builder`（US/EU 的 Builder Vault 就跑在各自 staging 集群内，用外部域名即可，或集群内 service `vault-builder-active.vault-builder.svc:8200`）。
>
> **切勿把 Builder 域集群 onboard 到 Ops vault。** §3.2 创建的 `external-secrets-policy` 是 `secret/data/*` **全量读**；一旦在 Ops vault 上为某 Builder 集群建了 `external-secrets` role，该集群的 ESO（`external-secrets/external-secrets` SA）即可认证进 Ops vault 读取**全部 C 端生产密钥**，Builder/Ops 隔离被旁路。2026-06 cn-dev 即因此误配（jwt-eks-dev/external-secrets 建在了 `vault-cn.addx.live`）被实测可全量读 C 端密钥，已下线。Builder 域集群的 ESO 只连 Builder Vault（ClusterSecretStore 名 `vault-builder-backend`）。

## 执行流程

### Step 1: 确认目标集群和变量

1. 确认 kubectl context 可用：
   ```bash
   kubectl config get-contexts | grep <KUBE_CONTEXT>
   # 如不存在：
   aws eks update-kubeconfig --name <cluster-name> --region <REGION> --profile <AWS_PROFILE>
   ```
2. 参考现有最近的同类型集群目录（US/EU/CN EKS 或 TKE）的配置作为模板

### Step 2: 创建集群目录结构

1. 在 `k8s` 创建 `clusters/{CLUSTER_DIR}/cicd/`，逐组件参考同云、同区域、同 Vault 域的现有集群；不要整目录复制 `self-manage/`。
2. 在 `argocd-apps` 创建 `{CLUSTER_DIR}/`，从 `origin/main` 的同平台集群复制并逐个核对 authoritative AppProject / child Application。
3. 创建 `k8s/clusters/{CLUSTER_DIR}/cicd/argocd/root-application.yaml`，让 `spec.source` 指向 `DEV/argocd-apps.git` 的 `{CLUSTER_DIR}`。
4. 同时准备 fleet scanner 与 Notifications onboarding；不要把创建两仓目录当作完成标志。

### Step 3: Bootstrap — 手动 Helm 安装

ArgoCD 自管理前必须先手动 bootstrap。部署顺序：**ESO → ArgoCD → 其余组件（通过 ArgoCD 管理）**

#### 3.1 安装 External Secrets Operator

```bash
cd clusters/{CLUSTER_DIR}/cicd/external-secrets-operator
helm dependency build
helm install external-secrets . -n external-secrets --create-namespace \
  -f values-override.yaml --kube-context <KUBE_CONTEXT>
```

#### 3.2 配置 Vault JWT Auth（Vault 侧）

> **⚠️ 先按域选对 Vault：** Builder 域集群（dev/staging/sandbox）的 `VAULT_ADDR` 必须是 **Builder Vault**（`vault-{cn,us,eu}.builder.addx.live`），**不是** Ops vault。下方 `external-secrets-policy` 授予 `secret/data/*` 全量读——在 Ops vault 上为 Builder 集群建此 role = 把 C 端生产密钥暴露给沙箱集群（见上方「区域差异」域分离说明）。

```bash
export VAULT_ADDR=<VAULT_EXTERNAL_ADDR>
vault login -method=userpass username=qlv

# 启用独立 JWT Auth path（每集群独立，见下方 JWT Auth Path 表）
vault auth enable -path=<JWT_AUTH_PATH> jwt
vault write auth/<JWT_AUTH_PATH>/config \
  oidc_discovery_url="<EKS_OIDC_ISSUER>" \
  default_role="external-secrets"

# 创建 Policy（如该 Vault 上还没有 external-secrets-policy 则先创建）
vault policy write external-secrets-policy - <<EOF
path "secret/data/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/*" {
  capabilities = ["read", "list"]
}
EOF

# 创建 Role
vault write auth/<JWT_AUTH_PATH>/role/external-secrets \
  role_type="jwt" \
  bound_audiences="https://kubernetes.default.svc" \
  user_claim="sub" \
  bound_subject="system:serviceaccount:external-secrets:external-secrets" \
  policies="external-secrets-policy" \
  ttl="1h"
```

#### 3.3 部署 External Secrets Config

```bash
kubectl --context <KUBE_CONTEXT> apply -f external-secrets-config/service-account.yaml
kubectl --context <KUBE_CONTEXT> apply -f external-secrets-config/cluster-secret-store.yaml
# 验证
kubectl --context <KUBE_CONTEXT> get clustersecretstore vault-backend
# 期望: READY=True
```

#### 3.4 Bootstrap ArgoCD（手动 Helm）

```bash
cd clusters/{CLUSTER_DIR}/cicd/argocd
helm dependency build

# 先安装基础版（不含 OIDC，Casdoor 尚未部署）
helm install argocd . -n argo-cd --create-namespace \
  -f values-override.yaml --kube-context <KUBE_CONTEXT>

# 获取 admin 密码
kubectl --context <KUBE_CONTEXT> -n argo-cd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
```

### Step 4: Bootstrap root-apps

确认 `argocd-apps/{CLUSTER_DIR}` 已提交并包含 AppProject 与 child Applications 后，只 apply 一次 bootstrap root：

```bash
kubectl --context <KUBE_CONTEXT> apply \
  -f clusters/{CLUSTER_DIR}/cicd/argocd/root-application.yaml
```

禁止执行 `kubectl apply -f clusters/{CLUSTER_DIR}/cicd/self-manage/`。`root-apps` 会从 `argocd-apps/{CLUSTER_DIR}` 创建 child Applications，并由这些 child Applications 接管 ESO、ArgoCD 和其余 CICD 组件。
存在 child Application sync-wave 依赖时，先按「App-of-Apps sync-wave 健康前置」验证 live `argocd-cm`，再合并后续依赖应用。

**先验证 root，再验证 child Applications：**
```bash
kubectl --context <KUBE_CONTEXT> -n argo-cd get app root-apps
kubectl --context <KUBE_CONTEXT> get app -n argo-cd -w
```

### Step 5: 配置 Casdoor (飞书 SSO)

#### 5.1 部署 MySQL

```bash
kubectl --context <KUBE_CONTEXT> create namespace casdoor
kubectl --context <KUBE_CONTEXT> apply -f clusters/{CLUSTER_DIR}/cicd/casdoor/mysql.yaml
```

部署后将 PV reclaimPolicy 改为 Retain：
```bash
PV=$(kubectl --context <KUBE_CONTEXT> -n casdoor get pvc casdoor-mysql-pvc -o jsonpath='{.spec.volumeName}')
kubectl --context <KUBE_CONTEXT> patch pv $PV -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

#### 5.2 Casdoor 由 ArgoCD 管理

casdoor child Application 已从 `argocd-apps/{CLUSTER_DIR}/casdoor.yaml` 创建，ArgoCD 自动部署。确保 Application 核心契约以及 `casdoor/values-override.yaml` 中域名和 Ingress 配置正确。

#### 5.3 配置内部 ALB + CoreDNS

ArgoCD Pod 需通过内网访问 Casdoor 做 OIDC 验证（外部 ALB SG 仅放行办公 IP）：

```bash
# 创建内部 ALB 安全组
SG_INTERNAL=$(aws ec2 create-security-group \
  --group-name casdoor-internal-alb \
  --description "Casdoor internal ALB - VPC access only" \
  --vpc-id <VPC_ID> --region <REGION> \
  --query 'GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id $SG_INTERNAL \
  --protocol tcp --port 443 --cidr <VPC_CIDR> --region <REGION>

# 创建内部 Ingress
kubectl --context <KUBE_CONTEXT> apply -f clusters/{CLUSTER_DIR}/cicd/casdoor/internal-ingress.yaml

# 获取内部 ALB DNS
INTERNAL_ALB=$(kubectl --context <KUBE_CONTEXT> -n casdoor get ingress casdoor-internal \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# 配置 CoreDNS（在 .:53 块内、kubernetes 行之前添加）
# rewrite name <CASDOOR_DOMAIN> <INTERNAL_ALB_DNS>
kubectl --context <KUBE_CONTEXT> -n kube-system rollout restart deployment coredns
```

#### 5.4 DNS 配置

在阿里云 DNS 添加 CNAME：`casdoor-{NAME}.addx.live` → 外部 ALB DNS

#### 5.5 初始化 Casdoor

```bash
# 修改 init-casdoor.sh 中的域名后执行（redirectUri 已含 localhost:8085，无需手动加）
export CASDOOR_ADMIN_PASSWORD="<强密码>"
export FEISHU_APP_SECRET="<飞书应用密钥>"
./clusters/{CLUSTER_DIR}/cicd/casdoor/init-casdoor.sh
# 记录输出的 clientID 和 clientSecret
```

> **ArgoCD CLI 支持**：`init-casdoor.sh` 创建的 `app-argocd-v2` 已包含 `http://localhost:8085/auth/callback`（CLI SSO 回调）和 `refresh_token` grant（refreshExpireInHours=168）。开发者可直接运行：
> ```bash
> argocd login argocd-{NAME}.addx.live --sso --grpc-web
> ```

#### 5.6 更新 ArgoCD OIDC 配置

在 `argocd/values-override.yaml` 中填入 Casdoor OIDC 配置，提交 git。`argocd-apps/{CLUSTER_DIR}/argocd.yaml` 会触发 ArgoCD 自管理升级。

### Step 6: 校验 argocd-apps child Applications

不要在 `argocd-apps` 创建 `root-application.yaml`。以已完成 `platform-control-plane` 迁移的同平台集群为模板，逐个检查 child Application：

凡 `syncOptions` 含 `CreateNamespace=true`，在检查 Application 公共契约前先执行
「CreateNamespace 与 AppProject 权限门禁」：核对 exact destination namespace、desired/live
AppProject 和更低 wave bootstrap。此门禁未通过时停止，不以扩大 whitelist 或回退
`default` 继续。

```yaml
# argocd-apps/{CLUSTER_DIR}/argocd.yaml（仅展示公共契约）
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argocd
  namespace: argo-cd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    notifications.argoproj.io/subscribe.on-deployed.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-health-degraded.feishu-ops: ""
    notifications.argoproj.io/subscribe.on-sync-failed.feishu-ops: ""
spec:
  project: platform-control-plane
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-cd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

完整 `source` / `sources`、Helm valueFiles、syncOptions 和 ignoreDifferences 必须从当前同平台 `origin/main` Application 复制并按目标集群核对，不要凭示例补写。

### Step 7: Crossplane Post-Install

Crossplane operator 和 post-install 都由 ArgoCD 管理。禁止手动 apply `post-install/`；在 `argocd-apps/{CLUSTER_DIR}/crossplane-post-install.yaml` 创建独立 child Application，source 指向 `k8s/clusters/{CLUSTER_DIR}/cicd/crossplane/post-install`，并满足：

- `spec.project: platform-shared-infra`
- `automated.prune: false`，避免删除 Application 时级联清理 Provider/ProviderConfig 并扩大云资源风险
- `automated.selfHeal: true`
- `ServerSideApply=true`、`SkipDryRunOnMissingResource=true`
- Application finalizer与飞书三通知核心契约完整

```bash
# 等待两个 Application 和 Providers 健康；只读验证，不手工修补 desired state
kubectl --context <KUBE_CONTEXT> -n argo-cd get app crossplane crossplane-post-install
kubectl --context <KUBE_CONTEXT> get providers.pkg.crossplane.io
```

`argocd-apps origin/main` 当前已有 17 份 `crossplane-post-install.yaml`，包括 EKS、GKE、TKE 的现有集群目录；是否需要具体 provider 资源以目标目录内容为准，不再用“仅 AWS 才创建 Application”推断。

**CN EKS 注意：** providers 镜像使用 `registry-harbor-cn.addx.live/charts/provider-aws-*:v2.0.0`（已同步到 CN Harbor）。

**AWS IAM 前置条件（每个 AWS 账号一次性创建）：**

1. 创建 `CrossplaneAppBoundary` IAM Policy — 标准模板见 `k8s/docs/crossplane-iam-policies.md`
2. 创建 `CrossplaneHubPolicy` IAM Policy — 同上，替换 `<ACCOUNT_ID>`
3. 创建 `CrossplaneProviderRole` IAM Role — Trust EKS OIDC Provider，Attach `CrossplaneHubPolicy`

**AWS 网络前置条件（每个账号-区域组合一次性创建）：**

```bash
# 1. DB Subnet Group（RDS + DocumentDB 共用）
aws rds create-db-subnet-group \
  --db-subnet-group-name crossplane-default \
  --db-subnet-group-description "Default subnet group for Crossplane-managed RDS/DocumentDB" \
  --subnet-ids <PRIVATE_SUBNET_1> <PRIVATE_SUBNET_2> <PRIVATE_SUBNET_3> \
  --tags Key=managed-by,Value=platform Key=purpose,Value=crossplane

# 2. Cache Subnet Group（ElastiCache 专用）
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name crossplane-default \
  --cache-subnet-group-description "Default subnet group for Crossplane-managed ElastiCache" \
  --subnet-ids <PRIVATE_SUBNET_1> <PRIVATE_SUBNET_2> <PRIVATE_SUBNET_3> \
  --tags Key=managed-by,Value=platform Key=purpose,Value=crossplane

# 3. Security Group（所有数据库服务共用）
SG_ID=$(aws ec2 create-security-group \
  --group-name crossplane-default-rds \
  --description "Allow database access from EKS pods and office network" \
  --vpc-id <VPC_ID> \
  --tag-specifications 'ResourceType=security-group,Tags=[{Key=managed-by,Value=platform},{Key=purpose,Value=crossplane}]' \
  --query 'GroupId' --output text)

# 添加 VPC CIDR 规则（EKS Pod 访问）
for port in 5432 3306 6379 27017 9092 9094 9096; do
  aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port $port --cidr <VPC_CIDR>
done

# 添加办公网络规则（公网访问）
for cidr in 111.200.53.80/29 58.250.250.53/32 58.251.23.184/29 122.224.139.146/32; do
  for port in 5432 3306 6379 27017 9092 9094 9096; do
    aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port $port --cidr $cidr
  done
done
```

> 端口说明：PostgreSQL(5432) + MySQL(3306) + Redis(6379) + DocumentDB(27017) + MSK(9092/9094/9096)

## JWT Auth Path 表

每个集群必须使用独立的 JWT Auth path（同一 Vault 实例上每个 path 只能绑定一个 OIDC issuer）：

| Vault | 集群 | JWT Auth Path |
|-------|------|---------------|
| US Vault | aws-002497567426-us-tech-service | `jwt-eks-tech-service` |
| US Vault | aws-302571458622-us-prod | `jwt-eks-prod` |
| US Vault | aws-769494896000-us-data | `jwt-prod-data` |
| EU Vault | aws-010840394398-eu-tech-service | `jwt-eks-tech-service` |
| EU Vault | aws-740315635167-eu-prod | `jwt-eks-prod` |
| EU Vault | aws-769494896000-eu-data | `jwt-prod-data` |
| CN Vault | aws-589899215075-cn-tech-service | `jwt-eks-tech-service` |
| CN Vault | aws-741924744516-cn-prod | `jwt-eks-data-ops` |
| **CN Builder Vault** | aws-801447536674-cn-dev | `jwt-eks-dev` |
| CN Vault | aws-125710977284-sg-devops | `jwt-eks-sg-devops` |
| US Vault (external) | gcp-a4xcloud-tech-service-us | `jwt-gke-us-tech-service` |
| US Vault (external) | gcp-a4xcloud-p-us-us-prod | `jwt-gke-us-prod` |
| CN Vault | tencent-100014919455-cn-main | `kubernetes` (K8s Auth) |

> 表中 **「Vault」列即 Ops 域 vault**（`vault-{us-new,eu,cn}.addx.live`）。**「CN Builder Vault」= `vault-cn.builder.addx.live`**（`jwt-eks-dev` mount 建在这台，不是 Ops vault）。其余 Builder 域集群（`*-cn-staging` / `*-us-staging` / `*-eu-staging`）同理，JWT path 建在各自 **Builder Vault**（`vault-{cn,us,eu}.builder.addx.live`）上，path 按 `jwt-eks-<cluster>` 命名（如 cn-staging=`jwt-eks-cn-staging`）。**绝不在 Ops vault 上为 Builder 域集群建 `external-secrets` role。**

## 关键注意事项

### 通用

1. **部署顺序**: 准备 k8s overlay + argocd-apps child Apps → ESO → Vault Config → ArgoCD (手动) → 一次性 apply root-apps → Casdoor → OIDC 更新 → fleet/通知验收
2. **App of Apps**: root-apps 监听 `argocd-apps/{CLUSTER_DIR}`；`k8s/.../self-manage` 不是部署入口
3. **ArgoCD 自管理**: ArgoCD 首次需手动 helm install（bootstrap），之后由 `argocd-apps/{CLUSTER_DIR}/argocd.yaml` 自管理
4. **飞书应用**: 所有集群共用 `cli_a92fe00c8f789bd1`，每个集群在 Casdoor 创建独立 Application
5. **密码安全**: MySQL 和 admin 密码 20+ 字符，禁止跨集群复用
6. **PV 持久化**: MySQL 部署后立即将 PV reclaimPolicy 改为 Retain
7. **Casdoor 用户名**: 组织必须启用 `useEmailAsUsername`，否则中文飞书名导致 ASCII 校验失败
8. **DNS**: Casdoor/ArgoCD 域名 CNAME 在阿里云 DNS 配置

### EKS 特有

9. **CoreDNS**: rewrite 规则**禁止**使用 `answer auto`，EKS CoreDNS v1.11.x 不支持
10. **Harbor**: 每个集群有独立 Harbor（见集群表），Runner 自动挂载凭证并注入 `HARBOR_REGISTRY` 环境变量
11. **Crossplane CN 镜像**: provider 镜像已同步到 CN Harbor，CN EKS 集群使用本集群 Harbor 域名
13. **CN ARN**: 所有 ARN 使用 `arn:aws-cn:` 前缀

### TKE 特有

14. **Crossplane**: 使用 `cicd/base/tencent/crossplane` 和腾讯云 provider/post-install；不要套 AWS provider/IAM 前置条件
15. **Ingress 控制器**: 使用腾讯云 CLB annotation (`kubernetes.io/ingress.class: qcloud`)，不设 `ingressClassName`
16. **CLB 固定 IP**: 必须通过 `kubernetes.io/ingress.qcloud-loadbalance-id` 固定 CLB ID，防止同步时 IP 变更
17. **CoreDNS (TKE)**: CLB 返回 IP，CoreDNS 使用 `hosts` 插件而非 `rewrite name`
18. **存储类**: 使用 `cbs-topo`（腾讯云 CBS）
19. **Vault Auth (TKE)**: Vault 运行在同集群，使用 Kubernetes Auth 替代 JWT Auth
20. **Kyverno**: TKE K8s 1.28 使用 `cicd/base/tencent/` 中的 Kyverno 适配配置

### GKE 特有

21. **有 ArgoCD**: GKE 集群已有 `clusters/gcp-*/cicd/argocd/` 配置和对应 ArgoCD UI
22. **Crossplane**: 使用 `cicd/base/gcp/crossplane` 和 GCP 对应 post-install；不要套 AWS provider/IAM 前置条件
23. **ESO 独立 JWT path**: GKE 使用自己的 OIDC issuer，需要独立 JWT auth path

## 安全规则

1. **密码和密钥禁止硬编码**，运行时通过环境变量传入
2. **每次部署生成新密码**，禁止跨集群复用
3. **内部 ALB/CLB SG 只允许 VPC CIDR**，禁止 0.0.0.0/0
4. **外部 ALB SG 只允许办公网络**，使用已有 from-office SG
5. **所有 kubectl apply / helm install / helm upgrade 必须先展示命令，用户确认后执行**
6. **生产集群操作需额外谨慎**

## 示例

### Good — 为新 EU EKS 集群部署完整 CICD 栈

```
用户：帮我在 aws-999999999999-eu-new 集群部署 CICD 环境

AI：
1. 参考 aws-010840394398-eu-tech-service 目录创建新集群配置
2. 替换变量：Account ID、Region、OIDC Issuer、域名、Ingress 配置
3. 确认 JWT Auth path（EU Vault 上选一个未用的 path）
4. Bootstrap：手动 helm install ESO → Vault 配置 → ArgoCD
5. 在 argocd-apps 创建 AppProject + authoritative child Applications
6. 只 apply k8s/cicd/argocd/root-application.yaml，等待 root 和 child Applications Synced/Healthy
7. 部署 Casdoor：mysql.yaml → Casdoor ArgoCD App → 内部 ALB → CoreDNS → 初始化 → OIDC 配置
8. 验证 fleet scanner、readonly token/网络白名单和飞书三通知
9. 分别提交 k8s 与 argocd-apps MR，合并后 ArgoCD 完全接管
```

### Good — 为已有集群添加 Crossplane

```
用户：帮 aws-302571458622-us-prod 加上 Crossplane

AI：
1. 在 clusters/aws-302571458622-us-prod/cicd/ 创建 crossplane/ 目录
2. 参考 aws-002497567426-us-tech-service/cicd/crossplane/ 模板
3. 确认 AWS 账户中 CrossplaneProviderRole 是否已存在
4. 在 argocd-apps 创建 `crossplane.yaml`（`platform-control-plane`）
5. 在 argocd-apps 创建 `crossplane-post-install.yaml`（`platform-shared-infra`、prune=false、SSA、SkipDryRun）
6. 提交 MR 合并后由 ArgoCD 部署 operator、Providers 和 ProviderConfig；只读验证两个 Application 与 Providers
```

### Bad — 使用过时目录路径

```
# 错误：使用旧路径结构
eks/us-eks-tech-service/cicd/argocd/values.yaml
```

**问题**：k8s 仓库已重构，正确路径为 `clusters/aws-002497567426-us-tech-service/cicd/argocd/values-override.yaml`

### Bad — 手动 helm install 所有组件

```
# 错误：手动安装 kyverno、image-updater 等
helm install kyverno . -n kyverno --kube-context <ctx>
```

**问题**：除 ESO 和 ArgoCD 需要 bootstrap 外，其余组件通过 `argocd-apps` authoritative child Applications 由 ArgoCD GitOps 管理，不应手动 install。

### Bad — CoreDNS 使用 answer auto

```
rewrite name casdoor-eu.addx.live internal-xxx.eu-central-1.elb.amazonaws.com answer auto
```

**问题**：EKS CoreDNS v1.11.x 不支持 `answer auto`，导致 CrashLoopBackOff。

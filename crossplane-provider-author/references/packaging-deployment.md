# 打包 + 部署

> **何时读**：上线前；准备发第一个 release。

## safe-start capability（必须开）

Crossplane v2 默认要求 provider 声明 `safe-start` capability — "如果一些 CRD 没部署，相关 controller 不启动，但不要让整个 provider 进程退出"。provider-template 默认开了，你要做的只是：

1. 每个资源用 `SetupGated` 而不是 `Setup`：

```go
// internal/controller/<resource>/<resource>.go
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    o.Gate.Register(func() {
        if err := Setup(mgr, o); err != nil {
            panic(errors.Wrap(err, "cannot setup <Resource> controller"))
        }
    }, v1alpha1.<Resource>GroupVersionKind)
    return nil
}
```

2. `internal/controller/register.go` 调 Gated 版本：

```go
func SetupGated(mgr ctrl.Manager, o controller.Options) error {
    for _, setup := range []func(ctrl.Manager, controller.Options) error{
        config.Setup,           // ProviderConfig reconciler 不需要 gate（基础组件）
        datasource.SetupGated,  // 业务资源走 Gated
    } {
        if err := setup(mgr, o); err != nil { return err }
    }
    return nil
}
```

3. `package/crossplane.yaml`：

```yaml
apiVersion: meta.pkg.crossplane.io/v1alpha1
kind: Provider
metadata:
  name: provider-<name>
  annotations:
    meta.crossplane.io/maintainer: A4x Platform Team <devops@addx.ai>
    meta.crossplane.io/source: gitlab.addx.ai/DEV/provider-<name>
    meta.crossplane.io/license: Apache-2.0
    meta.crossplane.io/description: |
      Crossplane provider for managing <Service> resources via <API>.
spec:
  capabilities:
    - safe-start
```

provider 升级时 CRD 和 controller binary 很难原子部署。没 safe-start，CRD 少装一个、整个 provider CrashLoopBackoff，影响已有资源。

## 发布与部署：5 步流程

参考实现：[provider-ninedata v0.1.6 release](https://gitlab.addx.ai/DEV/provider-ninedata)（2026-04-28 全链路上线）。

```
1. git tag v0.1.X + push
   ↓ provider 仓库 .gitlab-ci.yml (tag-triggered)
2. SG Harbor:harbor-12571-sg-devops.addx.live/base/provider-<name>:v0.1.X (multi-arch)
   ↓ DEV/base-images internal-images.yaml 加一行 → MR → 合并
3. 目标集群 Harbor 都有这个镜像（通过 base-images `internal-images.yaml` targets 精确 fanout）
   ↓ DEV/k8s 仓库改 clusters/*/cicd/crossplane/post-install/providers.yaml 升 tag
4. MR 合并
   ↓ providers.yaml 是 self-managed (不在 ArgoCD 纳管，README 明确说明)
5. 手 kubectl apply 到 6 集群（或更多，看 provider 装在哪）
```

### Step 1 — provider 仓库 `.gitlab-ci.yml`（tag-triggered）

照搬 [DEV/crossplane-provider-grafana](https://gitlab.addx.ai/DEV/crossplane-provider-grafana/-/blob/main/.gitlab-ci.yml) / [DEV/provider-ninedata](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/.gitlab-ci.yml) 的 3 阶段模板：

| Stage | Job | Runner tag | 产出 |
|---|---|---|---|
| build | build-binary-{amd64,arm64} | sg-amd64 | bin/linux_<arch>/provider |
| runtime | build-runtime-{amd64,arm64} | sg-{amd64,arm64} | runtime-<arch>.tar (kaniko 多架构 native build) |
| package | build-push-xpkg | sg-amd64 | per-arch xpkg push + crane 拼 multi-arch manifest list |

**为什么要 stitch manifest 而不是单条 `crossplane xpkg push` 出 multi-arch**：crank v2.2.0 的 `--embed-runtime-image-tarball` 是 `xor:"runtime-image"`，单次只接受一个 tarball——所以分别 build amd64 / arm64 xpkg 推上去，再用 `crane index append` 拼一个 manifest list 当 canonical `:vX` tag。

**触发**：tag push 匹配 `^v[0-9]+\.[0-9]+\.[0-9]+$`（如 `v0.1.6`）。Branch / MR push 不跑 build——只跑 group-level compliance（credentials-scan / sonarqube）。

**认证**：`/kaniko/.docker-secret/config.json` runner-pod 自动挂载，已经预置 SG Harbor 凭据。**不需要任何 CI variable**。

⚠️ **不要加 top-level `workflow.rules`** 限制只在 tag 跑——会同时 starve compliance 工具链。改用每个 job 个体 rule（参考 provider-ninedata 的 `.tag-only` 模板）。

### Step 2 — 早期 Makefile（保留，但本地开发用）

```makefile
# Setup XPKG
XPKG_REG_ORGS ?= harbor-12571-sg-devops.addx.live/base
XPKG_REG_ORGS_NO_PROMOTE ?= harbor-12571-sg-devops.addx.live/base
XPKGS = provider-<name>
-include build/makelib/xpkg.mk

# 强制先 build image 再 build xpkg
xpkg.build.provider-<name>: do.build.images
```

本地：

```bash
make build              # 编译 binary 进镜像
make xpkg.build         # 把镜像和 CRDs 打包成 .xpkg OCI artifact
# make xpkg.push 一般不用 — 走 CI 即可
```

### Step 3 — base-images 仓库扇出

合并 [DEV/base-images](https://gitlab.addx.ai/DEV/base-images) 的 `internal-images.yaml` 加一条：

```yaml
- repo: base/provider-<name>
  tag: v0.1.X
  targets: [us-tech, us-prod, us-data, eu-tech, eu-prod, eu-data]   # 按实际部署集群填写；需要 staging 就显式加 staging target
```

`targets` 限定到这个 provider 实际部署的集群（避免 sync 到没用的 Harbor）。合并 MR 后，base-images sync pipeline 自动从 SG Harbor 拉到目标 Harbor；合法 target 名称以 `DEV/base-images` README / `.gitlab-ci.yml` 为准。

### Step 4 — k8s 仓库：post-install/ 文件结构

⚠️ **关键事实**：这个 post-install/ 目录**不在 ArgoCD 纳管**（当前说明在 `cicd/base/default/crossplane/README.md` 及各集群 crossplane post-install 文档中维护）。MR 合并后必须人手 `kubectl apply`。

每集群一份 `clusters/<cluster>/cicd/crossplane/post-install/providers.yaml`，里面汇总所有 Provider（aws-* / ninedata / cloudflare / oci / ...）。给你的 provider 加一个块：

```yaml
---
apiVersion: pkg.crossplane.io/v1
kind: Provider
metadata:
  name: provider-<name>
spec:
  package: harbor-XXXX.addx.live/base/provider-<name>:v0.1.X   # 各集群自己的 Harbor URL
  packagePullPolicy: IfNotPresent
  packagePullSecrets:
    - name: harbor-registry-secret
  revisionHistoryLimit: 1
  runtimeConfigRef:
    apiVersion: pkg.crossplane.io/v1beta1
    kind: DeploymentRuntimeConfig
    name: aws-irsa-private    # 复用现有的 RuntimeConfig；不要新建 ControllerConfig（已废弃）
```

> ⚠️ **`ControllerConfig` 已废弃**（Crossplane v1.x 老 API）。v2 走 `pkg.crossplane.io/v1beta1.DeploymentRuntimeConfig`。各集群 post-install/ 里都有现成的，复用 `aws-irsa-private`（私网 + IRSA）即可，不要新建。

ProviderConfig 一般也已经存在（`provider-config.yaml` / `clusterproviderconfig-access.yaml`），只有第一个引入新 vendor 的 provider 才需要新增。

### Step 5 — 手工 apply 到目标集群

```bash
for ctx in us-eks-tech-service us-prod arn:...:cluster/eu-eks ...; do
  kubectl --context "$ctx" apply -f clusters/<对应集群目录>/cicd/crossplane/post-install/providers.yaml
done
sleep 30
# 验证：
for ctx in ...; do
  kubectl --context "$ctx" get providerrevision | grep <name>
  # 应看到新 revision Active=True，旧 revision Inactive
done
```

### 凭据：Vault → ProviderConfig

通过 vault-kv-manager skill 把 JSON 写到 `secret/cicd/<provider>/api-credentials` 的 `credentials.json` key：

```json
{
  "endpoint": "https://api.example.com",
  "access_key_id": "AK...",
  "access_key_secret": "SK..."
}
```

ESO ExternalSecret 已经在各集群 `crossplane-system` namespace 配好（参考 ninedata-api-credentials 范本）。

## 网络出口必须走 NAT

很多 SaaS 做了 IP 白名单。Provider controller 的 pod 必须调度到稳定 NAT 出口的 nodepool（参考 Codex 全局 `AGENTS.md` 网络安全红线 #1，以及 k8s docs/plans 里的 `nat-egress` 设计）：

1. `DeploymentRuntimeConfig` 里加 `nodeSelector: cluster.addx.io/nodepool: nat-egress`（或复用 `aws-irsa-private` — 已经在私网 + 走 NAT）
2. 配置完成后必须在 pod 内验证：`kubectl exec <pod> -n crossplane-system -- curl checkip.amazonaws.com`，与 `aws ec2 describe-nat-gateways` 返回的 PublicIp 交叉核对
3. 把 NAT EIP 加入对端系统的 IP 白名单

**禁止**用节点动态公网 IP 当白名单（节点重建即失效）。

## 验证清单

每次 release 后跑一遍：

1. `kubectl get providerrevision | grep <name>` — 新 tag revision Active=True，旧 revision Inactive
2. `kubectl get pods -n crossplane-system -l pkg.crossplane.io/provider=provider-<name>` — pod READY 1/1, RESTARTS=0
3. `kubectl get clusterproviderconfig <name>-default` — 存在
4. apply 一个测试 CR，`kubectl describe <kind> <name>` 看 Conditions: `Ready=True`, `Synced=True`
5. 任选一个存量 CR 看 `LastReconcileTime`，应在 1 分钟内更新过

> ⚠️ **新仓库** GitLab `shared_runners_enabled` 默认关闭，需手动翻开关，否则首次 tag 触发 pipeline 卡 pending（详见 MEMORY 里的 "新建 GitLab 仓库 CI runner 坑" 条目）。

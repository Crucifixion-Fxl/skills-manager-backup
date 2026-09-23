# 脚手架：从 provider-template 起步

> **何时读**：第一次拉起一个新 provider 仓库时。

## 为什么不要从零写

Crossplane 官方 `crossplane/provider-template` 把 90% 样板（Makefile / build/ submodule / angryjet codegen / safe-start gate / ProviderConfig CRD / CI）做好了，你只需要填"你的资源类型 + 你的 client"。从零搭 Crossplane runtime 接入（scheme / usage tracker / rate limiter / leader election / safe-start gate / metric recorder）出错概率极高且价值为零；provider-template 每个版本都在跟 crossplane-runtime 同步，直接继承最省心。

## 起步流程

```bash
# 1. Clone provider-template 到新仓库
git clone https://github.com/crossplane/provider-template.git provider-<name>
cd provider-<name>
git submodule update --init --recursive   # build/ submodule 必装

# 2. 一键替换所有 "template" 字样
make provider.prepare provider=YourProviderName
# 这会替换 go module 名、Go struct 名、CRD group、Makefile 变量
# 注意：只能跑一次，要重跑请 git stash/reset

# 3. 加资源类型（生成 apis/ + internal/controller/ 骨架）
make provider.addtype provider=YourProviderName group=yourgroup kind=YourResource
# 然后在 apis/yourgroup.go 和 internal/controller/register.go 里手动注册

# 4. 生成 CRD YAML + deepcopy + managed.go 接口
make generate
```

## 项目目录速览（provider-ninedata 实际布局）

```
provider-<name>/
├── apis/                            # CRD Go 类型（kubebuilder 生成 CRD YAML）
│   ├── <name>/v1alpha1/
│   │   ├── <resource>_types.go      # ForProvider / Observation / Spec / Status
│   │   ├── groupversion_info.go     # +groupName=<name>.crossplane.io
│   │   ├── doc.go
│   │   └── zz_generated.*.go        # angryjet + controller-gen 产物（禁手编辑）
│   ├── v1alpha1/                    # ProviderConfig 类型（一般不用改）
│   │   ├── types.go
│   │   └── zz_generated.*.go
│   └── <name>.go                    # AddToScheme 注册所有 API group
├── internal/
│   ├── clients/<name>/              # 外部系统 SDK（自己写的 HTTP client）
│   │   ├── client.go                # 签名 + do() 通用封装
│   │   ├── <resource>.go            # 对应资源的 Create/List/Delete 方法
│   │   └── client_test.go
│   └── controller/
│       ├── register.go              # SetupGated 注册所有 controller
│       ├── config/config.go         # ProviderConfig reconciler（一般不用改）
│       └── <resource>/
│           ├── <resource>.go        # ExternalClient：Observe/Create/Update/Delete
│           └── <resource>_test.go   # 用 httptest 搭 mock 外部 API
├── cmd/provider/main.go             # controller-manager 入口
├── package/
│   ├── crossplane.yaml              # Provider 元数据 + capabilities (safe-start)
│   └── crds/                        # controller-gen 生成的 CRD YAML（禁手编辑）
├── examples/                        # 样例 CR（ProviderConfig + 资源）
├── hack/helpers/                    # addtype.sh 脚手架（从 template 继承）
└── Makefile                         # 继承 build/ submodule
```

## 三种"类型"不要搞混

| 类型 | 归谁管 | 例子 |
|------|--------|------|
| **ProviderConfig** (package 自带) | Crossplane runtime + 你的 `config/config.go` | `kind: ProviderConfig` / `kind: ClusterProviderConfig`，持有外部系统的认证 |
| **Managed Resource** (你加的) | 你的 `<resource>/<resource>.go` | `kind: DataSource` / `kind: PrometheusRule`，映射外部资源 |
| **ProviderConfigUsage** (runtime 自动) | Crossplane runtime | 记录"哪个 MR 正在用哪个 PC"，用于删除保护 |

## 端到端数据流

```
用户 apply CR
  ↓
controller-runtime watch → 调 ExternalClient.Observe
  ↓
Observe 决定：ResourceExists? ResourceUpToDate?
  ↓
不存在 → Create；存在但不一致 → Update；要删除 → Delete
  ↓
每次 reconcile 结束，Status.Conditions（Ready/Synced）写回 CR
  ↓
Crossplane 默认 poll 1m 一次，sync 1h 全量对齐
```

## 新仓库踩坑（非脚手架）

- **shared_runners_enabled 默认关**：新建 GitLab 仓库后必须先翻开关，否则 CI 卡 pending（MEMORY 里有专门条目）
- **Argocd-deploy Reporter**：仓库要给平台 deploy token Reporter 角色，否则 ArgoCD 拉不到 source
- **必须建在 group 下**（`DEV/` / `EM/` …），不要用个人 namespace

## 何时不用 provider-template

- 你只有 1 个资源类型且全部用 K8s API（不调外部 HTTP） → 直接写 controller-runtime 项目即可
- 你想从 Terraform schema 自动生成 → 用 [upjet](https://github.com/crossplane/upjet)（learning curve 较陡）

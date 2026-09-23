# cluster-access

开发者通过飞书 SSO 直接接 staging Kubernetes 集群跑 `kubectl` 的参考专题。

这个目录只回答"开发者如何拿到 staging 只读 + debug 的 kubectl
访问能力"。它不是部署 workflow，不生成 YAML，也不替代
`troubleshooting/` playbook。

## 文件分工

| 文件 | 内容 |
|---|---|
| `clusters.yaml` | 已接入 cluster 的事实表：wrapper 名称、namespace、region、权限边界 |
| `staging-kubectl.md` | 通用接入流程：网络前置、安装、登录、验证、排障 |

## 使用边界

- 仅 staging 集群。prod kubectl 永远走 IAM/admin path，不给开发者通过该
  wrapper 自助接入。
- 已认证的授权组在已启用 staging cluster 的 `staging-*` namespace 内有 read + debug：
  get/list/watch、logs（含 `--previous`）、exec、port-forward、attach。业务资源变更仍走 GitOps MR。
  exec 等 debug 操作可修改应用运行状态，具体执行仍须本次任务授权，不能当作纯只读或 Secret 读取通道。
- 不授予 `get secrets`、写资源、删 Pod、非 `staging-*` namespace 或
  cluster-scoped 权限。
- 通用权限边界见 `../data/permission-boundaries.yaml`；本目录是其中 staging read/debug
  条件例外的事实来源。平台 CRD/Provider/Composition 等只读检查仍需已授权 ops 身份。
- wrapper 仓 [DEV/addx-cluster-login](https://gitlab.addx.ai/DEV/addx-cluster-login)
  是 kubeconfig 生成和 OIDC client 配置的来源；本目录只保存 skill 需要的稳定事实。

## 如何扩展新 staging 集群

新增集群时只做三处：

1. `DEV/addx-cluster-login` 加 `clusters/<cluster>.yaml`。
2. `DEV/k8s` 对应 cluster overlay 接入 `oidc-dev-rbac`，复用
   `cicd/base/default/oidc-dev-rbac/` ClusterRole，并用 Kyverno post-install
   generate policy 给 `staging-*` namespace 生成 RoleBinding。
3. 本目录 `clusters.yaml` 加一行事实表。

不要复制一份新的长篇 `<cluster>-kubectl.md`。接入步骤必须保持在
`staging-kubectl.md` 一处，集群差异只进 `clusters.yaml`。

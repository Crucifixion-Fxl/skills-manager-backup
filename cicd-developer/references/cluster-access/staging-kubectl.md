# Staging kubectl 接入

当开发者询问如何用 `kubectl` 接 staging Kubernetes，或 troubleshooting
需要开发者自助提供 staging namespace 的只读证据时，使用本参考。

支持哪些 cluster、默认 namespace 和 RBAC namespace pattern 是什么，先查
`clusters.yaml`。不要从 region 名凭印象推 cluster 名。

## 前置条件

- 目标 cluster 在 `clusters.yaml` 中存在，且 `status: enabled`。
- 开发者在 A4x 办公网，或已连接办公 VPN。
- 开发者的飞书 / Casdoor 账户在 `clusters.yaml -> auth_contract.required_group`
  指定的组内。
- 本机已安装 `kubectl` 和 `kubelogin >= 1.32.0`；wrapper 会检查
  `get-token --help` 是否支持 `--oidc-pkce-method`，并显式传入 `S256`。
- GitLab SSH key 或 Git credential helper 能读取私有仓库 `DEV/addx-cluster-login`。

任一条件不满足，先停止并告诉用户缺哪个前置条件。

## 安装工具

macOS:

```bash
brew install kubectl kubelogin
```

Linux:

```bash
kubectl krew install oidc-login
```

如果没有 krew，也可以从 kubelogin upstream release 页面安装。

安装 wrapper：该 GitLab 仓库是 private；匿名 raw URL 会返回登录页 HTML，
不能使用 `curl | bash`。先使用已有的 `~/Project/A4x/addx-cluster-login` checkout，
确认来源和本地改动后将 installer 更新到获准的 `main`；不要覆盖已有改动。
只有本地仓库不存在时，才通过已配置的 Git 认证 clone 到该目录。

```bash
bash "$HOME/Project/A4x/addx-cluster-login/install.sh"
```

installer 自身通过认证 Git 拉取 `main` 的脚本和 cluster 配置。安装失败时修复本地
Git 认证或网络，不改用匿名 raw 下载，也不把 token 放进 URL。

如果命令装到了 `~/.local/bin`，确认该目录在 `PATH` 中：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 登录

列出当前 wrapper 支持的 cluster:

```bash
addx-cluster-login --list
```

登录一个已启用 cluster:

```bash
addx-cluster-login <cluster>
```

示例：

```bash
addx-cluster-login us-staging
addx-cluster-login eu-staging
addx-cluster-login cn-staging
```

当前 wrapper 显式使用 `--skip-open-browser`：复制终端打印的登录 URL 到浏览器，
走 Casdoor 和飞书 SSO，完成本机 localhost callback 后缓存 OIDC token。
headless / WSL 场景也使用该 URL；不要等待 wrapper 自动弹出浏览器。
Token TTL 见 `clusters.yaml`；TTL 内正常 `kubectl` 命令通常直接使用缓存。

## 验证

使用 `clusters.yaml -> clusters.<cluster>.kubectl_smoke` 里的 smoke 命令。默认
namespace 只是 smoke / context 默认值；实际开发者 RBAC 覆盖同一 staging
cluster 内所有 `staging-*` namespace。

示例：

```bash
kubectl get pods -n staging-us
kubectl get pods -n staging-eu
kubectl get pods -n staging-cn
```

然后在目标 `staging-*` namespace 内验证常用只读 / debug 流程：

```bash
kubectl -n staging-<app-or-team> get deploy
kubectl -n staging-<app-or-team> get pods
kubectl -n staging-<app-or-team> get events --sort-by=.lastTimestamp
kubectl -n staging-<app-or-team> logs <pod> --tail=200
kubectl -n staging-<app-or-team> exec -it <pod> -- sh
kubectl -n staging-<app-or-team> port-forward svc/<service> 8080:8080
kubectl -n staging-<app-or-team> get externalsecret
kubectl -n staging-<app-or-team> get rollouts
```

不要建议 `kubectl apply`、`kubectl patch`、`kubectl delete pod` 或
`kubectl get secret`。这些都超出开发者访问契约。

## 权限契约

允许：

- 在同一 cluster 的所有 `staging-*` namespace 内对业务运行态资源
  `get/list/watch`
- 用 `logs`（含 `--previous`）、`exec`、`attach`、`port-forward` 做 debug
- debug 不等于纯只读；`exec` 可以改变应用运行状态，具体命令仍须本次任务授权，
  不能借此修改部署或提取 Secret
- 查看 ExternalSecret / Rollout / Ingress 状态，但不能读取 Secret 值

禁止：

- 读取 Kubernetes Secret 数据
- 写入或删除业务资源
- 通过删除 Pod 重启业务
- 访问 `default`、`kube-system`、`argo-cd` 或其他非 `staging-*` namespace
- list nodes / namespaces、读取 CRD/Provider/Composition 等平台/cluster-scoped 资源，或修改 RBAC

如果用户需要禁止项，把请求转到对应 GitOps、ArgoCD、Vault 或 ops 路径。
不要临时扩展这套流程。

## 排障

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `addx-cluster-login: command not found` | `~/.local/bin` 不在 `PATH` | 把 `export PATH="$HOME/.local/bin:$PATH"` 加到 shell rc |
| `kubectl` connection timeout | 不在办公网 / 未连 VPN | 连接办公 VPN 后重试 |
| 浏览器没有自动打开 | wrapper 默认 `--skip-open-browser` | 打开终端打印的 URL，并保持本机 callback listener 运行 |
| `kubelogin does not support --oidc-pkce-method` | kubelogin 版本过旧 | 升级到 >= 1.32.0 后重跑，不关闭 PKCE |
| 在 `default` namespace `Forbidden` | 权限只覆盖 `staging-*` namespace | 使用 `clusters.yaml` 中的默认 namespace，或显式 `-n staging-...` |
| 对 `secrets` `Forbidden` | Secret 读取被刻意排除 | 改用批准的 Vault/UI 路径 |
| `staging-*` namespace 内 `User <email> cannot ...` | 缺 Casdoor 组、token stale，或 namespace 不匹配 `staging-*` | 找 ops 核对组；确认 namespace；清 `~/.kube/cache/oidc-login/` 后重登 |
| `oidc-login is not found` | kubelogin 未安装 | 安装 kubelogin 后重跑 wrapper |

上报失败时带上：

- `clusters.yaml` 中的 cluster key
- 完整命令
- 完整错误文本
- 本机是否在办公网 / VPN
- `kubectl config current-context`

## 来源

本参考按 [addx-cluster-login README 与脚本](https://gitlab.addx.ai/DEV/addx-cluster-login/-/tree/f62f608f998dfd88dc17de35ea26d92da1f7b4c7)
校对；这里只证明仓库接入合同，不代表所有 endpoint 当前可用。CN wrapper 仍指向
AWS `cn-eks-staging`，不要把同名 region 或入口域名当作腾讯云 staging 的访问授权。

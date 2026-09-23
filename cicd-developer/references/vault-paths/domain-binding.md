# Vault 域与集群 tier 绑定规则

**TL;DR**：cluster `domain` → vault `domain` 是 **1:1 硬绑定**，workflow 不要重新发明。tech-service tier **一律** ops 域且不访问 builder Vault；builder 域 dev/staging 集群 **一律**使用 `vault-builder-backend` 且不访问 ops Vault。`cn-k8s` / TKE 是 ops 域特例：部署在 TKE 的服务即使 env=staging，也使用 ops Vault。

## 绑定表

| domain / tier (clusters.yaml) | vault domain | default `vault_css` | 实例 |
|---|---|---|---|
| ops (prod / data) | ops | `vault-backend` | `vault-{us,eu,cn}-{new,internal*}.addx.live` |
| ops (tech-service) | **ops** | `vault-backend` | 同上 |
| ops (cn-k8s / TKE, including staging workloads) | **ops** | `vault-backend` | `vault-cn-internal.addx.live` |
| builder (cn-eks-dev) | builder | `vault-builder-backend` | `vault-cn-internal.builder.addx.live` |
| builder (staging us/eu/cn) | builder | `vault-builder-backend` | `vault-{us,eu,cn}.builder.addx.live` |
| cicd-infra (sg-devops) | 自管 | — | n/a |

`clusters.yaml` 中 `domain` / `vault` / `vault_css` 字段就是答案；workflow 不要凭 cluster 名字、region 或 `ClusterSecretStore` 名字推断。

## 为什么 tech-service 是 ops 域（不是 builder）

虽然 tech-service 集群上**运行**着 vault-builder server，但它**不消费** builder 域的密钥。三个理由：

1. **职责边界**
   - builder vault = CI/CD 域应用密钥（runner、image-updater、staging/dev 业务 app）
   - ops vault = 平台 / 生产运维域
   - tech-service 跑的是平台基础设施层（ArgoCD / Casdoor / Harbor / cicd-portal / Kyverno / …），密钥归 ops。

2. **循环依赖风险**
   - vault-builder server 本体就部署在 tech-service。
   - 如果 tech-service 上的平台应用反过来读 builder vault 做启动 secret → vault-builder server 挂了，tech-service 平台应用也起不来，恢复路径双层卡死。
   - ops vault 是独立部署，无此耦合。

3. **权限模型隔离**
   - builder 域 OIDC role 通常给 staging/dev 用户 self-service。
   - 平台密钥（Casdoor OIDC client、GitLab bot token、Harbor admin、ArgoCD RBAC secret）不该跟开发用户共面。

## CN 特殊性

CN 区域的默认 `staging-cn` 已路由到独立 builder staging 集群 `cn-eks-staging`，普通 dev/staging 应用从 `vault-cn.builder.addx.live` 读。restricted-admin prod 仍跑在 `cn-eks-tech-service` 同账户的 `*-prod` namespace；这**不改变** tech-service 的 domain 归属：它仍然是 ops 域，平台栈密钥（cicd-portal 等）从 `vault-cn.addx.live` 读，不读 `vault-cn.builder.addx.live`。

`cn-k8s` / TKE 是显式例外。历史或指定部署在 `cn-k8s` 的服务，即使命名为 staging，也跟随 `cn-k8s` 的 ops domain：`secretStoreRef.name: vault-backend`，Vault server 指向 `vault-cn-internal.addx.live`。不要把这类 TKE staging workload 迁到 `vault-builder-backend`，除非先把应用迁出 TKE 到 `cn-eks-staging`。

`cn-eks-dev` 与 `cn/us/eu-eks-staging` 属于 builder 域。

## workflow 自检

写或审 ExternalSecret / PushSecret 模板时：

- ✅ 普通 app 先查 `references/data/clusters.yaml -> clusters[].vault_css`
- ✅ `secretStoreRef.name: vault-backend` 只用于 ops / tech-service 域，实际 server 指向 ops Vault
- ✅ `secretStoreRef.name: vault-builder-backend` 只用于 builder 域 dev/staging，实际 server 指向 builder Vault
- ✅ 明确部署到 `cn-k8s` / TKE 的 staging workload 使用 `vault-backend`，这是 ops 域例外
- ⚠️ 在 builder dev/staging 集群 ExternalSecret / PushSecret 看到 `secretStoreRef.name: vault-backend` → 规则违反，迁到 `vault-builder-backend`
- ⚠️ 在 tech-service 集群目录下看到 `cluster-secret-store-builder.yaml` → 历史包袱，应清理（不再渲染）
- ⚠️ 在 tech-service 集群 ExternalSecret 看到 `secretStoreRef.name: vault-builder-backend` → 规则违反，迁到 `vault-backend`
- ⚠️ 判断是否走 ops/builder 域时，看 `clusters[].vault` 或 live `ClusterSecretStore.spec.provider.vault.server`，不要只看名字 `vault-backend`

## 历史决策

- 2026-05-25 — 从 us/eu/gcp tech-service 清理 `vault-builder-backend` ClusterSecretStore（0 消费方），从 cn-tech-service 把 cicd-portal-next 的 2 个 ExternalSecret 迁到 ops vault。clusters.yaml `cn-eks-tech-service` 的 `domain` 从 `builder` 改成 `ops`。
- 2026-05-25 — cn-dev 默认 `vault-backend` 曾切到 `vault-cn-internal.builder.addx.live`；随后 builder 域统一迁到 `vault-builder-backend`，dev/staging 不再使用 `vault-backend`。
- 2026-05-25 — us/eu/cn staging 加入 builder 域事实表；`staging-*` 环境路由从 tech-service 切到 `*-eks-staging`，dev/staging 不再访问 ops Vault。
- 2026-05-25 — 明确 `cn-k8s` / TKE 是 ops 域特例：TKE 上的 staging workload 仍使用 ops Vault `vault-backend`。

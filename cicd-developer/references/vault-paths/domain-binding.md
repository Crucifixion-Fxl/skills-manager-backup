# Vault 域与集群 tier 绑定规则

应用跟随精确目标集群的 Vault 域。`clusters.yaml` 的 `domain` / `vault` / `vault_css`
是查表入口；验证 live `ClusterSecretStore.spec.provider.vault.server`，不能只按 CSS 名称猜域。

| 当前目标 | Vault 域 | CSS | server |
|---|---|---|---|
| AWS/GCP prod、data、tech-service | ops | vault-backend | 对应区域 ops Vault |
| AWS US/EU staging | builder | vault-builder-backend | vault-{us,eu}.builder.addx.live |
| TKE cn-main，100014919455 | ops | vault-backend | vault-cn-internal.addx.live |
| TKE cn-tech-service，100052802231 | ops | vault-backend | https://vault-cn.addx.live |
| TKE cn-staging，100052802231 | builder/staging | vault-backend | http://vault-active.vault.svc:8200 |

当前 CN staging 使用集群内 Vault；同名 `vault-backend` 不代表 ops 域，不应机械重命名
成 `vault-builder-backend`。tech-service 不读取 builder 密钥。旧 AWS CN 全部退役，
原 builder CSS / endpoint 只供历史排障，不能作为当前 CN staging 的默认值。

历史 cn-main staging 工作负载仍跟随其源集群 ops Vault，路径首段保持 staging。
新 `staging-cn` / `staging-cn-tke` 路由均到 100052802231 独立 staging；路由更新不授权
自动移动旧工作负载或凭据。迁移必须分别验证源/目标身份、writer/reader 和实际 Vault。
Sentry DSN 还需核验 `references/sentry/README.md` 中 Job 写入与 CSS 读取为同一实例。

来源与新集群能力边界见 [CN 迁移证据](../cn-tencent-migration.md)。

## 历史决策

- 2026-05-25 — 从 us/eu/gcp tech-service 清理 `vault-builder-backend` ClusterSecretStore（0 消费方），从 cn-tech-service 把 cicd-portal-next 的 2 个 ExternalSecret 迁到 ops vault。clusters.yaml `cn-eks-tech-service` 的 `domain` 从 `builder` 改成 `ops`。
- 2026-05-25 — cn-dev 默认 `vault-backend` 曾切到 `vault-cn-internal.builder.addx.live`；随后 builder 域统一迁到 `vault-builder-backend`，dev/staging 不再使用 `vault-backend`。
- 2026-05-25 — us/eu/cn staging 加入 builder 域事实表；`staging-*` 环境路由从 tech-service 切到 `*-eks-staging`，dev/staging 不再访问 ops Vault。
- 2026-05-25 — 明确 `cn-k8s` / TKE 是 ops 域特例：TKE 上的 staging workload 仍使用 ops Vault `vault-backend`。

- 2026-09-28 — AWS CN 退役；当前独立 CN staging/tech-service 使用腾讯云 100052802231。历史 CSS 统一命名规则不覆盖新 staging。

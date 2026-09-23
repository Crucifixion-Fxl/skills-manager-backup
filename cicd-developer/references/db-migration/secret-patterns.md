## 1. Secret 设计：三种正确模式 + 两种禁忌

migration Job 和主应用都需要从 K8s Secret 读 DB 连接信息。**Secret 怎么从 Vault 同步过来**是这件事的关键，下面列所有合法/非法模式。

先确认凭据生产者的字段契约：app-owned RDS PushSecret 使用大写
`DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`；shared `kind: Database` Composition 使用
小写 `host`/`port`/`username`/`password`。shared 场景用
`recipes/k8s/shared-db-external-secret.yaml.tmpl` 显式映射，不能照搬 app-owned 模板。

下面示例里的 `{{cluster_secret_store}}` 一律从目标集群
`references/data/clusters.yaml -> vault_css` 填。ExternalSecret 的
`remoteRef.key` / `dataFrom.extract.key` **不带 `secret/` 前缀**；ClusterSecretStore
已经挂 `path: secret`。

### ✅ 模式 A（推荐）：单 ExternalSecret + dataFrom.extract

最简单、最少出错。所有 keys 都从两个（或多个）Vault 路径一次性 extract 出来，写到一个 Secret 里。

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-secret
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: {{cluster_secret_store}}
  target:
    name: my-app-secret
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: staging/app/my-app/config        # 拉 OIDC_*, JWT, ...
    - extract:
        key: staging/rds/application/my-app/database   # 仅适用于路径本身已存应用需要的 key 名
```

> `dataFrom.extract` 会把 Vault 路径下所有字段平铺成 K8s Secret 的 key。如果两个 Vault 路径有同名 key 后者覆盖前者，所以 Vault 路径设计上要避免 key 名冲突。
> shared `kind: Database` 路径存的是小写四字段；应用需要大写 `DB_*` 时不得用
> `dataFrom.extract`，改用下面模式 B 的显式映射。
>
> ⚠️ **Vault 路径必须预存在** — 即使该路径下所有 key 都是可选的（例如只放 `SENTRY_DSN` 一个可选变量），`dataFrom.extract` 引用时 Vault 路径**本身必须存在**，否则 ExternalSecret 会 `SecretSyncedError: Secret does not exist` 并阻塞 Pod 启动（Pod 会 `CreateContainerConfigError`）。Bootstrap 责任：开发者在 MR 描述里列出所有 `dataFrom.extract` 引用的 Vault 路径，运维至少写入一个 placeholder key（如 `SENTRY_DSN=""`）让路径存在；后续真要启用时直接覆盖值即可。对比 `data[].remoteRef` 模式：单 key 引用若 Vault 里该 key 不存在会 SecretSyncedError，但路径不存在的行为是"Secret does not exist"，错误消息更模糊、排查更慢。

migration Job 和 Rollout 都用同一个 envFrom：

```yaml
envFrom:
  - secretRef:
      name: my-app-secret
```

### ✅ 模式 B：两个独立 Secret，各 Owner，envFrom 引两次

适合 app 密钥和 db 密钥**生命周期不同**的场景（例如 db 密钥由 Crossplane 自动 rotate，app 密钥由人工维护）。

```yaml
# external-secret-app.yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-app-secret
spec:
  refreshInterval: 1h
  secretStoreRef: { kind: ClusterSecretStore, name: {{cluster_secret_store}} }
  target:
    name: my-app-app-secret
    creationPolicy: Owner
  data:
    - secretKey: OIDC_ISSUER_URL
      remoteRef: { key: staging/app/my-app/config, property: OIDC_ISSUER_URL }
    - secretKey: OIDC_CLIENT_ID
      remoteRef: { key: staging/app/my-app/config, property: OIDC_CLIENT_ID }
    - secretKey: SERVICE_JWT_HMAC_SECRET
      remoteRef: { key: staging/app/my-app/config, property: SERVICE_JWT_HMAC_SECRET }
---
# external-secret-db.yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app-db-secret
spec:
  refreshInterval: 1h
  secretStoreRef: { kind: ClusterSecretStore, name: {{cluster_secret_store}} }
  target:
    name: my-app-db-secret
    creationPolicy: Owner
    template:
      engineVersion: v2
      data:
        DB_HOST: "{{ .DB_HOST }}"
        DB_PORT: "{{ .DB_PORT }}"
        DB_USER: "{{ .DB_USER }}"
        DB_PASSWORD: "{{ .DB_PASSWORD }}"
        DB_NAME: "my_app"   # 字面量，不在 Vault 中
  data:
    - secretKey: DB_HOST
      remoteRef: { key: staging/rds/application/my_app/database, property: host }
    - secretKey: DB_PORT
      remoteRef: { key: staging/rds/application/my_app/database, property: port }
    - secretKey: DB_USER
      remoteRef: { key: staging/rds/application/my_app/database, property: username }
    - secretKey: DB_PASSWORD
      remoteRef: { key: staging/rds/application/my_app/database, property: password }
```

上例是 shared `kind: Database`，其中 Vault app 段必须等于 `spec.app`；应用名含连字符时
`spec.app` 使用下划线。app-owned RDS 则继续使用 `db-external-secret.yaml.tmpl` 的大写 remote
property。

migration Job 和 Rollout 都用两次 envFrom：

```yaml
envFrom:
  - secretRef:
      name: my-app-app-secret
  - secretRef:
      name: my-app-db-secret
```

### ✅ 模式 C（仅当不需要 db 时）：单 Owner ExternalSecret + 显式 data 列表

适合**没有数据库**或者只需要少量字段的应用。和模式 A 区别是用 `data` 列表显式列出每个字段，而不是 `dataFrom.extract` 整把拉。

```yaml
spec:
  target:
    name: my-app-secret
    creationPolicy: Owner
  data:
    - secretKey: API_KEY
      remoteRef: { key: staging/app/my-app/config, property: API_KEY }
    - secretKey: WEBHOOK_SECRET
      remoteRef: { key: staging/app/my-app/config, property: WEBHOOK_SECRET }
```

### ❌ 反模式 D：多个 ExternalSecret 写同一个 target Secret（**禁止**）

```yaml
# external-secret-app.yaml
spec:
  target:
    name: my-app-secret           # ← 同一个 target
    creationPolicy: Owner
  data: [API_KEY, ...]
---
# external-secret-db.yaml
spec:
  target:
    name: my-app-secret           # ← 同一个 target
    creationPolicy: Merge          # ← Merge 写入"已存在的" Secret
  data: [DB_HOST, DB_PASSWORD, ...]
```

**为什么禁止：**
- ESO 的 Owner 模式通过 server-side apply 管理 Secret 字段集，每次 reconcile 会"清理"它认为不属于自己 fieldset 的字段。Merge ES 写入的字段不在 Owner ES 的声明里，会被 Owner reconcile 时擦掉
- 后果：Secret 在 Owner 和 Merge 各自 reconcile 之间反复闪烁。已经在跑的 Pod 因为 envFrom 是启动时快照表面正常，但**任何 Pod 重启或新 Pod 创建（包括 PreSync hook Job 的 Pod）都会拿到不完整的 Secret 而崩溃**

如果实在要分两个 ExternalSecret 来源（不同 Vault 路径、不同 refresh 频率），用**模式 B**（两个独立 target Secret），不要硬塞一个 Secret。

### ❌ 反模式 E：多个 ExternalSecret 都用 Merge 写同一个 target Secret（**禁止**）

```yaml
# 两个 ES 都是 Merge
target: { name: my-app-secret, creationPolicy: Merge }
```

**为什么禁止：** Merge 模式不会创建 Secret，只会 patch 已存在的 Secret。两个 Merge ES 共写一个 Secret，意味着**没有任何 ES 负责创建 Secret**。结果：
- ESO 状态显示 `SecretMissing` 但 condition `Ready=True`（坑：很多人以为 Ready=True 就是正常）
- 目标 Secret 永远不存在
- migration Job 启动时 envFrom 引用不存在的 Secret，Pod 报 `Error: secret "xxx" not found`，进入 `CreateContainerConfigError`
- migration Job 失败 → PreSync hook 失败 → 整个 sync 失败

---

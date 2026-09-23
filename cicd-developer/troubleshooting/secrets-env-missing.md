---
name: secrets-env-missing
description: Pod 启动后报 env var 缺失 / 业务代码 panic "missing config"，或 Secret 一直 NotReady。诊断 ExternalSecret / PushSecret / ClusterSecretStore 链路。
---

# Playbook：env vars / Secret 缺失

## 症状

业务报：
- 应用启动 panic："DB_PASSWORD is empty" / "Cannot connect to redis: no auth token"
- `kubectl exec <pod> -- env | grep <KEY>` 输出为空或不存在
- `kubectl get secret <name> -o yaml` 看 `data:` 为空 / 缺 key

ExternalSecret 状态：
- `kubectl get externalsecret -n <ns>` 显示 `READY=False` / `STATUS=SecretSyncedError`

PushSecret 状态：
- `kubectl get pushsecret -n <ns>` 显示 `Synced=False`

## 诊断顺序

### Step 1. 沿链路反查

```
应用 env var
  ↑ 引用
Pod spec envFrom secretRef / env.valueFrom.secretKeyRef
  ↑ 引用
K8s Secret <name>
  ↑ Owner / 写入
ExternalSecret <name>
  ↑ 读
ClusterSecretStore <css-name>
  ↑ 拉
Vault path `secret/...`
```

从应用层往后追：

```bash
# 1. Pod 是否引用了 Secret
kubectl -n <ns> get pod <pod> -o yaml | yq '.spec.containers[].envFrom, .spec.containers[].env[] | select(.valueFrom)'

# 2. Secret 是否存在 + 含 key
kubectl -n <ns> get secret <secret-name> -o yaml | yq '.data | keys'

# 3. ExternalSecret 状态
kubectl -n <ns> get externalsecret <es-name> -o yaml | yq '.status'

# 4. ClusterSecretStore 状态
kubectl get clustersecretstore <css-name> -o yaml | yq '.status.conditions'

# 5. Vault 路径是否真的有数据
vault kv get <vault-path>
```

第一个失败的环节决定模式。

### Step 2. 失败点对模式

| 失败点 | 模式 |
|---|---|
| Pod spec 没 envFrom / env.valueFrom | **模式 1：Rollout 没接 Secret** |
| Secret 存在但缺 key | **模式 2：ExternalSecret data 没 map 这个 key** |
| Secret 完全不存在 | **模式 3：ExternalSecret READY=False** |
| ExternalSecret READY=False，原因 `secret not found in vault` | **模式 4：Vault 路径不存在或 PushSecret 没写** |
| ExternalSecret READY=False，原因 `403 / permission denied` | **模式 5：Vault policy / CSS auth 错** |
| ClusterSecretStore READY=False | **模式 6：CSS 集群级问题** |

## 各模式修法

### 模式 1：Rollout 没接 Secret

`base/rollout.yaml` 的 `containers[0].envFrom` 或 `env` 没引用 Secret。

```yaml
# 加 envFrom（推荐，整 Secret 全注入）
spec:
  template:
    spec:
      containers:
        - name: <app>
          envFrom:
            - secretRef:
                name: <secret-name>     # ExternalSecret 同名
```

push + ArgoCD sync 后下次 rollout 自动有 env vars。

### 模式 2：ExternalSecret 没 map 这个 key

`kubectl get externalsecret <name> -o yaml | yq '.spec.data[]'` 看有没有这个 key。

```yaml
# 改 ExternalSecret，加一段
spec:
  data:
    - secretKey: NEW_KEY              # 给 K8s Secret 的 key
      remoteRef:
        key: <vault-path>
        property: NEW_KEY              # Vault 里的 field 名
```

ExternalSecret refreshInterval 默认 1h，等不及就 `kubectl annotate es <name> force-sync=$(date +%s) --overwrite`。

### 模式 3：ExternalSecret READY=False

`kubectl get es <name> -o yaml | yq '.status.conditions'` 看 message：

- `secret not found` / `path not found` → 跳模式 4
- `permission denied` → 跳模式 5
- `template rendering error` → ExternalSecret 用了 `target.template` 字段，go template 语法错；看 message 具体行号
- 如果是 RDS / Aurora master password 的 generator 型 ExternalSecret，且 target Secret 已存在并含 key，**不要**通过 delete/recreate ExternalSecret 修状态。`deletionPolicy: Retain` 不等于重建安全；实测重建可能覆盖现有 Secret。先验证 Secret、PushSecret、Vault、Crossplane 引用是否一致，再由运维做有审计的状态清理或 controller 级处理。

### 模式 4：Vault 路径不存在 / PushSecret 没写

```bash
vault kv get <vault-path>
# 期望：列出 key 值
# 实际："No value found at secret/data/..."
```

**情况 A**：本来就应该是 Crossplane PushSecret 写的：
- RDS connection 报 `secret key endpoint does not exist` → 跳 `troubleshooting/pushsecret-stuck-endpoint-does-not-exist.md`
- Redis connection 报 `secret key address does not exist` → 跳 `troubleshooting/redis-pushsecret-address-missing.md`

外部 MySQL 等 `platform-resource-credential` 也必须追到 GitOps owner claim/controller。
如果当前平台没有对应的高层 claim/Composition，STOP + Ops Todo；禁止把 DBA 手工建账号、
`vault kv put` 或 `kubectl create secret` 当成修复。

**情况 B**：app-owned 且上游只能人工签发（如第三方 API key）：

```bash
vault kv put <vault-path> KEY1=value1 KEY2=value2
```

权限：Maintainer+ 写 `secret/{env}/app/<app>/*` 全 4 env；platform 路径 `secret/{env}/<platform>/application/<app>/*` 只读（自动写入）。

此例外不适用于 DB / 缓存 / 队列凭据。它们必须走 platform 路径和 GitOps provisioner；
不能因为 Vault path canonical 就人工补值。

**情况 C**：路径写错 → ExternalSecret 的 `remoteRef.key` 不符合 `vault-paths/rules.yaml`。跑 `validators/check_vault_paths.py k8s/overlays/<env>/` 自查。

### 模式 5：Vault policy / CSS auth 错

`kubectl get es <name> -o yaml | yq '.status.conditions[].message'` 含 `403 permission denied`。

**情况 A**：app 没在 `DEV/gitlab-vault-sync apps-registry.yaml` 注册 → hard rule #7。提 MR 加一行；合并后 ~3 min Vault 身份组同步。

**情况 B**：写错路径走 `secret/cicd/*`（业务 app 没 cicd policy）→ hard rule #7a 红线，按 `references/vault-paths/resolver.md` 改 ExternalSecret 的 `remoteRef.key`。

**情况 C**：Developer 角色试读 pre/prod → hard rule #7c，**不要** 用 Maintainer+ token 代跑；提权或换人写。

**情况 D**：CSS 名字错 → 改 `secretStoreRef.name` 到正确值。通用应用 Secret 查 `references/data/clusters.yaml -> vault_css`；Sentry DSN 例外，查 `references/sentry/README.md -> SENTRY_DSN_CSS`，因为 ExternalSecret 必须读 sentry-onboard Job 写入的同一个 Vault。

### 模式 6：ClusterSecretStore 本身坏

```bash
kubectl get clustersecretstore <css-name> -o yaml | yq '.status.conditions'
```

不该业务负责修。STOP 产 Ops Todo "ClusterSecretStore <css> 在 <cluster> 不健康"。

平台侧常见原因：
- Vault 实例宕 / Vault token 过期
- ESO controller pod OOMKilled
- CSS auth method 配置变了（K8s JWT auth 改 secret name）

## 为什么不走 <替代方案>

- **直接 `kubectl create secret`** —— 临时 unblock 可，但下次 ArgoCD sync 会被 ExternalSecret 当成 drift 清掉；治根是修 ExternalSecret 链路
- **应用代码加默认值兜底** —— 对生产凭据是危险的（连不上 DB 用空字符串还不如崩）；只对 feature flag 这类用
- **改 imagePullPolicy / restartPolicy** —— 跟密钥无关
- **重启 ESO controller** —— 模式 6 才考虑；模式 1-5 重启没用
- **删掉 generator 型 ExternalSecret 再重建** —— 对 RDS / Aurora master password 不安全；可能触发新 password 覆盖 K8s Secret，而 Vault `IfNotExists` 仍保留旧值，最终密码漂移

## 参考

- hard-rules.yaml #7 / #7a / #7b / #7c / #15
- references/data/clusters.yaml（通用 app Secret 的 `vault_css`）
- references/sentry/README.md（Sentry DSN 的 `SENTRY_DSN_CSS` 例外）
- references/vault-paths/resolver.md（resolver）
- references/vault-paths/rules.yaml（5 条规则）
- references/vault-paths/platforms.yaml（platform 枚举）
- troubleshooting/pushsecret-stuck-endpoint-does-not-exist.md（RDS PushSecret 卡 endpoint）
- troubleshooting/redis-pushsecret-address-missing.md（Redis PushSecret 缺 address）

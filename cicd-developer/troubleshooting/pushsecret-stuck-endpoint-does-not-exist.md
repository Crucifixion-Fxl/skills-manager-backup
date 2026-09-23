---
name: pushsecret-stuck-endpoint-does-not-exist
description: PushSecret 报 `secret key endpoint does not exist` 死活不同步，即使 source Secret 已经填完整。根因是 Crossplane RDS 首次 provision 期间的竞态让 ESO 缓存了错误 reason。
---

# Playbook：PushSecret 卡 `secret key endpoint does not exist`

## 症状

用户报告以下之一或多个：
- ArgoCD app 显示 PushSecret 资源 `OutOfSync` / `Degraded`
- `kubectl describe pushsecret <name>` 状态 condition message：
  > secret key endpoint does not exist
- `kubectl get secret <app>-rds-conn -o yaml` 显示 7 个 key 都齐（host / address / port / endpoint / username / password / attribute.password）—— 源数据 OK
- Vault 路径 `secret/{env}/rds/application/<app>/database` 空或不全
- ArgoCD selfHeal / 多次 sync 都修不好

如果报错是 Redis / ElastiCache 的 `secret key address does not exist`，不要用本
playbook；跳 `troubleshooting/redis-pushsecret-address-missing.md`。

## 诊断顺序

### Step 1. 先核查源 Secret 真的填完了（排除数据真的缺）

```
kubectl get secret <app>-rds-conn -n <namespace> -o jsonpath='{.data}' | jq 'keys'
```

期望：列表含 `host`、`address`、`port`、`endpoint`、`username`、`password`、`attribute.password`。

如果列表 **不完整**：PushSecret 的报错是 **正确的**，Crossplane 还没把 Secret 写完。等 30 秒再看。一直不完整就跳到 `crossplane-rds-reconcile-loop.md`——RDS 实例没起来。

如果列表 **完整**：进 Step 2。这是缓存错误那种情况。

### Step 2. 确认是缓存错误（不是别的失败）

```
kubectl describe pushsecret <name> -n <namespace> | tail -20
```

找 `Reason: SecretNotFound`（或类似）的 condition，配上"Crossplane 正在 mid-write 时刻"的 message timestamp（Secret 当时只写了部分 key）。Crossplane 写完之后，ESO 缓存的旧 reason 不会自己刷新，PushSecret 也不会自己重试这个对象。

### Step 3. 排除 ESO 1.3.2 数据丢失 bug

跑：
```
python3 "$skill_root/validators/check_eso_pushsecret_bug.py" <manifest-dir>
python3 "$skill_root/validators/check_vault_paths.py" <manifest-dir>
```

如果 validator FAIL 报 `updatePolicy=Replace + dataCount>=7 + attribute.password`：STOP。**这不是本 playbook 范围**。是 PushSecret manifest 本身写错——按 `recipes/crossplane/rds-conn-push-secret.yaml.tmpl` 改成 `updatePolicy: IfNotExists` 后重新 sync。

两个 validator 都 PASS 才进入修复建议。

## 根因

ESO 缓存了 "secret key does not exist" reason，这个 reason 来自 Crossplane 渐进式写 Secret 的中间态（只写了部分 7 个 key）。Crossplane 写完之后缓存不刷新，PushSecret 也不会自动重试。

跟 ArgoCD selfHeal、refreshInterval、sync 次数都无关——它们都触发不了 ESO 重评。

## 修复

仅当用户明确要求实施后，才可执行下列删除操作；诊断时只报告建议和验证方式。

```
kubectl delete pushsecret <name> -n <namespace>
```

ArgoCD 会在下一轮 sync 重建（selfHeal: true）。新建的 PushSecret 一上来读到完整 Secret，把 4 个 key 推到 Vault。

验证：

```
# 等 ArgoCD 重建
sleep 30
kubectl describe pushsecret <name> -n <namespace> | grep -A2 "Conditions:"
# 期望：Synced: True, Reason: PushSecretSynced

# 看 Vault 路径填上了
vault kv get secret/<env>/rds/application/<app>/database
# 期望：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD 都存在且非空
```

如果 PushSecret 已经 Synced=True 但 Vault 还是空，问题在别处——查 ESO ClusterSecretStore 状态 + CSS Vault auth。

## 为什么不走 <替代方案>

- **重启 ESO controller (`kubectl rollout restart deploy external-secrets`)** —— 能清缓存，但整集群所有 secret refresh 离线 ~30 秒。只一个 PushSecret 卡时不必要；除非很多 PushSecret 同一时刻卡。
- **改 `refreshInterval`** —— 没用。缓存错误是按 Reason 缓存的，不是按时间。
- **强制 ArgoCD sync** —— 没用。ArgoCD sync 只是把 YAML 写到 apiserver；YAML 已经在那。卡的是 ESO controller 的缓存。
- **给 PushSecret 加 annotation 触发重评** —— 脆弱（用什么 annotation？selfHeal 会不会保留？）。直接 delete 更干净。

## 频率

RDS Instance 首次 provision 时可能每个 region 都中一次。delete 重建之后通常稳定，不会反复触发。

## 后续自动化想法

可以做 PreSync hook Job 检测这种 stuck state + 自动 delete。**不在本 playbook 范围**——这个人工操作只在用户主动盯着部署时发生，可接受。

## 参考

- Per-region RDS 首次 provision 反例：Crossplane connection Secret mid-write 会让 ESO 缓存缺 key 状态。
- `recipes/crossplane/rds-conn-push-secret.yaml.tmpl` 头部注释列了 PushSecret 必须遵守的约定。
- ESO 1.3.2 Replace+7key+attribute.password 是 **另一个** 失败模式——由 `validators/check_eso_pushsecret_bug.py` 拦截。

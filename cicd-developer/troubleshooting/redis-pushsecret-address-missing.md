---
name: redis-pushsecret-address-missing
description: Redis / ElastiCache PushSecret 报 `secret key address does not exist`，通常是 crossplane-conn-patcher 尚未补齐或未运行，不是 RDS endpoint 缓存问题。
---

# Playbook：Redis PushSecret 缺 `address`

## 症状

用户报告以下之一或多个：
- ArgoCD app 显示 Redis PushSecret `OutOfSync` / `Degraded`
- `kubectl describe pushsecret <app>-redis-push -n <namespace>` message 含：
  > secret key address does not exist
- `<app>-redis-conn` Secret 不存在，或只含 `port` / `endpoint`，没有 `address`
- ElastiCache `ReplicationGroup` / `Cluster` 已经 Ready，但 Vault 路径
  `secret/{env}/redis/application/<app>/redis` 还没有 `REDIS_HOST`

这不是 RDS 的 `secret key endpoint does not exist` 缓存问题；不要跳 RDS
PushSecret playbook。

## 诊断顺序

### Step 1. 确认报错 key 是 `address`

```
kubectl describe pushsecret <app>-redis-push -n <namespace> | tail -30
```

如果报错是 `endpoint` / `host`，先看 manifest 是否写错了 Redis PushSecret。
Redis 标准写法只读 source Secret 的 `address` 和 `port`。

### Step 2. 看 Redis connection Secret

```
kubectl get secret <app>-redis-conn -n <namespace> -o jsonpath='{.data}' | jq 'keys'
```

期望：`["address","port"]`。

- Secret 不存在：进入 Step 3。
- 只有 `port` / `endpoint`，没有 `address`：进入 Step 4。
- 已有 `address` / `port`，但 PushSecret 仍报旧错误：等下一个 refresh，或 force-sync PushSecret；不要先删。

### Step 3. 确认 Redis CR 写了 `writeConnectionSecretToRef`

按 manifest 里的 kind 选择 `cluster.elasticache.aws.m.upbound.io` 或
`replicationgroup.elasticache.aws.m.upbound.io`：

```
kubectl get <cluster-or-replicationgroup>.elasticache.aws.m.upbound.io <name> -n <namespace> -o yaml \
  | yq '.spec.writeConnectionSecretToRef'
```

期望：

```yaml
name: <app>-redis-conn
```

如果为空，manifest 写错了。按
`recipes/crossplane/elasticache-replicationgroup.yaml.tmpl` 补
`spec.writeConnectionSecretToRef`，并确认 Redis CR 自身有
`metadata.namespace: <namespace>`。当前 ElastiCache v2 CRD 不接受
`writeConnectionSecretToRef.namespace`，connection Secret 与 Redis CR 在同一 namespace。
不要写 `publishConnectionDetailsTo`；这个字段不在 ElastiCache v2 CRD schema 里，
会被 apiserver prune，conn-patcher 也读不到。

### Step 4. 等 conn-patcher 周期，超过 5 分钟再升级

`crossplane-conn-patcher` 每 2 分钟跑一次。ElastiCache Ready 后短时间内缺
`address` 是正常时序。

如果 Ready 已超过 5 分钟仍缺 `address`，联系运维执行：

```
kubectl get cronjob crossplane-conn-patcher -n crossplane-system
kubectl get job -n crossplane-system | grep crossplane-conn-patcher
kubectl logs -n crossplane-system job/<latest-crossplane-conn-patcher-job>
```

看点：
- CronJob 是否存在且最近成功跑过
- Job 日志是否 SKIP 了目标 `ReplicationGroup` / `Cluster`
- `ReplicationGroup` 是否是 cluster-mode-enabled；如果是，conn-patcher 版本必须能读
  顶级 `configurationEndpointAddress` + `port`

## 根因

ElastiCache v2 provider 对 `ReplicationGroup` 不稳定地产生下游可用的 connection
Secret；平台依赖 `crossplane-conn-patcher` 统一创建 / 补全 `<app>-redis-conn`，
最终字段固定为 `address` + `port`。PushSecret 读 `address` 失败，说明 source
Secret 还没被 patcher 补齐，或者 manifest 没给 patcher 足够信息。

## 修复

按诊断结果选一种：

1. **正常时序**：等下一个 conn-patcher 周期。验证 `<app>-redis-conn` 出现
   `address` / `port` 后，PushSecret 会把 `REDIS_HOST` / `REDIS_PORT` 写入 Vault。
2. **manifest 缺 `writeConnectionSecretToRef`**：修 Redis CR manifest，commit + sync。
3. **conn-patcher 未部署 / 失败 / 版本不支持当前 kind**：产 Ops Todo 给运维修
   `crossplane-conn-patcher`，并附上 CR 名、namespace、目标 cluster、最近 Job 日志。
4. **临时兜底**：由运维从 AWS Console 取 Redis primary / configuration endpoint，
   手工写入 Vault：
   ```
   vault kv put secret/{env}/redis/application/<app>/redis REDIS_HOST=<endpoint> REDIS_PORT=6379
   ```
   conn-patcher 恢复后，PushSecret 会用标准来源覆盖 / 补齐。

## 为什么不走 <替代方案>

- **跳 `pushsecret-stuck-endpoint-does-not-exist.md`** —— 那个是 RDS 首次
  provision 的 ESO reason 缓存问题；Redis 缺的是 source Secret 的 `address`，根因不同。
- **删除 PushSecret 重建** —— source Secret 没有 `address` 时，重建也只会继续报错。
- **把 PushSecret 改成读 `endpoint`** —— Redis 标准下游字段是 `address` / `port`；
  `endpoint` 可能不存在，也可能是 provider 中间形态，不能作为契约。
- **写 `publishConnectionDetailsTo`** —— ElastiCache v2 CRD 不接受，会被 prune。

## 参考

- `workflows/add-redis.md`
- `recipes/crossplane/elasticache-replicationgroup.yaml.tmpl`
- `workflows/add-redis.md` 中的 conn-patcher 时序与连接 Secret 契约

---
name: db-migration-failures
description: PreSync migration Job 失败 / ArgoCD sync 卡在 Running / 新 Pod 不启动 / DB 拼接 URL parse error 等 DB migration 失败模式。
---

# Playbook：DB Migration 失败

## 症状

按 `workflows/add-db-migration.md` 配好 PreSync hook 后：
- ArgoCD app 状态 `Running` 长时间不收敛 / `Failed`
- `kubectl get jobs -n <ns>` 看到 `<app>-migrate` 状态 `Failed` 或 `Active` 卡住
- 新版本 Pod 不启动（Rollout 没拉新版本）
- migration Job logs 报错（URL parse error / DB connection refused / 表已存在等）

## 诊断 + 修复对照

### 信号 1：Job logs `cannot parse postgres://... failed to parse as URL`

**根因**：migration binary 拼接 DATABASE_URL 时**没** URL escape 密码。Vault 自动生成的密码可能含 `:` `@` `/` `?` `#` 等 URL 保留字符，裸 `fmt.Sprintf` 会被 URL parser 误解析（把密码后半段当成 port / path）。

示例报错：

```
connect postgres: cannot parse `postgres://app:8b:dKBlYg@10.0.0.1:5432/app?sslmode=require`:
failed to parse as URL (invalid port ":dKBlYg" after host)
```

**修复**：业务方改 migration binary，按语言用对应 URL escape：
- Go: `url.UserPassword(user, pass).String()`
- Python: `urllib.parse.quote(password, safe='')`
- Java: `URLEncoder.encode(password, StandardCharsets.UTF_8)`

push 新 binary → 等 Image Updater 写 SHA → ArgoCD 自动重跑 Job。

### 信号 2：Job logs `connection refused` / `no such host`

**根因**：DB env vars 未注入到 Job container。

排查：

```bash
# 运维执行
kubectl -n <ns> describe pod <migrate-pod>
# 看 events 段是否报 secret/configmap not found

kubectl -n <ns> get job <app>-migrate -o yaml | yq '.spec.template.spec.containers[0].envFrom'
# 看 envFrom 引用的 Secret / ConfigMap 名
```

**根因细分**：

- envFrom 引用的 Secret 不存在（ExternalSecret 还没生效 / 名字拼错）—— 跳 `troubleshooting/secrets-env-missing.md`
- 同 commit 改了 ExternalSecret + 触发 migration（**时序坑**）—— PreSync hook 在 ES 同步前跑，拿不到新 Secret。修复：把这次 commit 拆成两次 push（先 ES，再 image）
- SA 跟主应用不一致 → IRSA 不生效 → DB 走默认凭据失败 → 看 Job spec `serviceAccountName` 是不是 `{{app}}`

### 信号 3：Job logs `relation "..." already exists` 或 `column already exists`

**根因**：migration binary 不幂等。Job 因 `backoffLimit > 0` 重试时报错。

**修复**：业务方改 binary，加 `IF NOT EXISTS` 或检查 schema_migrations 表跳过已应用的版本。

**短期绕开**：手动跑一次 SQL 把状态对齐，删 Job 让 ArgoCD 重跑（hook-delete-policy 自动清理，下次 sync 会重建 Job）。

### 信号 4：Job 状态 `Active` 卡住很久（>5min）

**根因**：migration 跑得慢 / DB lock 等待 / DDL 阻塞。

排查：

```bash
# 运维查 Job logs 看 migration 进度（stdout 应该输出每条 migration 名）
kubectl -n <ns> logs <migrate-pod>

# 看 DB 端 long-running query（在 DB 端执行）
SELECT pid, query, query_start, state FROM pg_stat_activity WHERE state != 'idle';
```

**根因细分**：
- 单条 migration 改大表加列（PG 是 metadata-only 操作但 MySQL 是 rewrite，慢）
- 锁等待（另一个连接长事务）
- migration 进程死锁自己（旧 backfill 跑的 batch 没退出）

**修复**：
- 终止单次 Job：`kubectl delete job <app>-migrate -n <ns>`，让下次 sync 重跑（注意 migration 必须幂等，否则不能重跑）
- DB 层 kill 阻塞的 connection
- 大表改 DDL 改用 `pt-online-schema-change` / `gh-ost`（PostgreSQL 12+ 用 `ALTER TABLE ... ADD COLUMN ... NULL`）

### 信号 5：ArgoCD app phase=Running，但 `kubectl get jobs` 看不到 Job

**根因**：PreSync hook 创建 Job 失败 / hook-delete-policy 配置错。

排查：

```bash
# 看 ArgoCD app 的 hook 状态
kubectl -n argo-cd get app <app> -o yaml | yq '.status.operationState.syncResult.resources[] | select(.kind == "Job")'
```

可能看到：
- `status: Failed` + 具体错误（manifest schema 错 / Kyverno 拦下等）→ 修 manifest
- `status: Succeeded` 但 Job 立刻消失，24h 内看不到日志 → hook-delete-policy 写错（通常是加了 `HookSucceeded`，ArgoCD 成功后立即删除 Job，K8s TTL 不会生效）

**修复**：
- `hook-delete-policy: BeforeHookCreation`（**模板默认**，按 recipe 抄就对；保留 24h 交给 `ttlSecondsAfterFinished`）
- 如果是 manifest 错 → 修 + push

### 信号 6：Pod 启动后用旧密码连 DB 失败

**根因**：migration 链路没问题，但 Rollout 主资源还引用旧 Secret（cache 没刷）。

排查：检查 ExternalSecret refreshInterval 和上次 refresh 时间。

**修复**：force-sync ExternalSecret（运维执行）：

```bash
kubectl annotate externalsecret <app>-db-secret force-sync=$(date +%s) -n <ns> --overwrite
```

ExternalSecret 1 分钟内重新拉 Vault；Rollout 下次 reconcile 时挂载新 Secret。

## 红线 复盘

回顾 `workflows/add-db-migration.md` 与本 playbook 的红线：

1. **绝对不要让两个 ExternalSecret 写同一个 target Secret** —— hard rule #15，触发 reconcile race 字段被反复擦写
2. **不要在同一 commit 改 ExternalSecret + 触发 migration 的东西**（如镜像 tag）—— 时序坑（信号 2）
3. **migration 二进制必须幂等** —— 重试场景（信号 3）
4. **拼 DATABASE_URL 必须 URL escape 密码** —— 信号 1

## 为什么不走 <替代方案>

- **在应用启动时跑 migration（init container 或 main 函数）** —— hard rule #16 拒绝。多副本竞争 + 失败回滚难 + log 混在应用 log 里 + 新 Pod 启动失败不会阻断旧版本承接流量
- **手工 SQL 直连跑 migration** —— 临时 unblock 可，但破坏 GitOps 可追溯。最多算应急通道，跑完后必须把 binary 修好走 PreSync hook
- **改 backoffLimit: 0 让一次失败就停** —— 看上去更严格，但实际 K8s 偶发问题（节点抢占 / image pull race）也会被当永久失败拦住部署。默认 1 是平衡

## 参考

- `workflows/add-db-migration.md`（接入流程）
- `recipes/db-migration/presync-hook.yaml.tmpl`（模板）
- `references/db-migration/end-to-end-example.md`（完整 MR 示例）
- `references/db-migration/secret-patterns.md`（3 模式 + 2 反模式）
- `references/db-migration/timing-pitfalls.md`（时序坑深入）
- hard-rules.yaml #15 / #16

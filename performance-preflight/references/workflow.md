# Workflow — Step 3 Procedure + Anti-patterns
# 流程 — Step 3 步骤 + 反模式

## 0. Hard rules / 硬规则

1. **Never trust a developer's frequency estimate without cross-checking against an existing metric.** Find a sibling counter on the same call chain and verify.
   **永远不要在没有用既有指标交叉验证的情况下相信开发者的频率估计。** 在同一调用链上找一个兄弟 counter 去核。

2. **Project log volume is project-specific.**
   - `iot-service-cloud` splits logs by `userId` / `serialNumber` → INFO/DEBUG total counts NOT meaningful as frequency.
   - `kiss` / `statemachine` do NOT split → log totals ARE valid frequency.
   - WARN/ERROR escape the split everywhere via `LogUtil.doOnPrintLogNotSplit` — both are queryable globally.
   See [recipes/iot-service-cloud.md §5](recipes/iot-service-cloud.md) and [recipes/kiss.md §5](recipes/kiss.md).

3. **Project code is dynamic — if a query result contradicts the documented expectation, re-grep the latest source code.** Counter names / labels / log structure change. Always grep current branch when in doubt.
   **项目是动态的。** 查询反逻辑时回头看最新代码。

4. **If no existing metric covers the line you need to estimate, your second move is `Grep` the source tree.** Search for `Counter.build` / `meterRegistry.counter` / `Tracker.track` / `LogUtil.warn` / `LogUtil.error` on the same call chain. Read the registration site to see name + labels, then write the query.
   **既有指标找不到，第二步是 Grep。** 不要凭空编 counter 名 — 编出来的查询是静默失败的。

5. **GitLab is the fallback when local working tree is missing the project.** `gitlab.addx.ai` is the source of truth for non-checked-out projects (auth, push-service, ai-saas, statemachine, etc.).
   **本地没的项目去 GitLab 兜底。**

6. **Read [historical-lessons.md](historical-lessons.md) before signing off.** Walk the L1-L12 lesson catalog; flag matches as `[HISTORICAL:Lx]` in the assessment doc.
   **签字前先看历史经验**。

## 1. The 6-step Step-3 procedure / 6 步流程

Run in order. Do NOT skip ahead; do NOT ask the developer for a number you can derive yourself.
按顺序执行。不要跳过；不要先问开发者一个你能推出来的数。

### Step 3.1 — Grep the call chain locally
### 3.1 — 本地 grep 调用链

```bash
# On the file(s) the change touches, grep for instrumentation
Grep "Counter.build|Counter.builder|MeterRegistry|Tracker.track|LogUtil.info|LogUtil.warn"
```

Pull metric names + labels + the line that increments them. One level up the call stack too.
拉出 metric 名 + label + 实际打点的那行；上一级 caller 也搜。

### Step 3.2 — If repo not local, query GitLab
### 3.2 — 仓不在本地用 GitLab 兜底

```bash
# Replace <id> with the project ID
curl -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/search?scope=blobs&search=Counter.builder"
```

### Step 3.3 — Match each new I/O line to either an existing metric or a contract row
### 3.3 — 新代码的每行 I/O 要么挂指标，要么挂契约

For every new line that does I/O (DB / Redis / RPC / MQTT / Kafka / S3 / disk / log) ask:
对每行 I/O 问："这行/同 method 的兄弟行是否已有 counter？"
- **YES** → record name + PromQL + current value (run the query, don't quote from memory).
- **NO** → either:
  - (a) add a counter as part of this MR (preferred when on a hot path), OR
  - (b) lock the frequency as a Step 6 contract value with **runtime guard**.

### Step 3.4 — Cross-validate developer estimates
### 3.4 — 交叉验证开发者估计

If the developer says "this runs N times per request":
- find a sibling counter on the same handler.
- compute the ratio (entry QPS) / (sibling counter rate).
- if it's wildly different from N, mark `[CONFLICT:Q<n>]` and resolve before sign-off.

### Step 3.5 — Pick log volume only when project's logs are non-split
### 3.5 — 只在日志非拆分的项目用日志总量

- `iot-service-cloud` INFO/DEBUG → use a counter.
- `iot-service-cloud` WARN/ERROR → `log_level_count_total{loglevel}` PromQL or ES — both work.
- `kiss` / `statemachine` (non-MDC-split) → ES query is fine.

### Step 3.6 — Output the signal table + contract list
### 3.6 — 产出信号表 + 契约列表

Per-signal row: `name | type | source query | current value (with timestamp) | data link`.
Anything not measurable goes to Step 6 contract list with `[CONTRACT:C<n>]`.

## 2. Anti-patterns specific to metric querying / 指标查询特有反模式

1. **"I'll grep ES to estimate INFO log frequency in iot-service-cloud."** — Wrong. Logs split by user/SN; you'll always undercount or hit a single-user view. Use a counter (or add one).
2. **"This metric name looks right — I'll copy-paste."** — Wrong. Counter names rotate, labels change, services rename. Always read the latest source registration site first.
3. **"The dev said 50 QPS — I'll take their word."** — Wrong. Cross-validate (Step 3.4). Devs systematically under-estimate fan-out and miss retries.
4. **"Job label is `prod-us-kiss` — same as iot-service-cloud."** — Wrong. kiss is `us-prod-kiss` (Family B). Always probe with `up{job=~".*<service>.*"}`.
5. **"It's just a single counter — I don't need to add labels."** — Wrong. Without labels you can't decompose by error class / model / region. Match labels to dimensions you'll later need to slice.
6. **"`tomcat_threads_busy_threads` will tell me worker saturation on iot-service-cloud."** — Wrong. iot-service-cloud uses **Undertow**, not Tomcat. No worker-thread metric exists. Use proxies (see L8 in historical-lessons.md).
7. **"I'll average over 24h to get the baseline."** — Wrong. Use 7-day max-over-time at 5-min step (`max_over_time(...[7d:5m])`). Capacity is set by the worst 5-min window in a week, not the average.
8. **"The query returned `n/a` so the metric doesn't exist."** — Maybe. Confirm with `count by (__name__)({job="<job>"})` to see what actually exists. The metric might exist with a different name suffix (`_count` vs `_count_total`).
9. **"Prometheus shows it's fine, so it's fine."** — See [first-principles-analysis.md §3](first-principles-analysis.md). Cross-validate with a non-Prometheus signal before any go/no-go.
10. **"This is a small change, I don't need a perf preflight."** — The bird-story incident's MR was a "small change" (one feature flag eval). Always run the workflow on hot paths.

## 3. When this skill collaborates with others / 与其它 skill 的协作

| Stage / 阶段 | Use which skill / 用哪个 |
|---|---|
| Pull Prometheus data | `prometheus` skill OR [query-helpers.py](query-helpers.py) directly |
| Pull Grafana panels / dashboards | `grafana` skill |
| Pull warehouse data | `superset` skill (with `datahub` for schema) |
| Pull production logs (per-user/SN) | `troubleshooting` skill |
| Cross-region cluster ops | `k8s-ops` skill |
| Argo CD deployment state | `argocd` skill |
| Sentry errors / release health | `sentry` skill |
| GrowthBook A/B status | `growthbook` skill |
| File issue for a found gap | `gitlab-issue-sop` skill |
| Add metrics + alerts after the assessment | `observability-design` + `grafana-dashboard-alert-update` + `sla-metric` skills |
| Write code (kill switch / cache) | `dev-workflow` or `small-feature-flow` skill |
| Real incident root-cause | `root-cause-analysis` skill |

This skill owns "method + doc + contract guarantee". Implementation goes to other skills.
本 skill 只负责"方法 + 产出文档 + 守住契约"。具体实施由其它 skill 完成。

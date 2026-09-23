# Historical Lessons — Patterns Mined from Past Incidents
# 历史经验 — 从过往事故里提炼的模式

This file is the **mistake catalog**. When reviewing a new design, walk this list and ask "is the new design heading toward any of these classes of bug?" — that's how you catch errors that look novel but are actually re-runs of past failures.
本文是**踩过的坑目录**。审一个新设计时，对照本表问"这个新设计有没有走向下面任一种 bug 类别？"——就这样抓住"看起来新、其实是老坑重演"的错误。

Sources / 来源:
- iot-release `docs/incident-*.md` post-mortem files
- `git log --all --grep="fix\|incident\|hotfix\|leak\|deadlock\|surge\|saturat" -i` on iot-release + kiss
- Project memory entries (e.g. JWT gray rollout, bird-story canary, etc.)

---

## L1. 「热路径上加一个看似廉价的 sync I/O 就能压垮 pod」
## L1. "A single innocuous-looking sync I/O on a hot path saturates the worker pool"

**Canonical example / 标准案例**: 2026-04-23 bird-story canary 5xx P0 (`docs/incident-2026-04-23-bird-story-canary-5xx.md`).

**What happened**: MR !2004 added a per-request GrowthBook feature evaluation + Snowplow self-describing event in `VideoAIService.resolveEnableBirdStory`, on the `/deviceMsg/config` hot path (~3,300 QPS in US prod). Each call cost only ~ms in isolation. But:
1. **Canary routed concentrated flow** — the gray whitelist sent kiwibit (a high-frequency bird-feeder tenant) traffic exclusively to 2-3 canary pods.
2. **Worker thread pool saturated within ~7 minutes** of pod startup.
3. **Liveness/readiness probes failed** (couldn't get a worker thread to respond).
4. **Pod entered crash-loop**: SIGTERM → graceful shutdown → ALB still routes new traffic → 504 storm.
5. **Kafka `public-gray` consumer rebalance** every restart → consumer lag spiked to 6000+.
6. **ALB returned 1.5M 5xx in 5min** while pod-side `UT005023` was only ~215 events.

**Why it eluded review**:
- The new code was "just one feature flag eval" — looked free.
- No worker-thread metric existed (no `tomcat_threads_*` or `undertow_*` in Prometheus). Saturation was invisible.
- Canary routing math wasn't in the design doc — concentration factor not computed.

**Detection rule for this skill / 本 skill 的检测规则**:
- If new code adds **any** sync RPC / external eval / Snowplow track / DB call on a path with > 100 QPS prod, **mandatory worker-thread budget calc** using Little's Law:
  ```
  Δ_worker_threads_per_pod ≈ (QPS_to_pod × added_latency_per_call) / num_pods
  ```
- **Concentration factor for canary** must be applied:
  ```
  effective_QPS_per_canary_pod = (whitelist_QPS) / (num_canary_pods)
  ```
  If the whitelist is "all bird-feeder tenants" and they aren't uniformly distributed, this can be 30–50× the average.
- **Rollback cache must be in place** — bird-story fix was `@QueryCache` per `(userId, sn)` for 5 min + extracting the resolver to a bean (`refactor(bird-story): extract resolver bean + switch cache to @QueryCache`, commit `c1367329c9`). Cache the negative path too.

---

## L2. 「按 SN/userId 拆日志的项目，加 INFO 日志后用 ES 拿不到全局速率」
## L2. "Adding an INFO log doesn't give you a global rate in iot-service-cloud (it's split by user/SN)"

**Pattern**: iot-service-cloud's `logback-spring.xml` puts `userId` + `serialNumber` in MDC; the log shipping pipeline splits by these keys. So an INFO line you added for "monitoring purposes" ends up in **per-user / per-SN ES indices**, not in a global index, and you can never get a clean rate from troubleshooting platform.

**Detection rule**: do not add INFO/DEBUG and call it "we'll observe via logs". Add a `Counter.build(...)` in `PrometheusMetricsUtil` (or `Counter.builder(...)` via Micrometer) on the same line.

**Exception**: WARN and ERROR escape the split via `LogUtil.doOnPrintLogNotSplit` — those ARE globally aggregable, AND auto-bump `log_level_count{loglevel}`. So WARN/ERROR are fine for rate.

---

## L3. 「跨服务 token 轮转 / 灰度环境 / 双密钥」的 1-3 个月连环事故
## L3. "Cross-service token rotation / gray env / dual-key" multi-month cascade

**Pattern (from project_jwt_gray_rollout_progress memory + commits like `a5508d78d5 fix(auth): createToken signs with secondary to shield single-key downstream`)**:
- Service A rotates secret. A's pods accept old + new keys.
- Downstream services B, C, D were never told to upgrade.
- A signs with the new key → B/C/D fail to verify → cascade outage.
- **Compounded by gray env**: gray and prod have **different Vault secrets**, so a token issued in gray is unverifiable in prod (per `project_gray_env_architecture` memory entry).

**Detection rule**:
- Adding a new secret? Check `docs/product/secret-rotation-plan.md` — entry must include all consumers, not just issuer.
- Token / signed-payload changes must include "downstream consumer list" in the design doc and dual-key window pre-rotation.
- Gray env changes that touch secrets should be assumed broken in prod handoff unless explicitly tested.

---

## L4. 「N+1 → bloom filter / Caffeine 短 TTL 是性能修复的标准模式」
## L4. "Bloom filter or Caffeine short-TTL is the standard fix for hot-path negative reads"

**Recurring pattern (commits `68b9f5d225 perf(setting-override): bloom filter negative fast-path` + `734e93ca9b fix(bird-story): cache resolveEnableBirdStory per (userId, sn) for 5 min`)**:

Every time a feature lands "1 DB read per request on the hot path" with the typical 99% negative miss rate (most devices not in the experiment / most users not in the cohort), the fix is **always** one of:
1. **Bloom filter** in front of the read for negative fast-path (cheap, no consistency cost when admin upserts because bloom is built fresh every N min).
2. **Caffeine local cache** with TTL of 30-60s, keyed by `(userId, sn)` or `sn`. Trade-off: TTL-window stale data tolerance, must be in contract.

If your design has the same shape, **propose the cache in the design doc**, don't ship the naïve version and bolt the cache on later.
设计如果是同样形状，**在设计文档里直接提出缓存**，别先上线"裸版本"再补缓存。

---

## L5. 「Redis 100% CPU is from a sync-on-connect side-effect that worked in dev」
## L5. "Redis CPU 100% is usually a sync-on-connect side-effect"

**kiss commit `8083d23e fix: remove full device sync on IoT node connect to prevent Redis 100% CPU`**: when a kiss IoT node restarts and reconnects, the old behavior was to do a full device sync (SCAN-like) against Redis. With 2.4M devices × N reconnects = Redis-killer.

**Detection rule**: any "on-connect" or "on-startup" code that touches a shared store should be evaluated for thundering-herd:
- If 100 pods restart at once (rolling deploy), what's the cumulative load on the shared store?
- Is there any random jitter or backoff?
- Is the cost O(devices) or O(1)?

---

## L6. 「sync ESC 字段意义随时间漂移、单测不能预防」
## L6. "Field-name semantic drift over time — unit tests cannot catch it"

**Recurring (commits like `102b51f343 fix(timeline): period must come from video_library.period, not library_status delta`)**: when adding a feature, devs assume "the existing field X means Y" without re-reading the producer side. Field semantics drift; comments lie.

**Detection rule for this skill** (relevant when a metric query unexpectedly returns 0 or seems off):
- Re-grep the code that **writes** the metric/field, not just the documentation.
- Cross-check against a sibling metric on the same call chain.

---

## L7. 「DB 连接池打满 → 整个 service 卡死 → 但 ALB 返 504 看不出根因」
## L7. "DB pool saturation looks like 504s upstream — root cause is invisible without `hikaricp_connections_pending`"

**Pattern observed live during this skill's evaluation (2026-04-28)**:
- US `hikaricp_connections_active{job="prod-us-iot-service"}` 7-day max = 50 = `pool_max`.
- US `hikaricp_connections_pending` 7-day max = 534 → 534 threads queued waiting for a DB connection.
- These episodes don't appear in HTTP error rate; they appear as **P99 latency spikes** (already observed: 7d P99 max 9,164 ms).

**Detection rule**: any new DB read on a hot path **must** include `hikaricp_connections_pending` in the design doc's load-assessment table — not just `_active`. A pool with `active = max` but `pending = 0` is fine; `active = max` with `pending > 0` is already saturating.

---

## L8. 「服务用 Undertow，不是 Tomcat — 但 dashboard 仍写 `tomcat_threads_*`」
## L8. "Service uses Undertow but dashboards reference `tomcat_threads_*` (silent zero)"

**Discovered during this skill's research (2026-04-28)**: iot-service-cloud doesn't expose any of `tomcat_threads_busy_threads`, `tomcat_threads_config_max_threads`, `undertow_*`, `executor_*`, or `process_threads`. The Bird-story incident's "worker thread pool saturated" wasn't visible in any metric — it was inferred from log timestamps and ALB 5xx pattern.

**Detection rule**:
- When estimating worker-thread budget for a new sync operation, **do not** assume `tomcat_threads_busy_threads` exists. Verify with `count by (job)(tomcat_threads_busy_threads)`.
- If the metric is missing, fall back to:
  - `jvm_threads_states_threads{state="runnable"}` as a noisy proxy.
  - Hikari `_pending` as a leading indicator (DB stall blocks worker threads first).
  - HTTP P99 jump as a downstream observable.
- **Long-term fix**: file an issue to expose Undertow XNIO worker-pool stats. This is a real instrumentation gap.

---

## L9. 「灰度路由白名单的流量集中倍数没人算」
## L9. "Gray-rollout whitelist concentration factor is rarely computed"

**From bird-story** (and pattern across multiple gray rollouts):
- 灰度路由不是按 % 均匀分流，而是按白名单（特定用户/租户/region）路由到 2-3 个 canary pod。
- 白名单流量在白名单成员里通常有 10-50× 的不均匀（high-frequency users 集中）。
- 加上 canary pod 数远小于 prod 总 pod 数（2-3 vs 124），单 pod 实际负载是 prod 平均的 几十到上百倍。

**Detection rule**: design docs that mention "灰度 X% 上线" must include:
- Concretely **which users / tenants / SNs** the gray router routes to canary.
- The QPS those users contribute (pull from a sibling counter, not from gut).
- The number of canary pods.
- Concentration factor = (whitelist QPS / num canary pods) ÷ (avg QPS per pod prod).
- If concentration > 5×, the canary itself is a load test — feature flag must have a gradual ramp before any meaningful sample.

---

## L10. 「`if (false)` 不是 kill switch；GrowthBook flag 才是」
## L10. "`if (false)` is not a kill switch — GrowthBook flag is"

**Project memory `project_growthbook_is_ab_standard.md` + `bird-story` resolution**: code-level `if (false)` requires a deploy to flip — too slow to mitigate an active incident.

**Detection rule**: any new code that meets ALL of the following must have a runtime kill switch (GrowthBook feature flag, NOT a `boolean` constant):
- Adds I/O on a path with > 100 QPS, OR
- Has fan-out > 1, OR
- Could be misconfigured by ops to cause runaway load (e.g. setting-override admin upsert)

The `setting-override` design's section 12 said "rollback by adding `if (false)`" — that's a one-line gate, but it requires a redeploy to flip. Upgrade to a feature flag before merge.

---

## L11. 「Sentry alert 噪音掩盖真问题」
## L11. "Sentry alert noise masks real signal"

**Commit `ac3ca398ca fix(airwallex): 401 credentials_invalid 不再触发 Sentry ERROR 噪音`**: a 401 from a known-broken-credential path was triggering Sentry ERROR every time, masking real Sentry signals.

**Detection rule**: when adding error logging, verify that retryable / expected-error paths are NOT logged at ERROR level. Use WARN with rate-limiter, or skip Sentry tag for the expected error class.

---

## L12. 「设计文档说"camera-only"但实现是无条件调用」
## L12. "Spec says X-only, implementation is X+Y unconditional"

**Discovered during this skill's setting-override review (2026-04-28)**: design said "Scope is limited to camera category"; implementation called `applySettingOverride` unconditionally at the tail of `initSetParameterRequest`, so hub category also pays the cost (and hub SN finds no row, but pays the DB read).

**Detection rule**: when reviewing, **read the implementation's call site against the design's scope clause**. If they diverge, that's a [CONFLICT:Q<n>].

---

## How to use this file in performance-preflight Step 7
## 在 Step 7 怎么用

When walking the 5-dimension review:
评 5 维 review 时：

- For **DB pressure** → check L4 (cache pattern) + L7 (Hikari pending).
- For **Thread pool** → check L1 (worker saturation) + L8 (no metrics, must use proxies).
- For **Async pool / memory** → check L5 (sync-on-connect storms).
- For **CPU** → no specific lesson here; check Goetz formula (in main SKILL.md §6.3).
- For **Failure modes / rollout** → check L9 (concentration) + L10 (kill switch) + L3 (cross-service).
- For **Observability** → check L2 (logs split) + L8 (instrumentation gaps) + L11 (alert noise).

If any rule fires, mark `[HISTORICAL:Lx]` in the assessment doc and require an explicit rebuttal before sign-off.
任一条命中，在 assessment 文档里标 `[HISTORICAL:Lx]`，签字前必须有显式反驳。

---

## How to refresh / 如何刷新

```bash
cd /Users/zouyijiang/workspace/iot-release && \
  git log --all --oneline --since="$(date -v-3m +%Y-%m-%d)" \
    --grep="fix\|incident\|hotfix\|outage\|p99\|leak\|deadlock\|saturation\|stampede" -i

cd /Users/zouyijiang/workspace/kiss && \
  git log --all --oneline --since="$(date -v-3m +%Y-%m-%d)" \
    --grep="fix\|incident\|hotfix\|outage\|p99\|leak\|deadlock\|saturation\|stampede" -i

# Read any new docs/incident-*.md files and add an entry per new pattern.
```

When a new incident lands a post-mortem, distill it into one numbered Lx entry above with:
当新事故出复盘文档，蒸馏为一条 Lx 条目，含：
- Canonical example (path/file/commit)
- What happened (3-5 lines)
- Why it eluded review (root cause of the review miss)
- Detection rule for this skill

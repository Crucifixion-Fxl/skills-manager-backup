---
name: performance-preflight
description: Senior-CTO-grade pre-launch performance review and optimization for a new code change — quantifies the extra DB/Redis/thread/connection/memory/CPU/network/tail-latency pressure the change adds, applies 30+ years of distilled internet/software performance wisdom (cache penetration/breakdown/avalanche, N+1 query, Little's Law, Goetz thread pool formula, USE/RED/4-Golden-Signals, circuit breaker, bulkhead, retry amplification, USL, Amdahl, "tail at scale", premature pessimization, etc.), derives closed-form frequency formulas from real Prometheus + 数仓 + troubleshooting data, locks any unobtainable number as a developer-supplied "expected value contract" with mandatory runtime guards, and produces a written 压力评估 + 优化建议 section that becomes part of the technical design. Both assessment and optimization are in scope. Auto-trigger on "性能压力评估", "性能前置评估", "performance preflight", "performance impact", "load assessment", "capacity review", "review 一下性能", "这个改动会不会压垮", "review my design 性能", "评审性能", "压测方案", "新增代码 QPS", "redis db 压力", "线程池压力", "连接池压力", "异步线程池内存", "缓存穿透", "cache penetration", "tail latency", "P99 抖动", or whenever a non-trivial code change is about to be merged on a hot path (login, push, livestream, device heartbeat, kafka consumer, scheduled job, http endpoint with > 1 QPS in prod).
---

# performance-preflight

性能前置审查 Skill —— 用资深 CTO 视角 review 一段**即将上线的代码变更**：量化它给现有系统带来的真实增量压力，**提出优化方案**，把"拍脑袋"的数字钉成**契约 (expected value contract)** + **契约违反时的兜底代码**，最后产出可以直接贴进技术方案的 `## 压力评估 + 优化建议` 章节。
评估和优化都在范围内 —— 像飞机起飞前的 preflight check 一样：**把可能在生产暴露的性能问题在合并前就发现并修掉**，而不是事后补救。

Performance Preflight Skill — review an **about-to-ship code change** through the lens of a senior CTO: quantify the real incremental pressure it puts on the running system, **propose optimizations**, nail every "guesstimate" as an **expected value contract** with **runtime guard code that fires when reality exceeds the contract**, and emit a `## Load & Capacity Assessment + Optimization` section that drops straight into the tech design.
Both assessment and optimization are in scope — like an aviation preflight check: **find and fix performance problems pre-merge, not after the incident**.

---

## Description
## 描述

Skill is a forced 8-step procedure that produces a `## 压力评估 + 优化建议` section ready to drop into a tech design doc. Inputs: a code change (MR / branch / patch) on a hot path. Outputs:
本 Skill 是一套强制的 8 步法，产出可直接贴进技术方案的 `## 压力评估 + 优化建议` 章节。输入：热路径上的代码改动（MR / 分支 / 补丁）。输出：

1. Industry-research declaration (cite which existing methods you reused vs invented).
2. Scope table (audience, entry point, call chain, change anchor).
3. Inventory of existing observable signals (PromQL / SQL / API recipes — see `references/`).
4. Per-call resource cost breakdown (DB / Redis / RPC / threads / memory / CPU).
5. Increment vs capacity table with hard red lines.
6. **Expected-value contracts (T1/T2/T3 tiered) with mandatory runtime guards** — the most critical deliverable.
7. Five-dimension review (DB / threads / connections / async pool / CPU) + 14 CTO-addendum dimensions.
8. Final assessment doc + post-launch verification + optimization recommendations.

Operational manual lives under `references/` (12 files): start at `references/README.md`.
操作手册见 `references/`（12 个文件），从 `references/README.md` 入手。

---

## Rules
## 规则

The 8-step workflow in §2 below is the rule body — every step is mandatory, no skipping, no reordering. Top-line constraints (also enforced individually within each step):
下面 §2 的 8 步法就是规则主体 —— 每步必做、不准跳、不准颠倒。顶层约束（每步内部也会再次落实）：

1. **Industry research before ANY design** — before proposing methodology, declare which existing skills / industry methods (Google SRE, Little's Law, USE/RED, "Tail at Scale", etc.) you reused vs newly added.
   **任何设计前先做调研** —— 提出方法前先声明用了哪些既有 skill / 业界方法，新增了什么。
2. **Never trust developer frequency estimates without cross-validation** — find a sibling counter on the same call chain and compute the ratio. Off by > 2× → mark `[CONFLICT:Q<n>]`.
   **未经交叉验证不要相信开发者的频率估计** —— 在同一调用链找兄弟 counter 算比值。差 > 2× 标 `[CONFLICT:Q<n>]`。
3. **Numbers that can't be auto-fetched become Step-6 contracts with runtime guards** — T1 contracts must have actual fallback code in the MR, not just a metric.
   **自动拿不到的数 → Step 6 契约 + 运行时 guard 代码** —— T1 契约必须有真实兜底分支，不是只埋指标。
4. **Use `references/` as the operational manual** — start at `references/README.md`; do not re-discover endpoints, job-labels, or magnitudes that are already documented there.
   **`references/` 是操作手册** —— 从 `references/README.md` 入手；已记录的端点 / job-label / 量级不要重新探。
5. **Walk `references/historical-lessons.md` before sign-off** — flag any matching pattern as `[HISTORICAL:Lx]` and require explicit rebuttal.
   **签字前过一遍历史经验** —— 命中模式标 `[HISTORICAL:Lx]` 并要求显式反驳。

---

## Examples
## 示例

### Bad
### 反例

```
User: "I'm adding a single GrowthBook feature-flag eval to /deviceMsg/config — minor change, no perf review needed."
AI: "Looks fine, ship it."
→ Bird-story 2026-04-23 P0: worker-thread pool saturated within 7 min on canary pods, ALB 5xx storm 1.5M/5min.
   The "minor" change was on a 3,300 QPS hot path; concentration factor on canary pods was ~50×.
```

```
User: "Dev says this query runs 50 QPS, that's the baseline."
AI: Locks 50 QPS as the contract value without cross-checking.
→ Real baseline (from a sibling counter on the same handler) was 5,500 QPS. Capacity calc off by 100×.
```

### Good
### 正例

```
User: "Adding 1 SELECT to /deviceMsg/setting build path."
AI: Step 1 declares method (SRE Workbook capacity planning + Little's Law).
    Step 3 pulls /deviceMsg/setting QPS from Prometheus = 3,295 (7d peak 5,588) via references/recipes/iot-service-cloud.md.
    Step 4 walks per-call cost: 1 DB SELECT, no JSON parse on miss path.
    Step 5 computes Δ = +5,588 ops/s on US prod = +4.7% of 119k SQL/s baseline. HikariCP 7d max active = 50 = pool_max!
    Step 6 contracts: C1 QPS_peak ≤ 5,588, C2 per-SN poll ≤ 1/12.4min, C5 backup-bytes p99 ≤ 32KB.
    T1 guards: GrowthBook kill switch + active-row hard limit (200k) with rejection.
    Step 7 §6.6: catches L1 (hot-path sync I/O) + L9 (canary concentration) historical lessons.
    Step 8 emits the assessment doc; verdict: Medium risk, blocked on filling C1-C7 + adding kill switch + Caffeine cache O1.
→ Catches the bird-story-class bug pre-merge, not via P0 incident.
```

---

## 1. 何时触发 / When to trigger

强触发 / Strong:
- 用户明确说 "性能压力评估 / performance impact / 压测方案 / capacity review / review 一下性能" 等。
- User explicitly says the above.

主动触发 / Proactive:
- 任何要并入 master / release 的代码改动，且改动落在以下任一路径上 / Any change about to merge onto:
  - 登录 / login, 推送 / push, 直播 / livestream, 设备心跳 / device heartbeat, 绑定 / binding
  - Kafka consumer / 定时任务 scheduled job
  - HTTP endpoint with prod QPS > 1
  - 任何 `@Cacheable` / `@Async` / `for (... : list) { redis|db ... }` 形态
  - 任何"为每个 SN / userId / requestId 多打一次 redis/db" 的代码
- 涉及"读 SettingOverride / 读配置 / 读规则 / fan-out 推送 / 批量遍历"等放大型操作 / fan-out style ops.
- 用户在做架构 review、写技术方案、提 MR 描述时。

不要触发 / Do NOT trigger:
- 纯文档改动、纯测试改动、纯重命名、纯 lint。

---

## 2. 强制工作流 / Mandatory workflow (8 steps, in order)

不允许跳步、不允许颠倒顺序。每一步必须显式输出，写到最终评估文档里。
No skipping, no reordering. Every step must produce explicit output that lands in the final doc.

### Step 1 — 调研已有方案 / Industry research first (HIGHEST priority)

在给任何评估方法之前，先 **3 分钟内** 完成以下检查并显式声明结论：
Before proposing any methodology, spend up to 3 minutes on:

1. 已有 skill 是否覆盖 / Existing skills already covering it:
   - `observability-design`（指标驱动）
   - `testing-strategy`（分层测试）
   - `architect`（技术方案体系）
   - `prometheus`、`grafana`、`troubleshooting`、`superset`（数据查询工具）
2. 业界已有的成熟方法 / Industry-established methods to reference (must cite by name, not invent):
   - Google SRE Workbook — "Capacity Planning" / "Non-Abstract Large System Design"
   - Netflix / Amazon "Back-of-the-envelope estimation"（费米估算）
   - USE method (Brendan Gregg) — Utilization / Saturation / Errors
   - RED method (Tom Wilkie) — Rate / Errors / Duration
   - Little's Law: `L = λ × W`（队列长度 = 到达率 × 平均等待时间），用来推线程池占用与连接池占用
   - Universal Scalability Law (Neil Gunther) — 估算横向扩容收益上限
   - 4 Golden Signals (Google SRE) — Traffic / Errors / Latency / Saturation
3. 同仓库的历史经验 / In-repo prior art:
   - 用 `git log --oneline --all | grep -iE "perf|capacity|qps|hot path|reduce"` 找最近 3 个月的优化提交
   - 用 `Grep` 在 `docs/` 找任何 `pressure|capacity|qps|hot path` 的设计文档

显式输出：`Research summary: 已有方法 X 覆盖 A/B，缺口在 C；本次评估借用 X+Y，新增 Z。`
Always print: `Research summary: existing methods cover A/B; gap is C; this assessment borrows X+Y and adds Z.`

如果跳过这一步，整个评估作废 / Skipping this step invalidates the whole assessment.

### Step 2 — 圈定改动的边界 / Scope the change

输出一张表 / Emit a table:

| 维度 / Dimension | 内容 / Content |
|---|---|
| 受众端 / Audience | App / 嵌入式设备 / 后端（多选）/ App / Embedded device / Backend (multi) |
| 入口 / Entry point | HTTP path / Kafka topic / Cron / gRPC method / WebSocket frame |
| 触发频率来源 / Trigger source | 用户操作 / 设备上报 / 定时 / 上游 fan-out |
| 调用路径 / Call chain | List every method node from entry to data store (Mermaid sequence preferred) |
| 改动落点 / Change anchor | 哪一行 / 哪几个方法是新增逻辑 |

### Step 3 — 盘点路径上现有的可观测信号 / Inventory existing signals

在新加任何代码前，先用现有的日志、指标、埋点估出 baseline。**不允许靠"我觉得 QPS 大概..."**，**也不允许直接相信开发者口头给的频率估计** —— 必须找到一个已有的兄弟指标做交叉验证。
Before adding anything, derive baseline from existing signals. **No "I think QPS is roughly..." allowed**, and **never trust the developer's frequency estimate at face value** — always cross-check against an existing sibling metric on the same call chain.

> **首选：从 `references/README.md` 入手**，按你要回答的问题挑文件读。该目录预先沉淀好域名、job-label 命名、当前数量级锚点、可直接跑的查询脚本、以及按项目分文件的 PromQL 配方。**只有当现成配方查不到值时**，才回退到"问开发者 + 锁 Step 6 契约值"。
> **Primary source: start at `references/README.md`** and pick the file that matches your question. The directory pre-builds endpoints, job-label conventions, point-in-time magnitude anchors, a runnable query helper, and per-project PromQL recipes. **Only when no recipe returns a value** do you fall back to "ask the developer + lock as a Step 6 contract value".
>
> Quick map of `references/`:
> - `endpoints.md` — Prometheus / Thanos / Grafana / Superset / DataHub URLs per region (US / EU / CN), datasource UIDs, dashboard UIDs.
> - `job-labels.md` — `prod-{region}-X` vs `{region}-prod-X` naming + how to discover unknown services.
> - `magnitudes.md` — current real values + cross-region ratios; anchors that catch "off by 10×" at a glance.
> - `query-helpers.py` — copy-paste-runnable Python (e.g. `python3 query-helpers.py thanos us '<promql>'`).
> - `recipes/<project>.md` — per-project recipes (iot-service-cloud / iot-consumer / kiss / state-machine / fallback).
> - `historical-lessons.md` — 12 lesson patterns mined from past incidents (bird-story, JWT, etc.) — walk before sign-off.
> - `first-principles-analysis.md` — 50 analysis methods beyond Prometheus when a hypothesis needs falsification.
> - `data-warehouse.md` — Superset SQL templates + DataHub schema-discovery flow.
> - `troubleshooting-bridge.md` — when troubleshooting platform answers stat questions Prometheus can't.
> - `workflow.md` — the 6-step Step-3 procedure + 10 anti-patterns.

强制查询顺序 / Mandatory query order:

1. **Open `references/recipes/<project>.md`** for the project that owns the entry point (iot-service-cloud / iot-consumer / kiss / state-machine / fallback). Copy the exact PromQL. Cross-check magnitude against `references/magnitudes.md` — if your number is off by 10× from the anchor, something is wrong.
   **打开 `references/recipes/<project>.md`** 入口归属的项目（iot-service-cloud / iot-consumer / kiss / state-machine / fallback），抄准确的 PromQL。把数与 `references/magnitudes.md` 锚点对一下 —— 偏离 10× 就是有问题。
2. **Run via the appropriate skill**:
   - `prometheus` skill — entry QPS, latency, error rate, custom counters
   - `grafana` skill — dashboards (especially `上线必看 - {区域}`), pre-built panels
   - `superset` skill — business volumes from the data warehouse (DAU, devices/region, P99 list-size for fan-out)
   - `datahub` skill — schema discovery before SQL
   - `troubleshooting` skill — logs (per the project's split rule; see references file §1.5 / §3.5)
3. **In-code grep** (always, even if the query returned a value — the registration site tells you what labels are emitted):
   `Grep "Counter\.build(|Counter\.builder(|MeterRegistry|Tracker\.track|LogUtil\.(info|warn|error)"` on the changed file + one level up the call stack.
   **代码内 grep**（哪怕查询有返回值也要做 — 注册点告诉你打了哪些 label）：
   在改动文件 + 上一级调用方 grep `Counter\.build(|Counter\.builder(|MeterRegistry|Tracker\.track|LogUtil\.(info|warn|error)`。
4. **GitLab fallback for repos not in `cwd`**: use `gh` / `glab` against `gitlab.addx.ai` to grep blob contents — **never** invent counter names.
   **本地没 checkout 的仓库走 GitLab 兜底**：用 `gh` / `glab` 在 `gitlab.addx.ai` 上 grep blob 内容 — **绝不**凭空编 counter 名。
5. **Cross-check the developer's frequency estimate**: pick a sibling counter on the same handler, compute (entry QPS) / (sibling rate); if the ratio is wildly different from what the developer said, flag `[CONFLICT:Q<n>]` and resolve before signing off.
   **对开发者的频率估计做交叉验证**：在同一 handler 找一个兄弟 counter，算 (入口 QPS) / (兄弟 rate)；如果比值与开发者所述差距悬殊，标记 `[CONFLICT:Q<n>]` 并在签字前解决。

输出 / Output:
- 一张 `信号清单` 表：信号名 / 类型 / PromQL 或 SQL / 当前值（带时间戳）/ 数据来源（链接）
- 显式标注哪些数据是缺失的 → 进入 Step 6 的"契约值"清单
- 显式记录每个开发者口头数字与最近的兄弟指标的比值，做为交叉验证证据
  Explicitly record the ratio of every developer-claimed number against the nearest sibling metric, as cross-validation evidence.

### Step 4 — 单次调用的资源成本 / Per-call cost breakdown

对**新增代码**做一次**逐行成本走查**，列出每次调用产生多少：
For the **new code**, walk every line and list per-invocation:

| 资源 / Resource | 次数 / Count | 静态 or 动态 / Static or dynamic | 公式（如果动态）/ Formula (if dynamic) |
|---|---|---|---|
| Redis GET/SET/DEL/MGET/Pipeline | | | |
| DB SELECT / INSERT / UPDATE / 事务次数 | | | |
| 跨服务 RPC / HTTP | | | |
| Kafka produce | | | |
| 本地 CPU 重活（regex / json / 加解密 / 压缩 / 大循环 / 递归）| | | |
| 内存分配峰值（per call）| | | |

**关键规则 / Critical rules**:

1. **N+1 / fan-out 必须显式标注** —— 任何 `for (... : list)` 内部命中 redis/db/RPC 都视为高危，必须给出 list 长度的 P50 / P99 / P999 三个值，并由 `list 长度 × 单次成本` 推总。
   Any loop that hits redis/db/RPC inside is high-risk. Must supply P50/P99/P999 of list length and compute total = length × per-iter cost.
2. **缓存命中率不允许默认 100%** —— Caffeine / Redis 缓存的命中率必须给出当前观测值或预期值（Step 6 契约），未命中那一支的成本必须单独算。
   Cache hit rate must be observed or contracted, never assumed 100%; miss-path cost computed separately.
3. **同一 key 在同一调用栈里命中多次 = 一次** —— 但前提是确实有 request-scoped 缓存，否则按多次算。
   Same key hit multiple times in one call counts as one **only if** request-scoped cache exists.
4. **所有动态次数都要给闭式公式** / Every dynamic count gets a closed-form formula:

   示例 / Example:
   ```
   redis GET 次数 = 1 (校验)
                  + N_devices × 1 (per-device override)
                  + (1 - hit_rate) × N_devices × 1 (DB 兜底)
   N_devices: 用户绑定设备数, P50=2, P99=15, 数据来源: superset query <link>
   hit_rate: Caffeine 60s, 当前未上线, 契约预期 ≥ 95%, 见 Step 6
   ```

### Step 5 — 增量频率推算 / Incremental frequency

把 Step 3 的 baseline QPS 乘以 Step 4 的单次成本，得到**增量** Redis QPS / DB QPS / 线程占用 / CPU 时间 / 内存分配速率。

Multiply Step 3 baseline QPS by Step 4 per-call cost to get **incremental** Redis QPS / DB QPS / thread occupancy / CPU time / mem alloc rate.

输出一张**增量表** / Emit an increment table:

| 资源 / Resource | 当前 / Current | 增量 / Δ | 增量后 / After | 容量上限 / Limit | 余量 / Headroom % |
|---|---|---|---|---|---|
| Redis QPS (cluster) | | | | | |
| MySQL QPS (instance) | | | | | |
| MySQL 连接数 / connections | | | | | |
| Redis 连接数 / connections | | | | | |
| Tomcat 线程占用 / busy threads | | | | | |
| 业务线程池 active | | | | | |
| Pod CPU (mCPU) | | | | | |
| Pod RSS (MB) | | | | | |
| Network egress (MB/s) | | | | | |

**判定红线 / Hard red lines** (任意一条触发就必须改方案 or 扩容 or 加开关):
- 任一资源 headroom < 20%
- 增量 > 当前 baseline 的 30%
- 高峰时段（取过去 14 天最高 5min 桶）下 headroom < 10%
- DB / Redis 连接池：`pod_count × max_threads_per_pod_using_conn > pool_max - 10%`

### Step 6 — 契约值 / Expected value contract（最重要 / most critical）

凡是**自动拿不到的数**（缓存命中率、设备数分布、用户列表大小、循环平均次数、消息体平均大小、上游 RPS 增长 12 个月预期等），开发者必须给出预期值并写入技术方案，进入合同状态。
Any number that **cannot be auto-fetched** (cache hit rate, device count distribution, list size, loop avg, msg size, upstream RPS growth, etc.) must be supplied as an **explicit expected value** by the developer and written into the design doc as a contract.

输出契约清单 / Emit contract list:

| Contract ID | 量 / Quantity | 预期值 / Expected | 上界 / Upper bound | 数据级别 / Tier | 违反后果 / If breached |
|---|---|---|---|---|---|
| C1 | Caffeine hit rate | ≥ 95% | < 90% (告警) | T1-Critical | DB QPS 翻倍 / DB QPS doubles |
| C2 | 用户 P99 设备数 | ≤ 15 | > 30 | T1-Critical | redis fan-out 翻倍 |
| C3 | Kafka 单条消息平均字节 | ≤ 2 KiB | > 10 KiB | T2-High | 网络 +5x |

**Tier 分级 / Tier classification**:
- **T1-Critical**: 违反 → 5 分钟内出事故。代码必须写 **runtime guard**（参见下一节）。
  Breach causes incident within 5 min. **Runtime guard is mandatory.**
- **T2-High**: 违反 → 1 小时内告警。代码必须埋指标 + 设阈值告警。
  Breach alerts within 1 hour. Metric + alert mandatory.
- **T3-Info**: 违反 → 容量规划阶段才发现。仅记录在文档。
  Discoverable at capacity planning. Doc-only.

**Runtime guard 模板** / Runtime guard pattern (必须写到代码里 / must be in code):

```java
// 性能契约 C2: 单用户 P99 设备数 ≤ 15 (来源:技术方案 §压力评估)
// Performance contract C2: device count P99 ≤ 15
private static final int DEVICE_COUNT_HARD_LIMIT = 100;  // 5x of P99 for safety
private static final int DEVICE_COUNT_SOFT_LIMIT = 30;

if (devices.size() > DEVICE_COUNT_HARD_LIMIT) {
    // T1: 直接拒绝, 防止打挂下游 / reject to protect downstream
    bugsnag.notify(new ContractViolationException("C2 hard limit", devices.size()));
    meterRegistry.counter("contract.violation", "id", "C2", "level", "hard").increment();
    throw new TooManyDevicesException(devices.size());
}
if (devices.size() > DEVICE_COUNT_SOFT_LIMIT) {
    // T1: 软上限, 走降级路径 (跳过 override / 用 stale cache)
    meterRegistry.counter("contract.violation", "id", "C2", "level", "soft").increment();
    return degradedPath(devices);
}
```

**禁止 / Forbidden**:
- 只埋指标不写 guard 代码（"以后再说"）—— T1 契约违反必须有兜底分支。
- 把硬上限设成 P99 本身（必须留 ≥ 3 倍安全边距）。
- 用 `if (size > N) log.warn` 充当 guard —— 日志不是 guard，必须有真实的拒绝/降级行为。

### Step 7 — 五维资源压力 review / Five-dimension resource review

按下面 5 个维度逐项过一遍，每项至少给出"Δ / 风险等级 / 缓解措施"：
Walk through 5 dimensions, each with "Δ / risk / mitigation":

#### 7.1 数据库压力 / DB pressure
- Redis QPS / DB QPS 增量
- 慢查询风险（新查询是否走索引？回表？大表 join？）
- 事务持有时间（是否在事务里调 RPC？是否 `for (...) { update }`？）
- 连接抖动（新查询是否长连接？是否会在高峰期挤占其它业务的连接？）
- 主从延迟（是否在写后立刻读？强一致需求？）

#### 7.2 线程池资源压力 / Thread pool pressure (Little's Law)
- 阻塞调用清单 / List every blocking call on the path (HTTP / RPC / DB / Redis / disk)
- 应用 Little's Law: `concurrent_threads = QPS × avg_latency`
- 计算 / Compute: `pod_count × concurrent_threads_per_pod` vs Tomcat / WebFlux / 业务自建线程池上限
- 检查同一线程池是否被多个慢调用复用 → 头部阻塞 (head-of-line blocking)

示例 / Example:
```
QPS = 200
新增阻塞 RPC 平均耗时 80ms
=> Δ concurrent_threads = 200 × 0.08 = 16
Tomcat max-threads = 200, 当前 busy P99 = 90
=> 余量 110, 增量 16, headroom 47%, 通过 / OK
```

#### 7.3 连接池资源压力 / Connection pool pressure
关键公式 / Key formula:
```
peak_connections = pod_count × (threads_using_conn_per_pod) × P99_concurrent_factor
```
- DB / Redis / HTTP client 连接池上限是 **数据源端 (DB / Redis 实例)** 的硬上限，不是单 pod 上限
- 必须把 **所有微服务的 pod 数 × 各自使用该数据源的线程数** 求和，再对比数据源 max_connections
- The pool max lives on the **data source (DB / Redis instance)**, not the pod
- Sum across **all microservices' pods × threads using that source**, then compare to data source max_connections

红线 / Red line:
- `peak_connections > data_source_max × 0.7` → 必须扩容数据源 or 加连接池排队 or 用本地缓存抵消

#### 7.4 异步线程池 + 内存压力 / Async pool + memory
- `@Async` 任务里的 lambda 闭包了哪些大对象？(Request body / List<DeviceInfo> / 整个 Map<String,Object>?)
- 任务平均存活时间？任务到达率？队列容量？
- 用 Little's Law 算队列长度: `queue_len ≈ arrival_rate × avg_service_time × pool_busy_ratio`
- 单个任务持有的引用大小 × 队列长度 = 常驻堆内存增量
- 队列无界 (`Integer.MAX_VALUE`) 是 **永远的红线** —— 必须显式给出有限容量 + 拒绝策略 (CallerRuns / Discard / 抛异常)

示例计算 / Example:
```
推送任务平均闭包 ~5KB
到达率 1000/s, 平均处理 50ms
=> 在途任务 ≈ 50, 堆 ≈ 250 KB, OK
但若 burst 到 10k/s + DB 慢到 500ms
=> 在途 5000, 堆 25 MB, 队列若无界则一直涨, 必拒绝
```

#### 7.5 计算 / CPU 压力 / Compute pressure
- 递归深度 / 循环次数上限是否封顶？(`while (...)` / `recurse(...)` 没有 max-iter 直接判红)
- O(n²) 以上算法在 n 不可控时必须换算法或加上限
- 加解密 / 压缩 / JSON / 正则的成本 — 单次 ms 级也可能在 1k QPS 下吃满核
- 是否在 NIO / Reactor 线程里跑 CPU 重活 → 阻塞 event loop

#### 7.6 [CTO 补充] 其它常被忽略的维度 / Often-missed dimensions

CTO 视角下你（用户）漏掉的几条 / What you (as a junior CTO) missed:

| 维度 / Dimension | 关注点 / Focus | 红线 / Red line |
|---|---|---|
| 网络出口带宽 / Network egress | 单条 response / event 字节数 × QPS | > pod NIC 30% 或 > 跨 region egress 配额 |
| GC 压力 / GC pressure | 短命大对象分配速率 (alloc rate) | YGC 频率 > 1/s 或 P99 STW > 200ms |
| 锁竞争 / Lock contention | `synchronized` / `ReentrantLock` / `synchronized Map` 在热路径 | 持锁内含 IO，或 lock wait p99 > 10ms |
| Fan-out 放大 / Fan-out amplification | 1 次入站 → N 次出站 (推送 / 通知 / 索引) | N 上界不可控 → 直接判高风险 |
| 冷启动 / Cold start | 上线后第一波缓存全 miss、连接池全冷 | 启动后 5min 是否会击穿下游 |
| 队列堆积 / Queue backlog | Kafka consumer lag / DelayQueue / Redis Stream | 单分区 lag > 10s 流量 |
| 下游契约影响 / Downstream contract | 你新增的调用是否给下游 (PaaS / 第三方) 带来 QPS 飙升 | 下游 SLA 是否容纳此增量？是否要先发邮件 |
| 多区 / 多租户隔离 / Multi-region & tenant isolation | 增量是否在所有 region / tenant 都算过 | 某 region pod 数少 → 同样增量百分比更高 |
| 重试放大 / Retry amplification | 失败时是否重试？最多几次？是否带 jitter？ | 故障期内重试 × 上游故障 = 雪崩 |
| 限流 / Rate limit | 是否需要新加限流？阈值怎么定？是按 user/tenant/global？ | 没有限流就上线 = 等故障 |
| 灰度 / Rollout | 是否有 feature flag / 按 tenant / 按 % 灰度？回滚开关在哪？ | 全量上线 = 没有回滚 |
| 长尾 / Tail amplification | 你算 P99 时上游 P99 已经被新代码再放大一次 | P999 业务影响 |
| 时间轮转 / Time-of-day | 是否按时区 / 推送高峰 / 早 8 点等场景重新算高峰 | 用平均 QPS 评估而非高峰 |
| 失败模式 / Failure modes | 如果新代码抛异常会怎样？是否影响主路径？降级路径有没有被测过？| 见项目级 Inviolable Invariant 规则 |

### Step 8 — 输出最终评估文档 / Emit final assessment doc

把以上 1-7 整理成一段可以直接贴进 `docs/architecture/<feature>/load-assessment.html` 的内容。模板：

Aggregate Steps 1-7 into a doc that drops into `docs/architecture/<feature>/load-assessment.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>压力评估 / Load & Capacity Assessment — &lt;feature name&gt;</title>
</head>
<body>
  <main>
    <h1>压力评估 / Load & Capacity Assessment — &lt;feature name&gt;</h1>
    <dl>
      <dt>评估人 / Reviewer</dt><dd>&lt;name&gt;</dd>
      <dt>评估时间 / Date</dt><dd>&lt;YYYY-MM-DD&gt;</dd>
      <dt>关联 MR / Related MR</dt><dd>&lt;link&gt;</dd>
      <dt>关联 US / Related US</dt><dd>&lt;link&gt;</dd>
    </dl>

    <section id="research-summary"><h2>0. Research summary</h2><p>&lt;industry method 引用 + 仓内已有方法 + 本次新增&gt;</p></section>
    <section id="scope"><h2>1. 改动范围 / Scope</h2><p>&lt;Step 2 表格&gt;</p></section>
    <section id="existing-signals"><h2>2. 路径上的可观测信号 / Existing signals</h2><p>&lt;Step 3 表格 + Prometheus / superset / troubleshooting 链接&gt;</p></section>
    <section id="per-call-cost"><h2>3. 单次调用资源成本 / Per-call cost</h2><p>&lt;Step 4 表 + 公式&gt;</p></section>
    <section id="increment-capacity"><h2>4. 增量与容量 / Increment &amp; capacity</h2><p>&lt;Step 5 增量表 + 红线判定&gt;</p></section>
    <section id="contracts"><h2>5. 契约值 / Expected value contracts</h2><p>&lt;Step 6 契约表 + Tier 分级 + runtime guard 引用代码位置&gt;</p></section>
    <section id="review"><h2>6. 五维 + CTO 补充 review / Five-dimension + CTO addendum</h2><p>&lt;Step 7 全部子项&gt;</p></section>
    <section id="risk-verdict">
      <h2>7. 风险等级与决策 / Risk verdict</h2>
      <ul>
        <li>整体风险 / Overall: Low / Medium / High / Block</li>
        <li>上线策略 / Rollout: 全量 / 灰度 X% / feature flag</li>
        <li>回滚开关 / Kill switch: &lt;code location&gt;</li>
        <li>监控告警新增项 / New alerts: &lt;list&gt;</li>
        <li>容量扩容需求 / Capacity ask: &lt;list, owner, ETA&gt;</li>
      </ul>
    </section>
    <section id="post-launch">
      <h2>8. 后续验证 / Post-launch verification</h2>
      <ul>
        <li>上线后 24h 必看的图 / 24h dashboards: &lt;links&gt;</li>
        <li>契约真实值回填窗口 / Contract backfill window: &lt;date&gt;</li>
        <li>若契约违反, 本次评估的 owner 负责回滚 / Owner on contract breach: &lt;name&gt;</li>
      </ul>
    </section>
  </main>
</body>
</html>
```

---

## 3. 反模式清单 / Anti-patterns to call out aggressively

每次评估必须主动检查这些**反模式**，命中即直接判 High 风险：
Every assessment must actively scan for these anti-patterns; any hit = High risk:

1. **「以后再优化」/ "Optimize later"** —— 新代码上热路径前必须先评估。代码已上线再做评估只能写事故复盘。
2. **「QPS 不高」/ "QPS is low"** —— 不允许只看平均。必须看 P99 5min 桶 + 节假日/活动峰值 + 12 个月预期。
3. **「缓存能扛住」/ "Cache will handle it"** —— 没有命中率契约的缓存等于没有缓存。冷启动、缓存击穿、雪崩三连必须显式回答。
4. **「DB 还有余量」/ "DB has headroom"** —— 必须看的是连接数 / IOPS / CPU / 锁等待 / 主从延迟，不是单一 QPS。
5. **「队列消化得了」/ "Queue can absorb it"** —— 无界队列、无拒绝策略、无 lag 告警 = 等故障。
6. **「线程池调大就行」/ "Just bump the pool"** —— 线程池调大 → 连接池被打爆 → 全局阻塞，是常见连锁反应，必须前后联动算。
7. **「日志输出关心啥」/ "Logs are cheap"** —— 热路径上 `log.info` 序列化大对象，磁盘 IO + GC 双杀。
8. **「埋指标就行了」/ "Just add a metric"** —— T1 契约违反必须有兜底代码，不只是指标。
9. **「测试环境跑过了」/ "Worked in staging"** —— staging 没有真实 fan-out / 真实设备数 / 真实多租户分布，不算证明。
10. **「只是改了个常量」/ "Just changed a constant"** —— 常量改动若改变了循环次数 / 缓存 TTL / 重试次数，必须按完整流程评估。
11. **「我加了 try-catch」/ "I added try-catch"** —— 异常被吞 ≠ 容量问题被解决；该限流的还是要限流，该降级的还是要降级。
12. **「等灰度看效果」/ "Just gray-roll it"** —— 灰度只能减小 blast radius，不能替代评估；契约该写还是要写。

---

## 4. 受众适配 / Audience-specific notes

### 4.1 后端 / Backend (主战场 / primary)
按上面 8 步走完整流程。重点查 DB / Redis / 线程 / 连接 / 异步池 / GC / 网络。
Walk all 8 steps. Focus on DB / Redis / threads / connections / async pool / GC / network.

### 4.2 App (Claude knowledge)
- 启动耗时 / cold start (冷启 P95 < 2s)
- 主线程阻塞 / main thread blocking (Android ANR 5s, iOS watchdog 10s)
- 网络请求合并 / network batching (don't fan out N parallel HTTPS on launch)
- 内存峰值 / memory peak (Android low-mem device 192 MB cap)
- 电量与流量 / battery & traffic (后台轮询频率 ≤ 1/15min, payload < 5 KB)
- App 增量评估必须考虑 **离线 → 上线** 的 burst：海量 device 同时连回，对后端是叠加压力。

### 4.3 嵌入式设备 / Embedded device (Claude knowledge, user 不熟悉)
- RAM 极少 (常 < 256 MB), 必须算单条 payload 字节
- Flash 写入次数有寿命 (~10k erase cycles), 高频写日志会损耗硬件
- 弱网 + 高丢包：每次 retry 翻倍流量
- 长连接保活: ping 周期 × 设备数 = 后端 Redis/连接池压力（**经常被忽略**）
- OTA / 灰度回滚困难: **嵌入式上线一旦有性能问题，回滚周期是天而不是分钟，所以上线前评估的标准要更严**
- 时钟漂移: 定时任务在大量设备上同时触发 → 后端瞬时尖峰 (thundering herd)，必须加 jitter

---

## 5. 与其它 skill 的协作 / Skill collaboration

| 阶段 / Stage | 用哪个 skill / Use which skill |
|---|---|
| 取 Prometheus 数据 / Pull Prometheus | `prometheus` |
| 取 Grafana 面板 / Pull Grafana | `grafana` |
| 取数仓数据 / Query warehouse | `superset` / `datahub` |
| 取日志频率 / Log frequency | `troubleshooting` |
| 评估完后写 issue / File issues | `gitlab-issue-sop` |
| 评估完后埋监控 / Add metrics & alerts | `observability-design` + `grafana-dashboard-alert-update` + `sla-metric` |
| 评估完后改代码 / Implement guards | `dev-workflow` 或 `small-feature-flow` |
| 真出事故 / Real incident | `root-cause-analysis` |

本 skill 只负责"评估方法 + 产出文档 + 守住契约"。
This skill only owns "method + doc + contract guarantee".

### References — 项目级精确指标查询配方 / Project-level precise metric recipes

| File | What it records / 记录什么 |
|---|---|
| [`references/README.md`](references/README.md) | 入口文件 — 列出 references/ 下所有子文档及其职责（endpoints / job-labels / magnitudes / query-helpers / per-project recipes / historical-lessons / first-principles / data-warehouse / troubleshooting-bridge / workflow）。Step 3 强制先读。<br>Entry file — lists every sub-doc under references/ and its purpose. Step 3 must read this first. |

References 区别于 SKILL.md 的方法论：references **记录"在这家公司这些项目里去哪查、查什么、怎么解读"** 的运营事实；SKILL.md 记录**通用方法论**。新增项目或大规模改动指标命名时，把变更落到 references；不要把它写回 SKILL.md。
Distinction: SKILL.md owns the universal methodology; references owns the operational facts ("at this company, in these projects, where to query, what to query, how to interpret"). When a new project is added or metrics are renamed at scale, update the references file — do not push the change back into SKILL.md.

---

## 6. 经典性能模式 + 行业定律 / Classical Performance Patterns + Industry Laws

互联网/软件行业 30+ 年沉淀下来的性能问题与解法，每次评估都要主动扫一遍是否命中。**很多时候你不是在发现新问题，而是在重复别人 20 年前踩过的坑**——先核对是不是经典模式，能省 90% 调研时间。
30+ years of internet/software industry distilled wisdom. Every assessment must scan these patterns. **Most "new" perf problems are repeats of patterns that were named decades ago** — match against the classics first to save 90% of investigation time.

### 6.1 缓存模式与陷阱 / Cache patterns & traps

| 模式 / Pattern | 中文名 | 触发条件 / Trigger | 经典解法 / Classic fix |
|---|---|---|---|
| **Cache penetration** | 缓存穿透 | 大量请求查不存在的 key, 每次都打到 DB | **Bloom filter** 负向 fast-path / 缓存 null 值 + 短 TTL |
| **Cache breakdown** | 缓存击穿 | 单个热 key 过期瞬间, 并发大量打到 DB | Mutex / `singleflight` 合并并发 fetch / 概率提前刷新 (β·TTL 前主动 refresh) |
| **Cache avalanche** | 缓存雪崩 | 大量 key 同时过期, DB 瞬时被打爆 | TTL 上加随机抖动 (e.g. `ttl + rand(0, 0.2*ttl)`) |
| **Stale read after write** | 写后读到旧值 | 写入还没传播到 cache | Write-through / write-invalidate / read-your-write 显式绕过 cache |
| **Negative cache 副作用** | null 缓存副作用 | 缓存 null 后, 数据真的写入也读不到 | 写路径必须主动 evict null entry / 用版本号触发刷新 |
| **5 大缓存策略** | | 选错策略 → 一致性 / 性能 / 复杂度三角崩塌 | **Cache-aside** (默认), **Read-through**, **Write-through**, **Write-behind** (写性能), **Refresh-ahead** (热 key) |

### 6.2 数据库经典 / Database classics

| 模式 / Pattern | 表征 / Symptom | 经典解法 / Classic fix |
|---|---|---|
| **N+1 查询** / N+1 query | ORM 取列表后逐项 lazy-load | JOIN / batch fetch / `IN (...)` / DataLoader pattern |
| **Missing index / 慢查询** | 全表扫描 / filesort / temporary table | `EXPLAIN` 必看;覆盖索引;不要 `SELECT *` |
| **长事务 + 锁竞争** / Long tx + lock contention | 事务里调 RPC / 等用户输入 | 事务只包数据修改;RPC 移到事务外 |
| **热行 / 热分区** / Hot row / hot partition | 单 SKU / 单 shard 把整个集群拖慢 | 拆分热 key (sharding by hash);乐观锁 + 重试;批量合并 |
| **OFFSET 大分页** | `LIMIT 100 OFFSET 100000` 越来越慢 | **游标分页** (cursor pagination): `WHERE id > last_id` |
| **从库延迟** / Replica lag | 写后立刻读从库读不到 | 写后短期强制读主库 / session pinning / GTID 等待 |

### 6.3 并发与线程池 / Concurrency

**Brian Goetz 线程池公式** (《Java Concurrency in Practice》):
```
N_threads = N_cpu × U_cpu × (1 + W/C)

N_cpu  = 物理核数 / CPU cores
U_cpu  = 目标 CPU 利用率 (0~1)
W/C    = 等待时间 / 计算时间 (IO-bound 任务比值高)
```
- IO-bound (W/C ≫ 1): 线程数 ≫ N_cpu, 几百-几千合理
  IO-bound: many threads OK
- CPU-bound (W/C ≈ 0): 线程数 ≈ N_cpu, 多了反而争 cache + context switch
  CPU-bound: don't overprovision

**Little's Law** — 后端容量评估的"E=mc²":
```
L = λ × W

L = 并发量 / concurrent in-flight
λ = 到达率 / arrival rate (QPS)
W = 平均处理时间 / avg response time
```
应用 / Use cases:
- 推算线程池占用: `busy_threads = QPS × P99_latency`
- 推算连接池占用: `active_conns = QPS × avg_db_latency`
- 推算队列长度: `queue_len = arrival_rate × avg_service_time`

### 6.4 Nygard《Release It!》可靠性模式 / Reliability patterns

| 模式 / Pattern | 解决的问题 / Problem solved |
|---|---|
| **Circuit Breaker** | 下游挂掉时不要继续打,快速失败 + 自动半开探测 (Hystrix / resilience4j / sentinel) |
| **Bulkhead** | 一个慢调用不能耗光全部线程 → 不同调用走不同线程池/连接池 |
| **Timeout + Retry + Backoff + Jitter** | 永远要带 timeout;重试要指数退避 + 抖动,**带最大次数上限**;否则故障期内重试 = 雪崩放大 |
| **Idempotency key** | 重试必须幂等; 写操作一定要带 idempotency-key 让下游能去重 |
| **Steady State** | 长时间运行的进程不能无限增长内存/连接/日志 (有 leak 的服务都靠重启续命) |
| **Fail Fast** | 拿不到下游就立刻失败,不要慢慢 retry 把上游拖垮 |
| **Throttling / Rate limiting** | 入口必须限流;**Token bucket** (允许 burst) vs **Leaky bucket** (强匀速) |
| **Test Harness** | 在 staging 注入慢响应/失败/超时,验证 circuit breaker / bulkhead 真起作用 |

### 6.5 可扩展性定律 / Scalability laws

**Amdahl's Law** (1967) — 串行瓶颈定律:
```
Speedup(N) = 1 / (s + (1-s)/N)

s = 串行部分占比 / serial fraction
N = 并行度 / parallelism
```
含义: 只要有 5% 串行 (锁/单点),最大加速比就被钉死在 20× 以内。**不要花钱买 100 核 CPU 解决一个有锁瓶颈的问题**。
Insight: 5% serial caps speedup at 20× regardless of cores. Don't throw cores at lock contention.

**Universal Scalability Law** (Gunther 1993) — 比 Amdahl 更狠:
```
C(N) = N / (1 + α(N-1) + β·N(N-1))

α = 串行干扰 / contention (Amdahl 部分)
β = 一致性代价 / coherence cost (节点间同步)
```
含义: 加节点不仅有边际递减,**β·N²** 项会让吞吐**先涨后跌** —— 这就是为什么有些系统加机器反而变慢 (锁/缓存一致性/分布式协调代价超过单机收益)。
Insight: adding nodes can DECREASE throughput due to coherence cost. Test, don't extrapolate.

### 6.6 监测方法 / Monitoring methodologies

| 方法 / Method | 作者 / Author | 适用 / Use for |
|---|---|---|
| **USE** (Utilization / Saturation / Errors) | Brendan Gregg | 资源 (CPU / mem / disk / network), 找瓶颈 |
| **RED** (Rate / Errors / Duration) | Tom Wilkie (Weaveworks) | 服务 (HTTP / RPC), 找异常 |
| **4 Golden Signals** (Traffic / Latency / Errors / Saturation) | Google SRE Book | 用户面 SLI, dashboard 必有 |
| **Layered SLO** | Google SRE Workbook | SLO = SLI + 阈值 + 时间窗;多层服务 SLO 要乘起来 |

每次评估都要回答: 新代码上线后, 我看 USE / RED 哪几条曲线? 哪条是 leading indicator?
Every assessment: which USE / RED curves do you watch post-launch? Which is the leading indicator?

### 6.7 长尾延迟 / Tail latency (Dean & Barroso, "Tail at Scale", CACM 2013)

**核心洞察 / Core insight**: 当一个请求 fan-out 到 N 个服务,即使每个服务 P99 只有 1%,**整体 P99 ≈ 1 - (1 - 0.01)^N**:
- N=10: 整体 P99 ≈ 9.6%
- N=100: 整体 P99 ≈ 63%
- 单服务的"罕见慢请求"在 fan-out 下变成"几乎必现"

**经典解法 / Classic remediations**:
- **Hedged requests** — P95 没回就并发再发一个,谁先回用谁 (代价: 多 5-10% 流量,换取尾延迟大幅下降)
- **Tied requests** — 备份请求带"取消"原请求的能力,避免重复工作
- **Speculative retry** — 预计算可能慢的请求,提前 backup
- **Micro-partitioning** — 把请求拆得更小,慢节点的影响面更小
- **避免同步串行 fan-out** — 必须并行,不要 `for { rpc }`

### 6.8 网络与序列化 / Network & serialization

| 模式 / Pattern | 关注点 / Focus |
|---|---|
| **N small calls vs 1 batch** | RTT × N 是真成本;能 batch 就 batch (`mget` vs N×`get`) |
| **HTTP keep-alive 必开** | TLS handshake ~30-100ms,新建连接是大头 |
| **Compression tradeoff** | gzip CPU 成本 vs 网络节省;< 1KB 不值得压;mobile 必压 |
| **Protobuf / msgpack vs JSON** | 体积 ~30%,解析快 5-10×;但调试可读性差 |
| **DNS / TLS 预热** | 冷启 / 灰度 / 切流 时主动预热,避免首请求长尾 |

### 6.9 内存与 GC / Memory & GC (JVM 重点)

| 反模式 / Anti-pattern | 后果 / Consequence |
|---|---|
| 静态 Map / List 无界增长 | 经典 leak (e.g. `ConcurrentHashMap` 当 cache 用却不清理) |
| ThreadLocal 没 remove | 线程池场景下漏到下一个请求 |
| ClassLoader leak | 热部署 / OSGi 下漏 ClassLoader,堆外可用空间一路下降 |
| 大对象短命 (`alloc rate` 高) | YGC 频繁 → STW 累积 → P99 抖动 |
| `String.format` / `+` 在循环里 | 用 `StringBuilder` |
| Boxing in hot path | `Integer` vs `int`, `for (Integer i : list)` |
| Reflection 在热路径 | 缓存 `Method` / `Field` 引用 |
| 无界队列 / unbounded queue | OOM 必现 (`Executors.newFixedThreadPool` 默认 unbounded queue!) |

### 6.10 反认知陷阱 / Cognitive traps to fight

> "**Premature optimization is the root of all evil**" — Knuth (1974)

但同等重要的反话 / But equally important counter:

> "**Premature pessimization is just as harmful**" — Herb Sutter
> 写明显低效的代码 (O(n²) 循环 / N+1 查询 / 同步串行 fan-out) 不是"保持简单",而是**给未来的自己埋雷**。
> Writing obviously inefficient code isn't "keeping it simple" — it's seeding future incidents.

边界 / Boundary:
- **过早优化的危害**: 牺牲可读性换一个不在热路径上的微秒级提升 → ❌
  Trading readability for microsecond gains off the hot path → ❌
- **过早悲观的危害**: 在可能进入热路径的代码里写 N+1 / 同步 fan-out / 无界队列 → ❌
  Writing N+1 / sync fan-out / unbounded queues in code that might land on hot path → ❌
- **正确做法**: 写算法/数据结构合理的代码 (大 O 没退化) + 可读 + **有可观测信号**;真正的微优化等数据说话。
  Right path: correct big-O + readable + **instrumented**; real micro-optimizations follow data.

### 6.11 行业经典书 / Industry references

每次评估给意见时,如果能引用,优先引用 (比"我觉得"权威):
When citing, prefer these over "I think":

| 主题 / Topic | 书 / Book |
|---|---|
| 数据系统总论 / data systems | **DDIA** (Designing Data-Intensive Applications) — Kleppmann |
| SRE 总论 / SRE | **SRE Book** + **SRE Workbook** — Beyer et al, Google |
| 并发 / concurrency | **Java Concurrency in Practice** — Goetz |
| 容量与稳定性 / stability | **Release It!** (2nd ed) — Nygard |
| 数据库 / DB | **High Performance MySQL** — Schwartz, **Database Internals** — Petrov |
| 性能工具 / perf tools | **Systems Performance** — Brendan Gregg, **BPF Performance Tools** — Gregg |
| 分布式 | **Designing Distributed Systems** — Burns |
| 论文 / papers | **"The Tail at Scale"** — Dean & Barroso, CACM 2013<br>**"Universal Scalability Law"** — Gunther |

---

## 7. 经验沉淀 / Lessons (思路, 不是细节 / lessons, not specifics)

来自历次"为热路径加新代码后被 redis/db 抓住"的优化经验，提炼出可复用的思路：
Distilled from past hot-path-optimization MRs, the reusable lessons (not the change details):

1. **每次入站 → 多次下游** 的代码形态，是性能事故第一来源。识别它的特征：方法签名是 `processOne` 但内部 `for (...) { redis|db|rpc }`。
   "1 inbound -> N downstream" is the #1 source of incidents. Signature looks single-shot but loop hits store inside.
2. **本地短 TTL 缓存 (Caffeine 30-60s)** 是把 DB 流量降一个数量级的最便宜手段。但前提：业务可以容忍最多 TTL 秒的过期数据，且**必须把这个容忍度写进契约 / written into the contract**。
3. **缓存命中率不是"加了就有"** —— 必须在线观察 1-3 天后才知真实值。所以契约里要给 **预期值 + 验收窗口 + 不达标的回滚措施**。
   Hit rate isn't "cache it and forget it" — observe 1-3 days, contract must include expected + window + rollback if missed.
4. **DAO / Service 层加缓存 vs 调用方加缓存** —— 优先在最靠近数据源的那一层加，避免每个调用方各自缓存导致缓存击穿和数据不一致。
   Cache as close to source as possible, not at each caller.
5. **可观测先于优化** —— 先埋指标看真实数据，再决定要不要优化。"我觉得这里慢"导致的过度优化，破坏代码可读性而真实瓶颈在别处。
   Observe before optimizing. "I feel this is slow" optimization is often misplaced.
6. **契约破裂的兜底必须真的能跑** —— guard 路径必须有 unit test / integration test 覆盖，不然出事时 guard 自己抛异常等于雪上加霜。
   Guard paths must be tested; an untested guard fails when you need it most.
7. **优化前后必须给数字证明** —— "感觉快了"不算。MR 描述里贴 before/after Prometheus 截图或 query。
   Before/after numbers required; "feels faster" doesn't count.

---

## 8. 输出风格 / Output style

- 全程 **双语输出 / bilingual** (一行英文一行中文)，源代码块除外。来自用户/项目级 `AGENTS.md` 或等价 agent 指令。
- 每个评估意见必须能落到 **具体代码行号 + 具体数据 link**。
  Every comment must land on **a specific line + a specific data link**.
- 不允许用 "可能 / 大概 / 应该没问题"，要么给数字 + 数据源，要么写进 Step 6 契约。
  No "probably / should be fine" — give numbers + sources, or move it to Step 6 contract.
- 像资深 CTO：不绕弯，直接判 Block / High / Medium / Low + 给可执行的下一步。
  CTO voice: blunt verdict + actionable next step.

---

## 9. 触发后第一句话 / First-line script when invoked

skill 被触发后，必须以这段话开场，确认拿到所有必要输入：
On invocation, open with this to confirm inputs:

```
我将以资深 CTO 视角对 <change description> 做性能压力评估，按 8 步法走：
I will run an 8-step CTO-grade performance impact assessment on <change description>:

1. Industry research (3 min)
2. Scope the change
3. Inventory existing signals (Prometheus / 数仓 / troubleshooting)
4. Per-call resource cost
5. Increment vs capacity, red lines
6. Expected-value contracts (T1/T2/T3) + runtime guards
7. Five-dimension review + CTO addendum
8. Final assessment doc

开始前请确认 / Before I start, please confirm:
- [ ] 改动的 MR / 分支链接 / MR or branch link?
- [ ] 入口路径 (HTTP / Kafka / Cron / RPC) / Entry point?
- [ ] 受众端 (App / 嵌入式 / 后端) / Audience?
- [ ] 当前所在环境 (dev / staging / prod) / Current env?
- [ ] 是否已有 baseline 数据可用 / Baseline data already available?

如果你说 "go"，我用合理默认值开跑 / Say "go" and I'll proceed with reasonable defaults.
```

---

## 10. 对 user 原方案的 CTO review 总评 / CTO review of the user's original outline

**保留的强项 / Strengths kept**:
- 五维资源拆分（DB / 线程池 / 连接池 / 异步内存 / CPU）方向正确，已成为本 skill 的 Step 7 主轴。
- 强调"自动拿不到的值要进契约 + 实际值大于预期要写兜底代码" —— 这是本 skill 最有价值的产物，被强化为 Step 6 + Tier 分级 + runtime guard 模板。
- 强调用 Prometheus / troubleshooting / 数仓 拿真实数据而不是拍脑袋 —— 落到 Step 3。
- 引用历史 MR 经验但不写细节 —— 落到 §6 经验沉淀，提炼为可复用思路。

**CTO 补充的盲区 / CTO-added blind spots**:
- 缺少**业界方法引用** (SRE Workbook / Little's Law / USE / RED / 4 Golden Signals) —— 加在 Step 1。
- 缺少**容量红线判定标准** (headroom 20% / 增量 30% / 连接池公式) —— 加在 Step 5。
- 缺少**契约 Tier 分级**和**runtime guard 强制要求** —— 加在 Step 6。
- 缺少**网络 / GC / 锁 / fan-out / 冷启 / 队列堆积 / 下游契约 / 多区 / 重试 / 限流 / 灰度 / 长尾 / 时区高峰 / 失败模式** 这 14 条常被忽略的维度 —— 加在 Step 7.6。
- 缺少**反模式雷达** (12 条) —— 单独拉出 §3，每次评估必扫。
- 缺少**App / 嵌入式特性的针对性条目** (ANR / 冷启 / Flash 寿命 / 设备 thundering herd) —— 加在 §4.2 / §4.3。
- 缺少**与其它 skill 的协作分工** —— 加在 §5，避免造轮子。
- 缺少**最终文档模板** —— 加在 Step 8，让评估直接落到 docs/。
- 缺少**触发开场白 + 必要输入确认** —— 加在 §8，避免漏问。
- **最关键 / most important**: 强化"评估不是 PPT，而是**契约 + 兜底代码**"这一立场——这是把性能压力评估从"事后补救"提升到"上线前阻断"的核心。

# First-Principles Analysis Methods — Beyond Standard Prometheus
# 第一性原理分析方法 — 超越标准 Prometheus

**Why this file exists**: Step 3 in the main workflow says "inventory existing signals". Most users (and most LLM defaults) reach only for app-level Prometheus. But there are dozens of signal sources at different abstraction levels — kernel syscalls, JVM internals, kernel-buffer states, network packets, GC logs, profiler samples — each better than Prometheus for a specific class of question. **Use this file when the standard recipe doesn't answer your question, or when you need to falsify a hypothesis Prometheus can't see.**
**本文为什么存在**：主流程 Step 3 说"盘点现有信号"。大多数用户（和 LLM 默认习惯）只想到应用层 Prometheus。其实信号源在不同抽象层有几十种 —— 内核系统调用、JVM 内部、内核缓冲区状态、网络抓包、GC 日志、profiler 采样 —— 每种针对一类特定问题比 Prometheus 更好。**当标准配方答不了问题、或需要证伪 Prometheus 看不到的假设时，看本文**。

---

## 0. The signal-fidelity ladder
## 0. 信号保真度阶梯

From cheapest (least invasive, but coarse) to most expensive (highest fidelity, but harder to obtain in prod):
从最便宜（侵入性最小但粒度粗）到最贵（保真度最高但 prod 不易获取）：

| Tier | Signal source | What it answers | Cost |
|---|---|---|---|
| 0 | App-level Prometheus counters (this is where everyone starts) | "How often does X happen, in aggregate?" | free, already running |
| 1 | Spring Boot Actuator `/actuator/metrics/<name>` (port-forward to one pod) | "Exact gauge / counter value on this specific pod, including metrics not exported to Prometheus" | trivial — `kubectl port-forward` |
| 1 | JVM JFR (`jcmd <pid> JFR.start duration=60s`) | "Where does CPU go, what allocates, what locks contend, on this pod, over a 60s window" | Spring Boot Actuator can trigger via `/actuator/startup`; otherwise SSH + jcmd |
| 1 | GC logs (`-Xlog:gc*` or `-Xloggc`) | "Real GC pause distribution, marker by-marker — way more detail than `jvm_gc_pause_seconds`" | grep on disk |
| 2 | `/proc/<pid>/stat` / `status` / `limits` / `sched` | "Voluntary vs involuntary context switches, scheduler stall, RSS vs VSS, fd limit" | `kubectl exec`, free |
| 2 | `/proc/pressure/{cpu,memory,io}` (PSI) | "Kernel-level stall measurement — captures contention not in CPU%" | exec, free |
| 2 | `ss -tnp summary` / `/proc/net/sockstat` | "TCP socket states (CLOSE_WAIT, TIME_WAIT, SYN_RECV) — connection-pool exhaustion vs network" | exec, free |
| 2 | `nstat` / `/proc/net/snmp` | "TCP retransmits, segment timeouts — invisible to app metrics" | exec, free |
| 2 | `iostat` / `blktrace` | "Disk I/O queue depth, await time — staging logs to local disk?" | exec, free |
| 2 | `perf top -p <pid>` (5-10s sample) | "What kernel + user functions are eating CPU right now" | exec, requires perf installed |
| 3 | `strace -c -p <pid>` (10-30s) | "Syscall histogram — fsync storm? stat() spam?" | exec, brief pause |
| 3 | `tcpdump` / Wireshark on a pod | "Are connections being reused? gzip on? TLS resumption rate?" | exec, very fine-grained |
| 3 | eBPF (bcc-tools, bpftrace) — `tcp_retransmit`, `runqlat`, `slabratetop`, etc. | "Almost any kernel question, sampled at near-zero cost" | requires eBPF tools on the node |
| 3 | Continuous profiler (Pyroscope / Parca / async-profiler agent) | "Always-on flame graph — diff pre-deploy vs post-deploy" | requires deploy |
| 4 | Heap dump (jmap → MAT) | "What objects exist, retained-size by class, leak path" | needs gc + transfer (~minutes) |
| 4 | Distributed tracing (Jaeger / Tempo / Zipkin) — if instrumented | "Span-level latency breakdown per request" | needs sampling tracer wiring |
| 4 | Production traffic shadowing (Diffy / shadow-replay) | "Does new code return identical responses to old code on real traffic?" | needs replay infra |
| 4 | Chaos / fault injection (Toxiproxy / chaos-mesh) | "How does new code degrade under DB latency / dropped packets?" | requires staging env |
| 4 | EXPLAIN ANALYZE / pt-query-digest | "Real cost of new SQL, including estimated rows scanned" | requires DB shell |

**Heuristic**: start at Tier 0. Move down only when the upper tier can't answer. Never jump to Tier 4 first — it's always over-engineering.
**经验法则**：从 Tier 0 开始，上一层答不了再往下走。永远别一上来就 Tier 4，那总是过度工程。

---

## 1. By symptom — what to grab when you see X
## 1. 按症状反查 —— 看到 X 该取什么

### "P99 latency just jumped, baseline is steady" / P99 突然抖
1. Tier 0: `histogram_quantile(0.99, sum by(le)(rate(...)))` — confirm the metric.
2. Tier 0: `hikaricp_connections_pending` — is it DB pool saturation? If yes, see L7 in historical-lessons.
3. Tier 1: GC logs — was there a Mixed GC / Full GC at the same minute? Cross-check with `jvm_gc_pause_seconds_max`.
4. Tier 1: JFR for 60s on one pod — flame graph will show new hot stacks.
5. Tier 2: `/proc/pressure/cpu` — kernel-level stall? CFS throttling?
6. Tier 3: `strace -c` — fsync storm?
7. **Hypothesis-driven trace**: distributed-tracing for the slow requests (sample by P99 traceID).

### "Pod CPU rises but throughput stays flat" / CPU 涨但吞吐没变
1. Tier 0: `process_cpu_usage` per pod — confirm.
2. Tier 0: `jvm_gc_memory_allocated_bytes_total` rate — alloc storm?
3. Tier 0: `container_cpu_cfs_throttled_seconds_total` — cgroup throttling?
4. Tier 1: JFR for 60s — flame graph (the gold standard).
5. Tier 3: `perf top -p <pid>` for live picture.

### "Pod restarts (OOMKilled / liveness fail)" / pod 反复重启
1. Tier 0: `kube_pod_container_status_restarts_total` — pin the cadence.
2. Tier 0: `kubectl describe pod` — last termination state ("OOMKilled" / "Error 137" / "liveness probe failed").
3. Tier 0: `container_memory_working_set_bytes` vs `container_spec_memory_limit_bytes` — was it actually OOM?
4. Tier 1: heap dump on next restart (`jmap -dump:live,format=b,file=...`) — what occupies memory.
5. Tier 4: Eclipse MAT diff — leak path.

### "Connection pool exhausted but no DB error" / 连接池打满但 DB 没报错
1. Tier 0: `hikaricp_connections_pending` time series — when did it first go > 0?
2. Tier 0: `hikaricp_connections_active == hikaricp_connections_max` — saturation epochs.
3. Tier 0: `server_sql_duration_milliseconds` P99 by method — slow query holding connections?
4. Tier 2: `ss -tnp state established '( dport = :3306 or dport = :5432 )'` on a pod — actual connection count to DB.
5. Tier 4: `EXPLAIN ANALYZE` on the suspect query — temp table? full scan?

### "Errors not in Prometheus error counter, only in logs" / 报错只在日志里
1. Tier 0: `log_level_count{loglevel=ERROR}` — global rate increased?
2. Tier 1: `troubleshooting` skill `/log-search/query-index` — find a sample line, get the class.
3. Read the throwing class' source (Grep + Read) — what triggers it.
4. Add a counter at the throw site if rate > 1/min.

### "Cache hit rate is `n/a` because the metric doesn't exist yet" / 缓存命中率没指标
1. Tier 1: Spring Boot Actuator `/actuator/caches` (per pod) — Caffeine stats endpoint.
2. Add an instrumented `Cache.recordStats()` call + expose via `CacheMetrics` — for next time.
3. Or: derive from sibling counters — if `(read_count - cache_miss_count) / read_count` is reachable, compute.

### "Load test: how big is the device list this user sees?" / 单用户设备数分布
1. Tier 0: business data warehouse (Superset) `SELECT user_id, COUNT(DISTINCT serial_number) FROM ...` — see [data-warehouse.md](data-warehouse.md).
2. Tier 1: troubleshooting platform `query/db` for a specific user — see [troubleshooting-bridge.md](troubleshooting-bridge.md).

### "What does the wire actually look like?" / 网络上实际是什么
1. Tier 3: `tcpdump -i any -w /tmp/pod.pcap port 80` then transfer + open in Wireshark.
2. Tier 3: bpftrace `tcp_lifestat.bt` — connection lifecycle stats.
3. Tier 4: traffic shadowing if request shape comparison needed.

---

## 2. By layer — what each layer hides
## 2. 按层级 —— 每一层都隐藏什么

### Layer 1: Application metrics (Prometheus counters)
**What it sees**: rate, latency-histogram of what the app explicitly counted.
**What it doesn't see**: anything not instrumented; partial-work mid-request; queue waits; lock contention; GC; I/O syscalls.

### Layer 2: JVM metrics (Actuator + JMX)
**What it sees**: heap, GC, classes, threads-by-state, buffer pools, ClassLoader churn.
**What it doesn't see**: which methods allocate; which threads are blocked on what; where stack frames live.
**Drill-down**: JFR / async-profiler.

### Layer 3: OS / kernel
**What it sees** via `/proc`, `/sys`, `nstat`: real CPU, memory, network, disk; PSI; cgroup throttling.
**What it doesn't see**: in-process state.
**Drill-down**: eBPF / bpftrace (almost any kernel question).

### Layer 4: Network
**What it sees** via tcpdump / Envoy / service mesh access logs: every packet / every cross-pod call with timing.
**What it doesn't see**: in-process latency causes.

### Layer 5: Storage (DB, Redis, S3)
**What it sees** via `performance_schema` / Redis `INFO commandstats` / S3 access logs: every query/cmd/get with cost.
**What it doesn't see**: app-side intent.
**Drill-down**: `EXPLAIN ANALYZE` / `pt-query-digest`.

### Cross-layer joins
- App-level `server_sql_duration_milliseconds` × DB-level `mysql_global_status_questions` — should line up (off by ≤10% allowable).
- Mismatch is a signal: connection pool dropping queries, or app retrying on read-replica.
- App-level `jvm_threads_states_threads{state=blocked}` × OS-level PSI cpu — both should rise during a deadlock.

---

## 3. Cross-validation methods (the heart of "first-principles")
## 3. 交叉验证方法（第一性原理的核心）

A single signal is never enough. Every claim should be derivable from at least 2 independent sources:
单一信号永远不够。每个结论都应可以从 ≥ 2 个独立信号源推导出来：

| Claim / 结论 | Source A | Source B (independent) |
|---|---|---|
| "Endpoint X is at 5,000 QPS" | `http_request_cost_time_histogram_count` rate | ALB / ingress access log rate / nginx stats |
| "DB pool is saturated" | `hikaricp_connections_active == _max` | `ss -tnp state established '( dport = :3306 )'` count, OR DB-side `mysql_global_status_threads_connected` |
| "GC pause caused P99 spike" | `jvm_gc_pause_seconds_max` time | GC log timestamp + duration |
| "Cache hit rate is 95%" | `cache_gets{result="hit"}` / `cache_gets_total` | DB-side query rate dropping by 95% after rollout |
| "Each device polls /deviceMsg/setting every 12.4 min" | endpoint QPS / `node_netstat_Tcp_CurrEstab{job="us-prod-kiss"}` | data-warehouse `event_count_per_device` over 24h |
| "Code path is reached" | counter on the line | log_level_count of an INFO line on that path |
| "User sees feature X" | GrowthBook eval counter | downstream business counter that only fires when X enabled |
| "P99 is 9 seconds because of one user" | log search by sn for the slow request | distributed trace for that specific traceID |

When source A and source B disagree, **that disagreement IS the finding** — usually it's the more interesting story than the original question.
当 A 和 B 不一致时，**那个不一致本身就是发现**——通常比原问题更值得讲。

---

## 4. Methods this codebase has not yet adopted (gaps & opportunities)
## 4. 本代码库尚未采用的方法（差距与机会）

Discovered during the 2026-04-28 setting-override evaluation:
2026-04-28 setting-override 评估时发现：

1. **No worker-thread metrics for iot-service-cloud** (Undertow XNIO unexposed). Bird-story incident invisible. **Opportunity**: expose `xnio.worker.busy-count`, `xnio.worker.queue-size` via Micrometer.
2. **No continuous profiling (Pyroscope / Parca)**. Hot-path regressions can only be inferred from latency, not from CPU stack distribution. **Opportunity**: add async-profiler agent.
3. **No distributed tracing on iot-service-cloud → kiss boundary** (despite trace_id in MDC). **Opportunity**: wire OTel exporter to Tempo.
4. **No cache statistics for in-process Caffeine caches** (e.g. `@QueryCache` from bird-story fix). Hit rate unknown. **Opportunity**: enable Caffeine stats + Micrometer exposition.
5. **No production traffic shadowing**. Behavior diff between baseline and new code is detectable only after canary. **Opportunity**: pre-merge shadow against staging-eu.
6. **No `EXPLAIN ANALYZE` snapshot for new queries**. The `device_setting_override` table's `selectBySn` cost is being inferred. **Opportunity**: run EXPLAIN against a staging snapshot.
7. **No kernel PSI scrape**. CFS throttling and CPU pressure invisible. **Opportunity**: scrape `/proc/pressure/cpu` via node-exporter textfile collector.
8. **No EBpf retransmit / latency probes**. Network-layer issues invisible. **Opportunity**: deploy `bcc-tools tcpconnlat` as DaemonSet sidecar in critical clusters.

A new code change introducing a new I/O on a hot path is the right occasion to **also propose** instrumenting one of these gaps. Otherwise the next bird-story-style incident will land in the same dark spot.
新代码改动在热路径上加新 I/O，正是**顺便**补一个差距 instrumentation 的好时机。否则下一次 bird-story 类事故还是会落在同一片盲区。

---

## 5. Anti-patterns specific to first-principles analysis
## 5. 第一性原理分析特有的反模式

1. **"Prometheus didn't show it, so it didn't happen."** — Wrong. Prometheus shows what someone instrumented. Always confirm via at least one signal that's NOT app-level Prometheus.
2. **"I'll add 5 dashboards then I can answer it."** — Wrong. Dashboards = derived views. Add the right counter / sampler at the source first. Dashboards last.
3. **"JFR is too heavy for prod."** — Wrong (mostly). JFR has < 1% overhead at default settings. Time-bounded JFR (60-300s) on one pod during an incident is fine.
4. **"Heap dump will OOM us."** — Real risk on a saturated pod. Take it at off-peak from a still-healthy pod for baseline; from a degrading pod immediately after restart from a snapshot.
5. **"eBPF needs root, can't do it in prod."** — In K8s with privileged DaemonSets you can. The eBPF tooling pays back its setup cost the first time you avoid an unnecessary code change.
6. **"Distributed tracing is for debugging, not for capacity."** — Wrong. Trace span histograms answer "where do my milliseconds go" better than any other tool. Capacity planning starts with where time is spent.
7. **"My P99 jumped 100ms, that means add a Caffeine cache."** — Wrong premature pessimization. First confirm WHERE the 100ms went (JFR / trace), then choose the fix. Fixing without locating is just decoration.
8. **"This is too low-level for design review."** — Wrong. The whole point of pre-merge review is catching what `git log --grep="incident"` would otherwise teach you the hard way. Reach for the lower-level signal **at design time**, not in post-mortem.

---

## 6. The 80/20 of where to actually start
## 6. 实际开始的 80/20

For 80% of perf-preflight work, the answer is in:
80% 情况下，答案在：

1. `http_request_cost_time_histogram_count{uri=...}` — entry QPS
2. `histogram_quantile(0.99, ...{uri=...})` — entry P99
3. `server_sql_duration_milliseconds_count{method=...}` — DB QPS by method
4. `hikaricp_connections_active` / `_pending` / `_max` — pool saturation
5. `node_netstat_Tcp_CurrEstab{job="<region>-prod-kiss"}` — online device count
6. `log_level_count_total{loglevel=ERROR/WARN}` — error/warn rate
7. `device_state_change_counter` (state-machine) — device churn
8. The 7-day max via `max_over_time(... [7d:1m])` for capacity-headroom

For the remaining 20%, this file applies. **Don't reach for tier-3+ tools without a hypothesis** — without a hypothesis, lower-tier exploration is just wandering.
剩下 20% 适用本文。**没有假设时不要直接拿 Tier 3+ 工具** —— 没有假设的低层探索就是闲逛。

# State / Time / Checkpoint / Savepoint

> 由 [../SKILL.md](../SKILL.md) 路由进入。**跨范式通用**：DataStream / SQL / PyFlink / CEP 共享相同的 state / time / checkpoint 语义。

## State Backend：统一 RocksDB（生产唯一选择）

**生产 Flink 集群 state backend 一律配 RocksDB**——不再讨论 HashMap state backend（仅适合本地 demo / 极小状态的边缘场景）。

### 为什么 RocksDB-only

| 维度 | RocksDB |
|---|---|
| 状态规模 | 可超过 TM 堆内存（落盘 SST） |
| Checkpoint | **增量 checkpoint**（只传 SST 增量，大状态时关键） |
| TTL 清理 | Compact filter 在 SST compaction 时清理，零额外开销 |
| Heap 压力 | 状态不进堆，TM GC 压力低 |
| 启动恢复 | 落盘 SST 直接 mount，恢复更快 |

唯一代价是序列化 I/O 开销（vs HashMap 直接 Java object 引用）——但生产场景中状态稳定性与可观测性远超这点性能差。

### 集群侧 flinkConfiguration（SSOT）

addx 集群所有 RocksDB / Checkpoint / S3 / restart-strategy 实际值集中在一个地方维护：[deployment.md §集群定义](deployment.md)。本文不重复列以避免漂移。

要点：
- `state.backend.type: rocksdb` + `incremental: true` + `localdir: /opt/flink/rocksdb-state` + `memory.managed: true`
- `execution.checkpointing.interval: 300000`（5 分钟，集群默认）
- TM Pod 必须挂 emptyDir `rocksdb-state`（base sizeLimit 15Gi）到 `/opt/flink/rocksdb-state`——overlay JSON merge patch 重列 `containers[0]` 时**别漏 volumeMounts**

业务作业**不要在 main() 里硬编码 StateBackend 类型**——集群统一管。要在业务侧覆盖 checkpoint interval，在 `config-<region>.yaml` 的 `jobs.<job>.checkpoint.intervalMs` 配（由 `Checkpointing.enable(env, spec)` 应用，见 [datastream.md](datastream.md)）。

### 业务代码不要硬编码

业务作业的 `main` 里**不要**调 `env.setStateBackend(new EmbeddedRocksDBStateBackend())`——让集群配置统一管。若临时本地调试想用堆内 state，加 program args / env 开关，**不要 commit 到默认路径**。

## State 类型速查

| 类型 | 用法 | 典型场景 |
|---|---|---|
| `ValueState<T>` | 单值；`value()` / `update()` / `clear()` | 标志位、计数器、最近一条 |
| `ListState<T>` | 追加列表 | 滑动窗口缓存、批量待发 |
| `MapState<K, V>` | KV 集合，`iterator()` 遍历 | 集合去重、关联缓存 |
| `ReducingState<T>` | 自动 reduce 单值 | running sum / running max |
| `AggregatingState<IN, OUT>` | 累加器模式 | 复杂聚合（如平均值） |

**RocksDB 优化提示**：
- `MapState<K, V>` 比 `ValueState<Map<K, V>>` 性能高几个数量级——前者每个 key 单独是 SST 条目，可单点 read/write；后者每次都要序列化整个 map
- `ListState` append 用 `add()` 不要 `update(List)`——前者增量，后者重写整个列表

## State TTL（RocksDB-only 推荐配置）

```java
// Flink 2.x：用 Duration；老 API org.apache.flink.streaming.api.windowing.time.Time 已删除
StateTtlConfig ttl = StateTtlConfig.newBuilder(Duration.ofDays(7))
    .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)   // 写时刷新
    .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
    .cleanupInRocksdbCompactFilter(1000)   // 每 N 条 evaluate 一次 → SST compaction 顺带清理
    .build();

ValueStateDescriptor<Long> desc = new ValueStateDescriptor<>("last-seen", Types.LONG);
desc.enableTimeToLive(ttl);
```

| TTL 清理策略 | 适用 |
|---|---|
| `cleanupInRocksdbCompactFilter(N)` | **RocksDB 唯一推荐**——零额外资源，跟随 SST compaction 顺带清理 |
| `cleanupIncrementally(...)` | 仅 HashMap state backend 使用，RocksDB 下无效 |
| `cleanupFullSnapshot()` | 仅 savepoint 时清理；运行时不动 |

**TTL 不是窗口**：到期不会触发任何 callback，仅在下次 read 时返回 null / 过滤掉。需要 callback 的话用 Timer。

## 时间语义

| 时间 | 取值 | 何时用 |
|---|---|---|
| **Processing time** | TaskManager wall clock | 简单去重、不关心顺序 |
| **Event time** | 事件自带时间戳 | 业务正确性要求顺序、窗口聚合（**默认选这个**） |
| Ingestion time | source 进入时间 | 罕用，不推荐 |

## Watermark

```java
WatermarkStrategy<Event> ws = WatermarkStrategy
    .<Event>forBoundedOutOfOrderness(Duration.ofSeconds(30))
    .withTimestampAssigner((e, ts) -> e.getEventTimeMs())
    .withIdleness(Duration.ofMinutes(1));          // partition 空闲时 watermark 不阻塞

env.fromSource(source, ws, "kafka-src");
```

**SQL 等价**：在 CREATE TABLE 里 `WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND`，partition 空闲配 `'scan.watermark.idle-timeout' = '60000'`。

## Window 速查

| 类型 | 何时用 |
|---|---|
| **Tumbling** | 固定不重叠（每 5 分钟一个桶） |
| **Sliding** | 重叠（每分钟一次过去 5 分钟） |
| **Session** | 不活跃间隔切分 |
| **Cumulating**（SQL） | 累计窗口（每分钟累计当天数据） |
| **Global + Trigger** | 自定义触发条件 |

**Late events 单独走 side output**，别丢：

```java
OutputTag<Event> lateTag = new OutputTag<>("late") {};
keyed.window(TumblingEventTimeWindows.of(Duration.ofMinutes(5)))
     .allowedLateness(Duration.ofMinutes(2))
     .sideOutputLateData(lateTag)
     .aggregate(...)
```

## Checkpoint vs Savepoint

| 维度 | Checkpoint | Savepoint |
|---|---|---|
| 触发 | Flink 自动按 interval | 人/脚本显式触发 |
| 格式 | 二进制、Flink 版本绑定 | 标准格式、跨版本兼容 |
| 生命周期 | 默认作业 cancel 时删（除非 RETAIN_ON_CANCELLATION） | 永久（除非显式删） |
| 用途 | 故障恢复 | 升级、迁移、A/B、调试 |
| 增量 | RocksDB 支持增量 | savepoint 总是全量 |

**Externalized checkpoint**（容灾兜底，作业意外失败也能从最近 checkpoint 恢复）：

```java
// Flink 2.x：用 setExternalizedCheckpointRetention（老的 setExternalizedCheckpointCleanup 已 deprecated）
env.getCheckpointConfig().setExternalizedCheckpointRetention(
    ExternalizedCheckpointRetention.RETAIN_ON_CANCELLATION);
```

## 升级流程（state 不能丢的唯一正确姿势）

### 推荐：REST API（addx 环境从办公网访问 JM Ingress）

```bash
# 1. 触发 savepoint + cancel job（一步搞定）
curl -X POST https://flink-staging-us.addx.live/jobs/<job-id>/savepoints \
     -H "Content-Type: application/json" \
     -d '{"target-directory": "s3://a4x-us-datalake-staging/flink/savepoints/", "cancel-job": true}'
# 返回 {"request-id": "..."}

# 2. 轮询完成状态
curl https://flink-staging-us.addx.live/jobs/<job-id>/savepoints/<request-id>
# status.id == "COMPLETED" 时 → operation.location 即 savepoint path

# 3. 用新 jar 提交（Web UI 或 REST API），Savepoint Path 填上一步 location
```

### `flink stop` CLI（在能 exec 进 JM Pod 或本地装了 flink CLI 的场景）

```bash
flink stop \
  --savepointPath s3://a4x-us-datalake-staging/flink/savepoints/ \
  <job-id>
# 输出 Savepoint completed. Path: s3://.../savepoint-xxx-yyy

flink run \
  -s s3://.../savepoint-xxx-yyy \
  -c com.addx.flink.jobs.bird.firstvisit.BirdFirstVisitJob \
  /path/to/new.jar \
  --region staging-us
```

### Web UI Stop 按钮

Flink Web UI **不存在**"取消作业时勾选 Trigger Savepoint"这种 checkbox。正确路径：

- Job Details 页右上角 **Stop** 按钮（与 `Cancel` 并列）→ 弹框填 **Savepoint Path** → 提交
- 作业先 trigger savepoint，savepoint 完成后停止
- 新 jar 提交时在 Submit New Job 表单填 **Savepoint Path**（与启动作业同一表单）

### ⚠️ `Cancel Job` ≠ savepoint

addx 4 个 region overlay（**不是 base**）都在 `flinkdeployment-patch.yaml` 显式配了 `web.cancel.enable: "true"`，所以 Web UI 暴露了 Cancel 按钮。但：

- **Cancel 只取消作业，不触发 savepoint**
- 集群配了 `RETAIN_ON_CANCELLATION` 后 cancel 不删 checkpoint，可从最近 checkpoint 恢复——但 checkpoint **不保证跨 Flink 版本兼容**，跨大版本升级时只能用 savepoint
- 改 parallelism / state schema 时 cancel + restart 行为不可预期（KeyGroups 重映射 + state migration）

**升级一律走 REST API / Stop 按钮 / `flink stop`，不要点 Cancel**。

## 何时必须 stop-with-savepoint

| 改动 | 必须 savepoint 升级？ |
|---|---|
| 改 `parallelism` | ✅ 必须 |
| 改 state schema（POJO 字段增删） | ✅ 必须（serializer 兼容前提下） |
| 改 operator uid | ❌ 没意义，state 已丢 |
| 改 SQL query plan 结构（join 顺序、agg key、window 类型） | ✅ 必须 + 业务确认 state 兼容 |
| 改 CEP Pattern 结构 | ✅ 必须 + 接受 partial match 损失 |
| 仅改算子内部计算逻辑（条件、UDF 实现），算子结构与 uid 不变 | ⚠️ 不强制，但建议走 savepoint 保险 |
| 换 Flink 大版本（1.x → 2.x） | ✅ 必须用 savepoint，**不能**用 checkpoint |

## State migration（schema 演化）

POJO + 显式 `TypeInformation` 比 Kryo 安全，可加字段、改字段类型（有限）。规则：

- **加字段**（默认值）→ ✅ 兼容
- **删字段** → ⚠️ 老 state 字段会丢
- **改字段类型** → ❌ 不兼容（Long ↔ Integer 也不行）
- **改字段名** → ❌ 不兼容
- **改字段顺序** → ✅（POJO 用名字识别，不依赖顺序）

不兼容的迁移走 **state processor API** 重写 savepoint（offline）：

```java
SavepointReader reader = SavepointReader.read(env, oldSavepoint, new EmbeddedRocksDBStateBackend());
DataStream<OldState> states = reader.readKeyedState("operator-uid", new OldReader());
SavepointWriter.newSavepoint(...)
    .withConfiguration(...)
    .removeOperator("operator-uid")
    .withOperator(OperatorIdentifier.forUid("operator-uid"),
        OperatorTransformation.bootstrapWith(states.map(...)))
    .write(newSavepoint);
```

## 跨范式速查

| 范式 | 配 state ttl | 升级 | savepoint 兼容性查法 |
|---|---|---|---|
| DataStream | `desc.enableTimeToLive(...)` | `flink stop -p` + `flink run -s` | uid 不变即兼容 |
| Flink SQL | `SET 'table.exec.state.ttl' = '7 days'` | 同上 | `EXPLAIN PLAN FOR INSERT ...` 对比 |
| PyFlink Table | 同 SQL | 同上 | 同上 |
| CEP | Pattern step `within(...)` | 同上 | Pattern 结构不变即兼容（条件改可热升级） |

## 下一步

- 写测试覆盖 state restore 行为 → [testing.md](testing.md)
- 部署 / K8s 挂盘 / vault → [deployment.md](deployment.md)

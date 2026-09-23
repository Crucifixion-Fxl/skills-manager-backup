# DataStream API（Java / Scala / Kotlin）

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文聚焦 DataStream 范式的 main 骨架、算子、Source/Sink、Async I/O。State / Time / Checkpoint 见 [state-time-checkpoint.md](state-time-checkpoint.md)，测试见 [testing.md](testing.md)，部署见 [deployment.md](deployment.md)。

## 何时选 DataStream

| ✅ 选 DataStream | ❌ 改用其他范式 |
|---|---|
| 自定义状态机、复杂时间触发 | 纯 SQL 可表达 → Flink SQL（[flink-sql.md](flink-sql.md)） |
| 多 sink 分流（side output） | Python 团队 → PyFlink（[pyflink.md](pyflink.md)） |
| 异步外部 I/O 编排 | 模式匹配规则引擎 → CEP（[cep.md](cep.md)） |
| 算子级低层细节（Watermark、Timer、State migration） | |

## main 类骨架（生产模板）

```java
public class MyJob {
    private static final Logger LOG = LoggerFactory.getLogger(MyJob.class);
    private static final String JOB_NAME = "my-job";

    public static void main(String[] args) throws Exception {
        // 1. 加载配置（来源由项目决定：args / env / classpath yaml）
        AppConfig cfg = ConfigLoader.load(args);

        // 2. 启动日志一行打全维度（线上排查从这里开始）
        LOG.info("region={} job={} parallelism={} checkpointMs={} input={} output={}",
            cfg.region, JOB_NAME, cfg.parallelism, cfg.checkpointMs,
            cfg.inputTopic, cfg.outputTopic);

        // 3. env + 全局配置
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(cfg.parallelism);
        env.enableCheckpointing(cfg.checkpointMs, CheckpointingMode.EXACTLY_ONCE);
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(cfg.checkpointMs / 2);
        env.getCheckpointConfig().setCheckpointTimeout(10 * 60 * 1000L);
        // Flink 2.x：用 setExternalizedCheckpointRetention（老的 setExternalizedCheckpointCleanup 已 deprecated）
        env.getCheckpointConfig().setExternalizedCheckpointRetention(
            ExternalizedCheckpointRetention.RETAIN_ON_CANCELLATION);
        // StateBackend 统一 RocksDB（生产唯一选择）——集群侧 flink-conf.yaml 已配
        // state.backend.type: rocksdb / state.backend.incremental: true

        // 4. 业务管道（每个算子加 uid + name）
        env.fromSource(buildKafkaSource(cfg), WatermarkStrategy.noWatermarks(), "kafka-src")
           .flatMap(new ParseAndKey()).returns(Types.TUPLE(Types.STRING, Types.STRING))
           .uid("parse-and-key").name("parse-and-key")
           .keyBy(t -> t.f0)
           .process(new MyDetector())
           .uid("my-detector").name("my-detector")
           .sinkTo(buildKafkaSink(cfg))
           .uid("kafka-sink").name("kafka-sink");

        env.execute(JOB_NAME + "-" + cfg.region);
    }
}
```

**关键规约**：
- `env.setParallelism()` / `enableCheckpointing()` 紧跟 env 创建，避免后面覆盖
- 每个 operator 链条上的方法调用立刻跟 `.uid().name()`——uid 漏一个等于丢 state
- `execute(jobName)` 的 name 包含 region/env，便于 Web UI 区分多环境
- StateBackend 在集群侧 `flink-conf.yaml` 统一配 `rocksdb`，**不要在代码里硬编码**——便于运维全局调

## KeyedProcessFunction（自定义状态机主力）

```java
public class MyDetector extends KeyedProcessFunction<String, Event, Enriched> {
    private static final Logger LOG = LoggerFactory.getLogger(MyDetector.class);

    public static final OutputTag<UpdateEvent> UPDATE_TAG =
        new OutputTag<>("update", TypeInformation.of(UpdateEvent.class));

    private transient ValueState<Boolean> seen;
    private transient MapState<String, Long> windowCounts;

    @Override
    public void open(OpenContext ctx) {
        ValueStateDescriptor<Boolean> seenDesc =
            new ValueStateDescriptor<>("seen", Types.BOOLEAN);
        seenDesc.enableTimeToLive(buildTtl(Duration.ofDays(7)));
        seen = getRuntimeContext().getState(seenDesc);

        MapStateDescriptor<String, Long> winDesc = new MapStateDescriptor<>(
            "window-counts", Types.STRING, Types.LONG);
        winDesc.enableTimeToLive(buildTtl(Duration.ofHours(1)));
        windowCounts = getRuntimeContext().getMapState(winDesc);
    }

    @Override
    public void processElement(Event in, Context ctx, Collector<Enriched> out) throws Exception {
        if (Boolean.TRUE.equals(seen.value())) {
            return;   // 已触发，drop（幂等性）
        }
        seen.update(true);
        out.collect(enrich(in));
        ctx.output(UPDATE_TAG, new UpdateEvent(ctx.getCurrentKey(), in.ts()));
        LOG.info("emitted key={}", ctx.getCurrentKey());
    }

    /** RocksDB-only TTL：用 compact filter 在 SST compaction 时清理，零额外开销 */
    private static StateTtlConfig buildTtl(Duration ttl) {
        return StateTtlConfig.newBuilder(ttl)
            .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
            .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
            .cleanupInRocksdbCompactFilter(1000)
            .build();
    }
}
```

详细 State 类型 / TTL / StateBackend 见 [state-time-checkpoint.md](state-time-checkpoint.md)。

## Source 模板

**Kafka source**（新版 API，老版 `FlinkKafkaConsumer` 已废弃）：

```java
KafkaSource<String> source = KafkaSource.<String>builder()
    .setBootstrapServers(cfg.brokers)
    .setTopics(cfg.inputTopic)
    .setGroupId(cfg.consumerGroup)
    .setStartingOffsets(OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST))
    .setValueOnlyDeserializer(new SimpleStringSchema())
    .setProperty("security.protocol", "SASL_SSL")
    .setProperty("sasl.mechanism", "SCRAM-SHA-512")
    .setProperty("sasl.jaas.config", buildJaasConfig(user, password))
    .build();

env.fromSource(source, WatermarkStrategy.noWatermarks(), "kafka-src")
   .uid("kafka-src").name("kafka-src");
```

**File source**（FileSource，新版）：

```java
FileSource<String> source = FileSource
    .forRecordStreamFormat(new TextLineInputFormat(), Path.fromLocalFile(new File(path)))
    .monitorContinuously(Duration.ofSeconds(30))   // 流式监听新文件
    .build();
```

## Sink 模板

**Kafka sink（EXACTLY_ONCE）**：

```java
KafkaSink<String> sink = KafkaSink.<String>builder()
    .setBootstrapServers(cfg.brokers)
    .setRecordSerializer(KafkaRecordSerializationSchema.builder()
        .setTopic(cfg.outputTopic)
        .setValueSerializationSchema(new SimpleStringSchema())
        .build())
    .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
    .setTransactionalIdPrefix(JOB_NAME + "-" + cfg.region)   // 必须全局唯一
    .build();
```

**Side output 分流**（一个算子多种输出）：

```java
public static final OutputTag<UpdateEvent> UPDATE_TAG =
    new OutputTag<>("update", TypeInformation.of(UpdateEvent.class));

// 在 processElement 里
ctx.output(UPDATE_TAG, new UpdateEvent(...));

// 在 main 里消费
DataStream<UpdateEvent> updates = mainStream.getSideOutput(UPDATE_TAG);
```

## Async I/O（外部 RPC / DB lookup）

同步阻塞调外部 RPC（在 `processElement` 里 `redis.get(...)`）会拖死 task。必用 `AsyncDataStream`：

```java
public class RedisLookup extends RichAsyncFunction<String, Enriched> {
    private transient RedisAsyncClient client;
    private transient StatefulRedisConnection<String, String> conn;

    @Override
    public void open(OpenContext ctx) {       // Flink 2.x 统一 OpenContext
        client = RedisAsyncClient.create(uri);
        conn = client.connect();
    }

    @Override
    public void asyncInvoke(String input, ResultFuture<Enriched> future) {
        conn.async().get(key(input))
            .thenAccept(v -> future.complete(List.of(new Enriched(input, v))))
            .exceptionally(t -> { future.completeExceptionally(t); return null; });
    }

    @Override
    public void close() {
        if (conn != null) conn.close();
        if (client != null) client.shutdown();   // 严格顺序：先 conn 后 client
    }
}

AsyncDataStream
    .unorderedWait(input, new RedisLookup(),
        5, TimeUnit.SECONDS,      // 单请求超时
        100)                       // 同 key 最大未完成请求
    .uid("redis-lookup").name("redis-lookup");
```

**关键**：
- `RichFunction.close()` 必须按序释放（先 connection 后 client）——否则 TM 内存涨 + Netty 线程泄漏
- 超时 + capacity 双限：超时控制单请求最长等待；capacity 控制同 key back-pressure 阈值
- `unorderedWait` 比 `orderedWait` 吞吐高，下游对顺序不敏感时优先选

## DataStream 特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| operator 没 `.uid()` | savepoint 恢复时 state 丢 | 每个 operator 都加 `.uid("<unique>")` |
| 同步 RPC 在 `processElement` | task back-pressure 急速涨 | 改 `AsyncDataStream.unorderedWait` |
| `RichFunction.close()` 没释放 client | TM 内存涨、Netty 线程泄漏 | 按序 `conn.close()` + `client.shutdown()` |
| Web UI 填 parallelism 但没生效 | 实际 = 代码里 `env.setParallelism()` | 代码覆盖优先；Web UI 填了无效，**留空** |
| Kryo serializer fallback | 升级老 state schema 时序列化失败 | POJO + 显式 `TypeInformation` / Avro / Protobuf |
| `transactionalIdPrefix` 多 job 撞 | Kafka coordinator 端事务互相 abort | 每个 job + region 唯一 prefix |
| Detector 主输出 + side output 不一致 | 一边发了一边没发 | **同一变量同时喂两条流**，不要分别构造 |

## 下一步

- 写 state / window / time → [state-time-checkpoint.md](state-time-checkpoint.md)
- 写测试 → [testing.md](testing.md)
- 部署 / 升级 / vault → [deployment.md](deployment.md)
- 看真实参考代码 → [reference-projects.md](reference-projects.md)

# 测试（L1 / L2 / L3 三层）

> 由 [../SKILL.md](../SKILL.md) 路由进入。跨范式通用测试方法论；各范式具体 harness 在对应章节。

## 三层定义

| 层 | 名称 | 起什么 | 速度 | 覆盖 |
|---|---|---|---|---|
| **L1** | 单元测试 | 不起 cluster、纯 JVM | < 100ms / test | POJO 序列化、静态工具、JSON 解析 |
| **L2** | 算子 / SQL 测试 | TestHarness（无 cluster） | < 1s / test | 单算子或单 SQL 的输入输出 + state restore |
| **L3** | 集成测试 | MiniCluster + 嵌入式 broker / 容器 | 5-30s / test | 端到端管道：source → operator → sink |

不要跳层——L1 不能替代 L2（state 行为不能纯 POJO 覆盖），L2 不能替代 L3（topology + serializer + checkpoint 走通才算可上线）。

## L1：单元测试

POJO / 静态工具 / 解析器的输入输出。无 Flink 依赖。

```java
@Test void parsesValidJson() {
    Event e = EventParser.parse("{\"id\":\"x\",\"ts\":123}");
    assertThat(e.id()).isEqualTo("x");
    assertThat(e.ts()).isEqualTo(123L);
}

@Test void parsesNullFieldAsNull() {
    Event e = EventParser.parse("{\"id\":\"x\",\"ts\":null}");
    assertThat(e.ts()).isNull();           // JSON null literal ≠ 缺字段
}
```

**必覆盖的边界**：null / 空字符串 / 空白 / JSON null literal / 字段缺失 / 类型错（数字 vs 字符串）。

## L2：算子 / SQL 测试

### DataStream — `KeyedOneInputStreamOperatorTestHarness`

```java
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.apache.flink.runtime.checkpoint.OperatorSubtaskState;

private static KeyedOneInputStreamOperatorTestHarness<String, Event, Enriched>
        newHarness() throws Exception {
    return new KeyedOneInputStreamOperatorTestHarness<>(
        new KeyedProcessOperator<>(new MyDetector()),
        Event::userId,
        Types.STRING);
}

@Test void firstEventPerKeyEmits() throws Exception {
    try (var h = newHarness()) {
        h.setup(); h.open();
        h.processElement(new Event("u1", "..."), 0L);
        h.processElement(new Event("u1", "..."), 0L);    // 重复
        assertThat(h.extractOutputValues()).hasSize(1);   // 幂等
    }
}

@Test void differentKeysAreIndependent() throws Exception {
    try (var h = newHarness()) {
        h.setup(); h.open();
        h.processElement(new Event("u1", "..."), 0L);
        h.processElement(new Event("u2", "..."), 0L);
        assertThat(h.extractOutputValues()).hasSize(2);   // 跨 key 独立
    }
}

@Test void sideOutput() throws Exception {
    try (var h = newHarness()) {
        h.setup(); h.open();
        h.processElement(new Event("u1", "..."), 0L);
        var late = h.getSideOutput(MyDetector.UPDATE_TAG);
        assertThat(late).hasSize(1);
    }
}

@Test void restoreFromSnapshot() throws Exception {
    OperatorSubtaskState snapshot;
    try (var h = newHarness()) {
        h.setup(); h.open();
        h.processElement(new Event("u1", "..."), 0L);
        snapshot = h.snapshot(1L, 1L);
    }
    try (var h = newHarness()) {
        h.initializeState(snapshot);     // 恢复
        h.open();
        h.processElement(new Event("u1", "..."), 0L);
        assertThat(h.extractOutputValues()).isEmpty();   // state 已恢复，幂等触发
    }
}
```

**L2 必覆盖的边界**：
- 主输出与 side output 一致性
- 跨 key 独立
- 重复事件幂等
- snapshot + restore 后行为不变（最关键 —— 关系到 savepoint 升级安全）
- Timer / Watermark 触发（如有）

### Flink SQL — `TableEnvironment` 内存表

```java
@Test void aggregatesByWindow() throws Exception {
    EnvironmentSettings settings = EnvironmentSettings.newInstance().inStreamingMode().build();
    TableEnvironment t = TableEnvironment.create(settings);

    // 用 datagen / values 作 source
    t.executeSql("""
        CREATE TABLE src (
          user_id STRING, amount DECIMAL(10,2), ts TIMESTAMP_LTZ(3),
          WATERMARK FOR ts AS ts - INTERVAL '0' SECOND
        ) WITH ('connector' = 'values', 'data-id' = 'test-data')
        """);

    t.executeSql("""
        CREATE TABLE sink (
          window_start TIMESTAMP_LTZ(3), user_id STRING, cnt BIGINT
        ) WITH ('connector' = 'values', 'sink-insert-only' = 'false')
        """);

    // 注入测试数据
    String dataId = TestValuesTableFactory.registerData(List.of(
        Row.of("u1", new BigDecimal("10"), Instant.parse("2026-01-01T00:00:00Z")),
        Row.of("u1", new BigDecimal("20"), Instant.parse("2026-01-01T00:01:00Z"))
    ));

    t.executeSql("""
        INSERT INTO sink SELECT window_start, user_id, COUNT(*)
        FROM TABLE(TUMBLE(TABLE src, DESCRIPTOR(ts), INTERVAL '5' MINUTES))
        GROUP BY window_start, user_id
        """).await();

    List<String> results = TestValuesTableFactory.getResultsAsStrings("sink");
    assertThat(results).containsExactlyInAnyOrder(...);
}
```

需要依赖 `flink-table-planner:test-jar`（Flink 2.x 已去 Scala 2.12 后缀；运行时 jar 是 `flink-table-planner-loader`，测试 jar 仍是 `flink-table-planner:test-jar`）。`getResultsAsStrings` 返回 `List<String>`（不是 `List<Row>`）。

### PyFlink

```python
def test_simple_select():
    settings = EnvironmentSettings.in_batch_mode()  # 单测用 batch 模式更快
    t = TableEnvironment.create(settings)

    t.execute_sql("""
        CREATE TABLE src (id STRING, val INT)
        WITH ('connector' = 'values', 'data-id' = '...')
    """)
    result = t.execute_sql("SELECT id, val * 2 FROM src").collect()
    assert list(result) == [Row("a", 2), Row("b", 4)]
```

PyFlink 单测建议跑在 batch 模式（无 watermark / 时间复杂度），逻辑正确性即可；端到端流测试留 L3。

### CEP

构造事件序列直接喂 PatternStream，验匹配结果与超时：

```java
@Test void detectsThreeFailsThenSuccess() throws Exception {
    StreamExecutionEnvironment env = StreamExecutionEnvironment.createLocalEnvironment();
    DataStream<Transaction> txs = env.fromElements(
        new Transaction("u1", "login_fail",    1_000L),
        new Transaction("u1", "login_fail",    2_000L),
        new Transaction("u1", "login_fail",    3_000L),
        new Transaction("u1", "login_success", 4_000L));

    DataStream<AlertEvent> alerts = CEP.pattern(txs.keyBy(Transaction::userId), pattern)
        .process(new MyAlertHandler());

    List<AlertEvent> collected = new ArrayList<>();
    alerts.executeAndCollect().forEachRemaining(collected::add);

    assertThat(collected).hasSize(1);
    assertThat(collected.get(0).failCount()).isEqualTo(3);
}
```

## L3：集成测试（MiniCluster + 嵌入式依赖）

```java
@RegisterExtension
static final MiniClusterExtension FLINK = new MiniClusterExtension(
    new MiniClusterResourceConfiguration.Builder()
        .setNumberSlotsPerTaskManager(2)
        .setNumberTaskManagers(1)
        .build());

@Test void endToEnd() throws Exception {
    // 起嵌入式 Kafka（spring-kafka-test EmbeddedKafkaBroker / Testcontainers）
    EmbeddedKafkaBroker kafka = new EmbeddedKafkaBroker(1, true, "input-topic", "output-topic");
    kafka.afterPropertiesSet();

    // 写测试事件
    Producer<String, String> producer = ...;
    producer.send(new ProducerRecord<>("input-topic", "k", "{\"id\":\"e1\"}"));

    // 跑 main（在测试 main 类里覆盖 broker 地址）
    CompletableFuture<Void> jobFuture = CompletableFuture.runAsync(() -> {
        MyJob.main(new String[]{"--region", "test", "--brokers", kafka.getBrokersAsString()});
    });

    // 用 consumer 读输出 topic + assert
    Consumer<String, String> consumer = ...;
    consumer.subscribe(List.of("output-topic"));
    ConsumerRecords<String, String> records = consumer.poll(Duration.ofSeconds(30));
    assertThat(records).hasSize(1);
}
```

**嵌入式依赖二选一**：

| 选 | 优点 | 缺点 |
|---|---|---|
| **Testcontainers**（docker） | 真实容器、覆盖 SASL/SSL 协议握手 | 依赖 docker daemon、CI 慢启动 |
| **嵌入式 broker**（spring-kafka-test、jedis-mock） | 无 docker、CI 友好、JVM 内启动 | 仅 PLAINTEXT；SASL 协议层得人工 staging smoke |

## 防假绿自查（必做）

写完任何测试，**故意改坏生产代码**（注释一行业务 / `==` 改 `!=` / 把 emit 删掉）跑一次，确认测试**会 fail**。

```bash
# 改坏
git diff             # 看自己改了什么
mvn test -Dtest=MyDetectorTest
# 必须看到 RED
# 改回去
git checkout src/main/java/...
mvn test -Dtest=MyDetectorTest
# GREEN
```

绿色但破不掉的测试 = 没在测真东西 = 假阳性。

## 测试矩阵建议（每个 job 必备）

| 测试 | 覆盖 |
|---|---|
| L1 EventParserTest | JSON 解析的所有边界 |
| L2 DetectorTest | 算子主流 + side output + 跨 key + 幂等 + restore |
| L2 SQLPipelineTest（如 SQL job） | 每条 INSERT 的预期输出 |
| L3 JobIT（嵌入式 Kafka + MiniCluster） | 端到端：写 source topic → 跑 main → 读 sink topic |
| L3 SinkIT（如 Redis / DB sink） | 外部容器 + 验落库语义（HSET / upsert / TTL） |

## CI 集成

```bash
# 单元测试快、IT 慢，分两阶段
mvn -B test -Dtest='!*IT'                 # PR 触发，秒级
mvn -B verify -Dshade.skip=true            # merge 前触发，全套
```

CI gating：unit + IT 全过才能合 MR。SASL/SSL 协议层走 staging 第一次部署人肉 smoke 验证（IT 用 PLAINTEXT 不覆盖握手）。

## 测试特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| 改坏生产代码测试还绿 | 假阳性，没在测真东西 | 每个新测试做防假绿自查 |
| IT 测试间状态污染 | 第二个测试拿到第一个测试的残留 | `@BeforeEach` 加 `FLUSHDB` / clear topic |
| `NoClassDefFoundError` 跑 IT | Testcontainers 依赖冲突 | pom test scope 显式 pin（如 `commons-codec:1.15`） |
| MiniCluster 跑完没退出 | Job 没正常 finish、卡 socket | `env.executeAsync()` + 超时强制 `cancel()` |
| SQL test `Sink` 没数据 | 没等 `.await()` | `INSERT ... INTO` 用 `.execute().await()` |

## 下一步

- 测完准备部署 → [deployment.md](deployment.md)
- savepoint 升级时如何测 state 兼容性 → [state-time-checkpoint.md](state-time-checkpoint.md)

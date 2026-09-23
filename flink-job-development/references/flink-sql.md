# Flink SQL / Table API

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文聚焦声明式开发：CREATE TABLE / SELECT / 窗口 / Join / UDF / 部署。跨范式主题（state、checkpoint、测试、部署）见对应 reference。

## 何时选 SQL / Table API

| ✅ 选 SQL | ❌ 改用 DataStream |
|---|---|
| ETL / 聚合 / Join 可用 SQL 表达 | 自定义状态机、复杂时间触发 |
| 流批一体（同一份 SQL 跑流和批） | 算子级 low-level 控制（Timer、operator state migration） |
| 业务方能自己改 SQL 迭代 | side output 多路分流（SQL 表达困难） |
| 想吃 Flink 优化器红利（filter pushdown、谓词下推、join reorder） | |

DataStream 与 SQL 可在同一 job 里混用（`tableEnv.fromDataStream()` / `tableEnv.toDataStream()`）。

## 工程化骨架（SQL job 入口）

不推荐"裸 SQL 文件提交"——用 Table API 作为容器，把 SQL 作为字符串嵌入，便于参数化、单测、CI：

```java
public class MyOrderJob {
    private static final String JOB_NAME = "my-order-job";

    public static void main(String[] args) throws Exception {
        AppConfig cfg = ConfigLoader.load(args);

        EnvironmentSettings settings = EnvironmentSettings.newInstance()
            .inStreamingMode()
            .build();
        TableEnvironment tEnv = TableEnvironment.create(settings);

        // 全局配置
        Configuration conf = tEnv.getConfig().getConfiguration();
        conf.setString("execution.checkpointing.interval", cfg.checkpointMs + "ms");
        conf.setString("execution.checkpointing.mode", "EXACTLY_ONCE");
        // StateBackend 统一 RocksDB（集群 flink-conf.yaml 已配；此处可显式 override）
        conf.setString("state.backend.type", "rocksdb");
        conf.setString("state.backend.incremental", "true");
        // 稳定 operator uid（SQL job 升级 savepoint 兼容性关键，见下文）
        conf.setString("table.exec.uid.generation", "ALWAYS");
        conf.setString("table.exec.uid.format", JOB_NAME + "-${operatorName}-${operatorId}");

        // CREATE TABLE 串：用 ${} 占位参数化
        tEnv.executeSql(String.format("""
            CREATE TABLE orders_src (
              order_id   STRING,
              user_id    STRING,
              amount     DECIMAL(10, 2),
              event_time TIMESTAMP_LTZ(3),
              WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND,
              PRIMARY KEY (order_id) NOT ENFORCED
            ) WITH (
              'connector' = 'kafka',
              'topic' = '%s',
              'properties.bootstrap.servers' = '%s',
              'properties.group.id' = '%s',
              'properties.security.protocol' = 'SASL_SSL',
              'properties.sasl.mechanism' = 'SCRAM-SHA-512',
              'properties.sasl.jaas.config' = '%s',
              'scan.startup.mode' = 'group-offsets',
              'format' = 'json',
              'json.fail-on-missing-field' = 'false',
              'json.ignore-parse-errors' = 'true'
            )
            """, cfg.inputTopic, cfg.brokers, cfg.consumerGroup, buildJaas(cfg)));

        tEnv.executeSql(String.format("""
            CREATE TABLE orders_agg_sink (
              window_start TIMESTAMP_LTZ(3),
              user_id      STRING,
              order_count  BIGINT,
              total_amount DECIMAL(20, 2),
              PRIMARY KEY (window_start, user_id) NOT ENFORCED
            ) WITH (
              'connector' = 'upsert-kafka',
              'topic' = '%s',
              'properties.bootstrap.servers' = '%s',
              'key.format' = 'json',
              'value.format' = 'json'
            )
            """, cfg.outputTopic, cfg.brokers));

        // INSERT 业务
        // 单 INSERT：detached 提交（streaming 不需要 await）；要拿 TableResult 做断言时显式 .await()。
        // 多 INSERT 共享 source 时改用 StatementSet：
        //   StatementSet ss = tEnv.createStatementSet();
        //   ss.addInsertSql("INSERT INTO sink_a SELECT ...");
        //   ss.addInsertSql("INSERT INTO sink_b SELECT ...");
        //   ss.execute();   // 一次提交，source 只读一遍
        tEnv.executeSql("""
            INSERT INTO orders_agg_sink
            SELECT
              window_start,
              user_id,
              COUNT(*)      AS order_count,
              SUM(amount)   AS total_amount
            FROM TABLE(
              TUMBLE(TABLE orders_src, DESCRIPTOR(event_time), INTERVAL '5' MINUTES))
            GROUP BY window_start, user_id
            """);
    }
}
```

## Connector 速查

| connector | 用法 | 备注 |
|---|---|---|
| `kafka` | source / append-only sink | EOS sink 配 `sink.delivery-guarantee=exactly-once` + `sink.transactional-id-prefix` |
| `upsert-kafka` | 按 PK upsert sink | 自动保留 retract 流；必须声明 `PRIMARY KEY ... NOT ENFORCED` |
| `jdbc` | 批 source / sink；lookup join | 流式写库；sink 幂等 upsert（`sink.parallelism` + 主键 conflict） |
| `filesystem` | S3 / HDFS / local | 写 Parquet/ORC/Avro 分区文件；rolling policy |
| `hudi` / `iceberg` / `paimon` | 数据湖 sink | Lakehouse 场景；支持 schema evolution |
| `elasticsearch` | ES sink | 流式索引；幂等按 `_id` |

**重要规约**：写时序列化失败要在 connector 配置里显式处理，不要静默 drop：

```sql
'json.ignore-parse-errors' = 'false'  -- 默认 false，遇坏数据 fail
-- 若必须 'true'，应同时配 dlq topic 或加 metric
```

## 时间 / Watermark / Window

**Watermark 声明**（CREATE TABLE 必须）：

```sql
WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
```

Partition 空闲时全局 watermark 不动 → source DDL 加：

```sql
'scan.watermark.alignment.group' = 'my-group',
'scan.watermark.alignment.max-drift' = '1min'
-- 或者：
'scan.watermark.idle-timeout' = '60000'
```

**Window TVF（推荐）**：

```sql
-- Tumbling
TUMBLE(TABLE t, DESCRIPTOR(event_time), INTERVAL '5' MINUTES)

-- Sliding
HOP(TABLE t, DESCRIPTOR(event_time), INTERVAL '1' MINUTES, INTERVAL '5' MINUTES)

-- Cumulating（累计窗口：每分钟累计当天数据，常用于实时报表）
CUMULATE(TABLE t, DESCRIPTOR(event_time), INTERVAL '1' MINUTES, INTERVAL '1' DAYS)
```

老版 Group Window（`TUMBLE_START(rowtime, ...)`）已废弃，**新代码一律用 Window TVF**。

## Join 范式

| 类型 | 语义 | 状态规模 |
|---|---|---|
| **Regular Join**（`a JOIN b ON ...`） | 双流持久 state，无过期 | ⚠️ 状态无界——必加 `table.exec.state.ttl` |
| **Interval Join**（`a.ts BETWEEN b.ts - INTERVAL '1' HOUR AND b.ts`） | 限定时间窗口的双流 join | 状态有界，安全 |
| **Temporal Join**（`FOR SYSTEM_TIME AS OF a.event_time`） | 流维表 join | 维表用 versioned table 或 `Lookup` |
| **Lookup Join**（`JOIN dim FOR SYSTEM_TIME AS OF PROCTIME()`） | 同步/异步查外部维表 | 无状态；推荐配 cache |

**异步 lookup 配置**（连 MySQL/HBase/Redis 等外部维表）：

```sql
CREATE TABLE dim_user (
  user_id STRING,
  vip_level INT,
  PRIMARY KEY (user_id) NOT ENFORCED
) WITH (
  'connector' = 'jdbc',
  'url' = 'jdbc:mysql://...',
  'lookup.cache' = 'PARTIAL',
  'lookup.partial-cache.max-rows' = '50000',
  'lookup.partial-cache.expire-after-write' = '10min',
  'lookup.async' = 'true',
  'lookup.max-retries' = '3'
);
```

## 自定义 UDF

```java
public class HashUDF extends ScalarFunction {
    public String eval(String input) {
        return DigestUtils.sha256Hex(input);
    }
}

// 注册
tEnv.createTemporarySystemFunction("sha256", HashUDF.class);

// SQL 里调用
tEnv.executeSql("SELECT sha256(user_id) AS uid_hash FROM orders_src");
```

UDF 类型：`ScalarFunction` / `TableFunction`（UDTF，一行多行）/ `AggregateFunction`（UDAF）/ `TableAggregateFunction`。

## SQL job savepoint 兼容性（升级关键）

Flink SQL 默认每次 query plan 重算时 operator uid 都可能变 → 升级前必查兼容性。

**做法**：

1. **`table.exec.uid.generation=ALWAYS`** + 显式 uid format（见上面骨架）→ 让 uid 由 operator name 决定而非 plan position
2. 升级前用 `EXPLAIN PLAN FOR INSERT INTO ...` 对比新旧 plan，确认 operator 结构不变
3. 改 SQL 涉及 join 顺序 / agg key / window 类型 → state 一定不兼容，必须**清 state 重跑**（业务可接受重算时）或写**迁移 job**

## SQL 特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| Regular Join 状态无界 | TM 持续 OOM | 配 `SET 'table.exec.state.ttl' = '24 h'` 或改 Interval Join |
| Watermark 不前进 | 窗口永远不触发 | partition 空闲 → 配 `scan.watermark.idle-timeout` |
| Group agg 没设 mini-batch | TPS 上不去、RocksDB 频繁随机读 | 开 `table.exec.mini-batch.enabled=true` + size/latency |
| upsert-kafka 没声明 PRIMARY KEY | DDL 报错或 silently 走 append | 必须 `PRIMARY KEY (...) NOT ENFORCED` |
| 改 SQL 直接重启 | state 不兼容、丢失 | 改 SQL 前 `EXPLAIN PLAN` 对比；改 plan 结构 = 必须清 state |
| JSON 字段缺失 | source 直接 fail | 配 `'json.fail-on-missing-field' = 'false'` + `'json.ignore-parse-errors' = 'true'` 二选一或都开（按 SLA） |
| Lookup join 无 cache | QPS 打爆维表 DB | `'lookup.cache' = 'PARTIAL'` + size + TTL |
| 多 INSERT 串行执行 | 吞吐低、互相阻塞 | 用 `StatementSet.addInsert(...).execute()` 一次提交多 INSERT 共享 source |

## 下一步

- 跨 SQL 与 DataStream 混用 → [datastream.md](datastream.md)
- 测试 SQL job（`TableEnvironment.executeSql` + `CollectionResultIterator`） → [testing.md](testing.md)
- savepoint / 升级 / RocksDB 调优 → [state-time-checkpoint.md](state-time-checkpoint.md)
- 部署 / vault / 集群配置 → [deployment.md](deployment.md)

# PyFlink（Python DataStream + Table API）

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文聚焦 PyFlink 特有问题：环境、与 Java 互操作、序列化、性能取舍。SQL 语法见 [flink-sql.md](flink-sql.md)（PyFlink Table API 与 Java Table API 语法一致）。

## 何时选 PyFlink

| ✅ 选 PyFlink | ❌ 改用 Java DataStream |
|---|---|
| Python 团队、Python-only 生态依赖 | 性能敏感场景（PyFlink Python UDF 慢一个数量级） |
| 嵌入 ML 模型推理（PyTorch / scikit-learn / Hugging Face） | 大状态、低延迟（< 100ms 端到端） |
| 业务方用 Python，SQL 写出来就用 PyFlink Table API 包装 | 需要复杂 ProcessFunction Timer 编排 |

**PyFlink 性能定位**：
- **Table API / SQL**（纯 SQL）→ 性能 = Java SQL（执行计划等价，无 Python 进程开销）
- **Table API + Python UDF** → 比纯 SQL 慢 2-10 倍（IPC + 序列化开销）
- **DataStream API** → 比 Java DataStream 慢 3-10 倍（每条记录都跨进程）

**建议优先级**：纯 Table API/SQL > Table API + 极少量 Python UDF > DataStream（仅在 SQL 无法表达时）。

## 环境与构建

```bash
# Python 必须 3.9-3.11（PyFlink 2.x 支持范围；过新 / 过旧的 Python 都不行）
python --version

# 安装：版本号要严格对齐集群 Flink 版本
pip install apache-flink==2.2.0

# 验证
python -c "from pyflink.datastream import StreamExecutionEnvironment; print('ok')"
```

**打包提交**（避免集群没有用户的 Python 依赖）：

```bash
# 方案 A：单文件提交
flink run -py my_job.py

# 方案 B：含依赖（archive 形态，集群解压到 PyFlink worker）
flink run \
  -py my_job.py \
  -pyarch venv.zip#venv \
  -pyexec venv/bin/python \
  -pyclientexec venv/bin/python

# 方案 C：requirements.txt（PyFlink 2.x 起支持）
flink run -py my_job.py -pyreq requirements.txt
```

## Table API（推荐入口）

```python
from pyflink.table import EnvironmentSettings, TableEnvironment
from pyflink.common import Configuration

settings = EnvironmentSettings.in_streaming_mode()
t_env = TableEnvironment.create(settings)

# 全局配置（StateBackend 统一 RocksDB）
conf: Configuration = t_env.get_config().get_configuration()
conf.set_string("execution.checkpointing.interval", "60000")
conf.set_string("execution.checkpointing.mode", "EXACTLY_ONCE")
conf.set_string("state.backend.type", "rocksdb")
conf.set_string("state.backend.incremental", "true")
conf.set_string("table.exec.uid.generation", "ALWAYS")

# CREATE TABLE 与 Java 完全一致
t_env.execute_sql(f"""
    CREATE TABLE orders_src (
      order_id   STRING,
      user_id    STRING,
      amount     DECIMAL(10, 2),
      event_time TIMESTAMP_LTZ(3),
      WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
    ) WITH (
      'connector' = 'kafka',
      'topic' = '{topic}',
      'properties.bootstrap.servers' = '{brokers}',
      'properties.group.id' = '{group}',
      'format' = 'json',
      'scan.startup.mode' = 'group-offsets'
    )
""")

t_env.execute_sql("""
    CREATE TABLE agg_sink (
      window_start TIMESTAMP_LTZ(3),
      user_id      STRING,
      cnt          BIGINT,
      PRIMARY KEY (window_start, user_id) NOT ENFORCED
    ) WITH (
      'connector' = 'upsert-kafka',
      ...
    )
""")

t_env.execute_sql("""
    INSERT INTO agg_sink
    SELECT window_start, user_id, COUNT(*)
    FROM TABLE(TUMBLE(TABLE orders_src, DESCRIPTOR(event_time), INTERVAL '5' MINUTES))
    GROUP BY window_start, user_id
""").wait()
```

## Python UDF（性能敏感处慎用）

```python
from pyflink.table.udf import udf, udtf, udaf
from pyflink.table import DataTypes

@udf(result_type=DataTypes.STRING())
def normalize(s: str) -> str:
    return s.strip().lower() if s else ""

t_env.create_temporary_function("normalize", normalize)
t_env.execute_sql("SELECT normalize(name) FROM users")
```

**Vectorized UDF**（pandas-based，吞吐高 5-10 倍，必选）：

```python
import pandas as pd
from pyflink.table.udf import udf

@udf(result_type=DataTypes.STRING(), func_type="pandas")
def to_upper(s: pd.Series) -> pd.Series:
    return s.str.upper()
```

**ML 模型推理**：

```python
from pyflink.table.udf import udf
import pickle

class ModelUDF:
    def __init__(self, model_path):
        self.model_path = model_path
        self.model = None

    def open(self, function_context):
        with open(self.model_path, "rb") as f:
            self.model = pickle.load(f)

    @udf(result_type=DataTypes.FLOAT())
    def predict(self, features):
        return float(self.model.predict([features])[0])

# 注册时 model 文件通过 -pyfs 上传
```

## DataStream API（Python）

```python
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.functions import KeyedProcessFunction, RuntimeContext
from pyflink.datastream.state import ValueStateDescriptor
from pyflink.common.typeinfo import Types

env = StreamExecutionEnvironment.get_execution_environment()
env.set_parallelism(4)
env.enable_checkpointing(60_000)
env.get_checkpoint_config().set_externalized_checkpoint_cleanup(...)

class Detector(KeyedProcessFunction):
    def open(self, ctx: RuntimeContext):
        self.seen = ctx.get_state(ValueStateDescriptor("seen", Types.BOOLEAN()))

    def process_element(self, value, ctx: 'KeyedProcessFunction.Context'):
        if self.seen.value():
            return
        self.seen.update(True)
        yield value

(env
    .from_source(kafka_source, watermark_strategy, "kafka-src")   # 新版 API；老 add_source 仅用于 SourceFunction
    .key_by(lambda x: x["user_id"], key_type=Types.STRING())
    .process(Detector(), output_type=Types.ROW_NAMED(...))
    .uid("detector").name("detector")
    .sink_to(kafka_sink)                                          # 新版 sink，对应 Java sinkTo
    .uid("sink").name("sink"))

env.execute("my-pyflink-job")
```

**注意**：每条记录都要跨 Python ↔ JVM IPC，吞吐显著低于 Java DataStream。能用 Table API 就别用 DataStream API。

## 与 Java 互操作

**调 Java UDF**（性能等同 Java SQL）：

```python
t_env.execute_sql(f"""
    CREATE FUNCTION my_hash AS 'com.addx.HashUDF'
    USING JAR '{jar_path}'
""")
t_env.execute_sql("SELECT my_hash(user_id) FROM orders_src")
```

**加载 Java connector**（如官方 Python 包未含）：

```bash
flink run -py my_job.py \
  -j /path/to/flink-connector-elasticsearch-3.0.0.jar \
  -j /path/to/flink-sql-connector-mongodb-1.0.0.jar
```

## PyFlink 特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| Python 版本不匹配 | 启动报 `ImportError` / `Unsupported Python version` | Python 3.9-3.11 + `pip install apache-flink==<对齐集群版本>` |
| 集群缺业务 Python 依赖 | TaskManager 报 `ModuleNotFoundError` | `-pyarch venv.zip` 或 `-pyreq requirements.txt` 打包提交 |
| 用 Python DataStream 性能不达预期 | TPS 比 Java 低 5-10 倍 | 改 Table API 或纯 SQL；UDF 用 `func_type="pandas"` 向量化 |
| 改 Python UDF 直接重启 | UDF 在 udf 链中位置变化 → operator uid 变 → state 不兼容 | savepoint 升级；改 UDF 链结构前 `EXPLAIN` 对比 |
| `from pyflink.table import ...` 报错 | jar 没下载（PyFlink 启动时拉 Flink runtime jar） | 第一次 import 时网络要通；CI 里预拉 |
| Pandas UDF 报序列化错误 | Arrow 版本不匹配 | `pip install pyarrow==<PyFlink 版本要求>`（PyFlink 2.x 通常要 13.x） |
| 多 INSERT 串行执行 | 吞吐低 | `statement_set = t_env.create_statement_set(); statement_set.add_insert_sql(...); statement_set.execute()` |
| Python worker OOM | 单个 PyFlink TM 跑多个 Python worker 进程，内存累加 | 配 `python.fn-execution.bundle.size` + `taskmanager.memory.process.size` 留 Python 内存余地 |

## 下一步

- 配 RocksDB / 升级 savepoint（语法与 Java 通用） → [state-time-checkpoint.md](state-time-checkpoint.md)
- 测 PyFlink job（pytest + `MiniCluster`） → [testing.md](testing.md)
- 部署 PyFlink job（zip 打包 / Docker image） → [deployment.md](deployment.md)

---
name: flink-job-development
description: 通用 Apache Flink 流处理作业开发方法论——覆盖 DataStream / Flink SQL / Table API / PyFlink / CEP 四大编程范式 + State / Time / Checkpoint / Savepoint / 测试 / 部署 / Vault 等跨范式工程实践。当用户提到"写一个 Flink 作业"、"新增 Flink job"、"Flink SQL / Table API / PyFlink / CEP / DataStream"、"keyBy / ProcessFunction / ValueState / KafkaSource"、"checkpoint / savepoint / 升级 Flink"、"watermark / window / event time"、"Flink 测试 / MiniCluster / TestHarness"、"Flink 部署 / 集群 / vault / ExternalSecret"、或要 review Flink 作业代码时使用。与具体仓库无关，但内附 addx 体系下的真实参考实现链接。
---

# flink-job-development

通用 Apache Flink 作业开发方法论。本 SKILL.md 是**入口与路由**——根据你的范式与任务挑对应 reference 文件深入读，不要试图把所有内容塞进单一文件。

## Description

覆盖 4 大编程范式（DataStream API / Flink SQL / Table API / PyFlink / CEP）+ 跨范式工程实践（state / time / checkpoint / savepoint / 测试三层 / Session Mode 部署 / Vault 切分）。内容以 addx 真实生产体系为依据（对账 [`flink-jobs`](https://gitlab.addx.ai/DATA/flink-jobs) + [`flink-addx`](https://gitlab.addx.ai/DATA/flink-addx)），其他体系按各自约定调整。

## 路由表（先选范式 + 主题，再加载对应 reference）

### 按编程范式选

| 范式 | 适用 | 加载 |
|---|---|---|
| **DataStream API**（Java/Scala/Kotlin） | 复杂 stateful 流处理、自建算子、最大灵活度 | [references/datastream.md](references/datastream.md) |
| **Flink SQL / Table API** | 声明式 ETL、窗口聚合、流批一体、低代码迭代 | [references/flink-sql.md](references/flink-sql.md) |
| **PyFlink** | Python 团队、ML 模型推理嵌入流处理 | [references/pyflink.md](references/pyflink.md) |
| **CEP**（Complex Event Processing） | 模式匹配、风控规则、用户行为序列识别 | [references/cep.md](references/cep.md) |

不确定选哪个？看：

- 业务逻辑能用 SQL 写出来 → **优先 Flink SQL**（运维简单、声明式、Flink 优化器接管）
- SQL 表达不出来（自定义状态机、复杂时间触发、跨流 join 的特殊语义） → **DataStream**
- 团队是 Python 栈 → **PyFlink**（Table API 部分与 Java Table API 语法一致；DataStream 部分功能子集）
- 监测"A 后 5 分钟内发生 B 但没有 C"这类规则 → **CEP**

四种范式可以**混用**——SQL 作业里 `CREATE FUNCTION` 注册 Java UDF；DataStream 里 `tableEnv.fromDataStream()` 转 Table 跑 SQL；PyFlink Table 里 `from_path("...jar")` 加载 Java UDF。

### 按跨范式主题选

| 主题 | 适用范式 | 加载 |
|---|---|---|
| State / Time / Checkpoint / Savepoint | 全部 | [references/state-time-checkpoint.md](references/state-time-checkpoint.md) |
| 测试方法论（L1/L2/L3） | 全部 | [references/testing.md](references/testing.md) |
| 部署 / 集群 / Vault / 可观测性 | 全部 | [references/deployment.md](references/deployment.md) |
| 真实参考项目（含 GitLab 链接） | 全部 | [references/reference-projects.md](references/reference-projects.md) |

## 7 步通用工作流（所有范式共享）

```text
spec/设计 → 代码 → 测试（L1/L2/L3）→ 本地验证 → code review → commit → 部署 → 升级
```

1. **设计**：明确数据来源、keyBy 维度（如有）、状态规模、时间语义（event/processing time）、SLA（端到端延迟、EXACTLY_ONCE vs AT_LEAST_ONCE）
2. **代码**：按范式选模板（见对应 reference）
3. **测试**：L1 单元 + L2 算子/SQL + L3 集群级端到端（见 [testing.md](references/testing.md)）
4. **本地验证**：起依赖容器 → 跑 main → 看启动日志一行打全维度 → 5 分钟内首个 checkpoint
5. **code review**：重点查红线（下文）+ savepoint 兼容性 + 测试覆盖边界
6. **commit**：业务 / 测试 / 配置 / 文档分开；message 写"为什么"
7. **部署 & 升级**：新作业 submit；升级**必须** stop-with-savepoint（见 [state-time-checkpoint.md](references/state-time-checkpoint.md)）

## Rules（红线 —— 违反即线上风险，跨范式通用）

### 1. 每个有状态算子 / SQL operator 必须有稳定 uid

- **DataStream**：每个 operator 调 `.uid("...")` + `.name("...")`，savepoint 恢复靠 uid 配对，漏一个等于丢 state
- **Flink SQL**：通过 `table.exec.uid.generation: ALWAYS` + `table.exec.uid.format` 配置稳定 uid（详见 [references/flink-sql.md](references/flink-sql.md)）；query 结构改变会自动改 uid → 升级前查 savepoint 兼容性
- **CEP**：Pattern 名 + KeyedStream 决定 state；改 Pattern 等于改 state schema

### 2. 改 parallelism / state schema 必须走 stop-with-savepoint

直接重启 → keyed state 重分布行为不可预期（KeyGroups 重映射 + state migration）。详见 [state-time-checkpoint.md](references/state-time-checkpoint.md) §升级流程。

### 3. 永远不写明文密钥

- 业务作业 yaml/properties **只声明** env 变量名（如 `passwordEnv: KAFKA_PASSWORD`），真实值通过 K8s Secret / Vault / SSM 在部署时注入 env
- 声明了 env 但缺失 → **fail-fast**，禁止"静默无认证"降级
- vault path / ExternalSecret / secretKeyRef 注入**不在业务作业仓库**——归集群部署仓库。详见 [deployment.md §vault 配置归属](references/deployment.md)

### 4. EXACTLY_ONCE 要全链路对齐

| 环节 | 配置 |
|---|---|
| Checkpoint mode | `EXACTLY_ONCE`（默认） |
| Source（Kafka） | `KafkaSource.builder()` + `setStartingOffsets(...)` |
| Sink（Kafka） | `DeliveryGuarantee.EXACTLY_ONCE` + 唯一 `setTransactionalIdPrefix()` |
| 下游消费者 | `isolation.level=read_committed`，否则读到未提交事务 |
| Sink 数据库 | 幂等写（upsert by PK）或 2PC sink（JDBC XA） |

任一环节是 at-least-once → 整链路是 at-least-once。

### 5. 异步外部 I/O 必须用 `AsyncDataStream`（DataStream）/ async LookupFunction（SQL）

同步阻塞调外部 RPC/DB 会拖死 task 并撑爆 back-pressure。范式相关用法见各 reference。

### 6. 测试要做防假绿自查

写完任何测试，**故意改坏生产代码**（注释一行业务 / `==` 改 `!=`）跑一次，确认测试**会 fail**。绿色但破不掉的测试 = 没在测真东西。

### 7. 启动日志一行打全关键维度

```text
INFO  region=staging-us job=my-job parallelism=4 checkpointMs=60000 input=topic-x output=topic-y
```

线上排查从这一行开始——缺一个维度就要去翻 yaml/env，浪费 10 分钟。

## 项目耦合点（项目自行决定，本 skill 不规定）

- 配置加载方式（yaml / properties / Consul / etcd）
- region / env 识别优先级（args / env / namespace）
- 密钥注入路径（Vault / SSM / K8s Secret 直接挂）
- 文档 SSOT 位置（`docs/specs/` / Confluence / Notion）
- commit message 语言、PR 模板
- CI/CD pipeline（GitLab / GitHub Actions / Jenkins）
- 集群部署模式（Session / Application / per-job）—— addx 体系用 Session（详见 [deployment.md](references/deployment.md)）

如项目里有 `docs/.../job-development-workflow.md`、`CONTRIBUTING.md` 这类项目级 SSOT 文档，**先读它**——项目约定优先级高于本 skill。

## 加载策略建议

避免一口气吞下所有 reference：

1. **首次使用** → 读 SKILL.md（即本文）+ 对应范式 reference + [reference-projects.md](references/reference-projects.md)
2. **设计 state / window / time** → 加载 [state-time-checkpoint.md](references/state-time-checkpoint.md)
3. **写测试** → 加载 [testing.md](references/testing.md)
4. **部署 / 改 vault / 接告警** → 加载 [deployment.md](references/deployment.md)
5. **跨范式混用**（如 SQL + 自定义 UDF） → 同时加载相关 paradigm reference

每个 reference 都是独立可读的，不强制阅读顺序。

## Examples

下面给出"违反 Rules"与"符合 Rules"的对照示例，方便快速识别审查时的常见 anti-pattern。

### ❌ Bad：operator 漏 uid + 改 parallelism 直接重启

```java
// 反例 1：算子链上没设 .uid()/.name()
stream
    .keyBy(Event::userId)
    .process(new MyDetector())            // ← 漏 .uid()/.name()
    .sinkTo(kafkaSink);

// 反例 2：升级时直接改 parallelism 重启
// $ flink run -p 8 new-job.jar           // ← 没走 stop-with-savepoint
```

后果：savepoint 恢复时算子 uid 不稳定 → state 全丢；改 parallelism 直接重启 → keyed state 重分布不可预期、可能丢数据或重复处理。

### ✅ Good：每个 operator 都带 uid + 升级走 savepoint

```java
// 主流程：算子链每一步都跟 .uid().name()
stream
    .keyBy(Event::userId)
    .process(new MyDetector())
    .uid("my-detector").name("my-detector")
    .sinkTo(kafkaSink)
    .uid("kafka-sink").name("kafka-sink");
```

```bash
# 升级流程：先 stop-with-savepoint，再用新 jar + 老 savepoint 恢复
curl -X POST https://flink-staging-us.addx.live/jobs/<job-id>/savepoints \
     -H "Content-Type: application/json" \
     -d '{"target-directory": "s3://a4x-us-datalake-staging/flink/savepoints/", "cancel-job": true}'

# 拿到 savepoint location 后
flink run -s s3://.../savepoint-xxx -c com.addx.MyJob new-job.jar --region staging-us
```

### ❌ Bad：yaml 写明文密码 / SQL 升级直接重启

```yaml
# 反例：业务 yaml 写明文密码（安全审计 fail；密码进 jar）
kafka:
  password: "ProdScramPassword123!"
```

```java
// 反例：改 SQL plan 结构（如改 join 顺序 / agg key）后直接重启
// → operator uid 全变 → state 全丢
```

### ✅ Good：yaml 只引用 env + SQL 升级前 EXPLAIN 对比 plan

```yaml
# 业务 yaml 只声明 env 变量名，真实值由集群侧 ExternalSecret 从 Vault 注入
kafka:
  passwordEnv: KAFKA_PASSWORD
```

```sql
-- 改 SQL 前 EXPLAIN 对比新旧 plan，确认 operator 结构稳定
EXPLAIN PLAN FOR INSERT INTO sink SELECT ... FROM source;
-- 配 table.exec.uid.generation = ALWAYS + table.exec.uid.format 让 uid 由 operator name 决定
```

更多场景见 [references/](references/) 各 paradigm 的"常见坑"表。

## 相关 Skill

- 提 MR / PR：项目对应的 `gitlab-mr` / `github-pr` skill
- code review：`code-review` skill
- 集群侧（部署 Flink 本身）：项目对应的 `argocd` / `cicd-developer` skill
- 测试方法论：`testing-strategy` skill

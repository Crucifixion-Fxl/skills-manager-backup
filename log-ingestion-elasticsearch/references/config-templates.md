# 配置模板参考

本文档包含 FluentBit 和 Vector 的配置模板，供接入新应用时使用。

## 目录

1. [命名规范](#命名规范)
2. [FluentBit 配置模板](#fluentbit-配置模板)
3. [Vector 配置模板 - US/EU](#vector-配置模板---useu)
4. [Vector 配置模板 - CN](#vector-配置模板---cn)
5. [地区参数速查](#地区参数速查)

---

## 命名参数定义

本文档所有模板用 3 个参数：

| 参数 | 含义 | 示例 | 来源 |
|---|---|---|---|
| `{app}` | **业务/服务逻辑名**。所有 topic / index / ConfigMap data key / tag / DB 文件名都用这个 | `naturehood-api`, `iot-service` | 来自用户需求 + 历史约定，通常等同于 ArgoCD app 名去掉环境后缀 |
| `{container}` | **K8s container 的真实名字**。**仅用于 FluentBit `Path` glob 的匹配**，不用于任何其他地方 | `naturehood`, `api` | 来自 `kubectl get pod <p> -o jsonpath='{.spec.containers[*].name}'`，必须由 preflight Q2 验证过 |
| `{namespace}` | 应用所在 K8s namespace | `staging-eu`, `prod-us`, `default` | `kubectl get pod` 输出 |

**关键事实**：`{app}` 和 `{container}` **可以完全不同**。本次 session 的真实例子：`{app}=naturehood-api`，`{container}=naturehood`。不要假设它们相等。

## 命名规范

| 元素 | US/EU 模式 | CN 模式 |
|------|-----------|---------|
| FluentBit Tag | `{app}-{env}.*` | `{app}-{env}.*` |
| FluentBit Input 文件 | `input-{app}-{env}.conf` | 内联（无独立文件） |
| FluentBit Output 文件 | `output-kafka-{app}-{env}.conf` | 内联（无独立文件） |
| FluentBit Path glob | `/var/log/containers/{container}-*_{namespace}_{container}-*.log` | 同 |
| FluentBit DB 路径 | `/var/log/flb_{app_underscore}_{env}.db` | `/var/log/flb_{app_underscore}_{env}.db` |
| Kafka Topic | `addx-{region}-{env}-{app}` | `addx-cn-{env}-{app}` |
| Vector Source ID | `a4x-{region}-{env}-kafka-topics` | `a4x-cn-{env}-kafka-topics` |
| Vector Transform ID | `journal-shaping-addx-{region}-{env}-{app}` | `journal-shaping-a4x-cn-{env}-{app}` |
| Vector Sink ID | `target-addx-{region}-{env}-{app}` | `target-a4x-cn-{env}-{app}` |
| ES Index | `addx-{region}-{env}-{app}-%Y.%m.%d` | 按服务类型三分，见下方 |

> `{app_underscore}` 是将 `{app}` 里的 `-` 替换为 `_`，如 `naturehood-api` → `naturehood_api`

### CN ES index 按服务类型分叉

CN topic 是统一的 `addx-cn-{env}-{app}`，但 **ES index 按目标 Vector toml 文件分三套**，以同文件里已存在的 `bulk.index` 行为准：

| 服务类型 | 目标文件 | ES index 格式 | 典型服务 |
|---|---|---|---|
| 后端 | `a4x_cn_{env}_backend.toml` | `k8s-{env}-cn-{app}-%Y.%m.%d` | iot-service、auth、webhook、ai-saas、oauth2、crm 等 |
| AI/算法 | `a4x_cn_{env}_ai.toml` | `a4x-algorithm-cn-{env}-{app}-%Y.%m.%d`（部分服务带 `-k8s` 后缀） | gpu-infer、smart-multimedia、flywheel、ai-algo-event-dispatcher 等 |
| KISS | `a4x_cn_all_kiss.toml` | `addx-cn-{env}-kiss-%Y.%m.%d` | kiss（单服务文件） |

**AI 类 `-k8s` 后缀漂移**：prod/staging/test 的 `gpu-infer`、`smart-multimedia` 带 `-k8s`；同环境的 `flywheel`、`ai-algo-event-dispatcher` 不带；pre 环境全部不带。这是存量历史不一致，skill 不强制统一——**接 AI 服务前先 `grep bulk.index /etc/vector/a4x_cn_{env}_ai.toml` 看同文件约定**，沿用即可。

### Path glob 的 3 段结构

K8s container log 文件命名约定是 `<pod-name>_<namespace>_<container-name>-<container-id>.log`（kubelet 创建的符号链接）。glob 必须三段都约束：

```
/var/log/containers/{container}-*_{namespace}_{container}-*.log
                    ^^^^^^^^^^^^^^            ^^^^^^^^^^^^^
                    pod 前缀（= Deployment 名）   container 名
```

- 第一段 `{container}-*`：匹配 pod 前缀。绝大多数情况 Deployment 名和 container 名相同，所以 pod 前缀就是 `{container}-`
- 第二段 `_{namespace}_`：硬约束 namespace，避免其他 ns 同名容器误匹配
- 第三段 `{container}-*.log`：匹配 container 名 + container-id，避免同 ns 其他 Deployment 误匹配

**反面例子**（本次 session 踩过的坑）：

| 错误 glob | 出了什么事 |
|---|---|
| `/var/log/containers/naturehood-api*staging*.log` | container 叫 `naturehood` 不是 `naturehood-api`，0 match |
| `/var/log/containers/naturehood-*.log` | 会误匹配同 ns 的 `naturehood-admin-*`、`naturehood-app-*`、`naturehood-hub-*` 容器 |
| `/var/log/containers/naturehood-*_staging-eu_*.log` | 同上，第三段没约束 container 名 |

正确 glob：`/var/log/containers/naturehood-*_staging-eu_naturehood-*.log`

---

## FluentBit 配置模板

### US/EU: INPUT 配置（作为 ConfigMap 独立数据键）

数据键名：`input-{app}-{env}.conf`

```ini
[INPUT]
    Name              tail
    Tag               {app}-{env}.*
    Path              /var/log/containers/{container}-*_{namespace}_{container}-*.log
    Parser            cri
    DB                /var/log/flb_{app_underscore}_{env}.db
    Mem_Buf_Limit     5MB
    Skip_Long_Lines   On
    Refresh_Interval  10
```

> Path 必须用 3 段 glob（见本文档"Path glob 的 3 段结构"）。**不要**用 `{app}*{env}*.log` 这种宽松模式，session 里证明会 0 match 或误匹配。

### US/EU: OUTPUT 配置（作为 ConfigMap 独立数据键）

数据键名：`output-kafka-{app}-{env}.conf`

```ini
[OUTPUT]
    Name        kafka
    Match       {app}-{env}.*
    Brokers     {zone_broker}
    Topics      addx-{region}-{env}-{app}
    Timestamp_Key  @timestamp
    Timestamp_Format iso8601
    rdkafka.request.required.acks 1
```

**`{zone_broker}` 按 AZ 不同，从同文件已有 OUTPUT 条目中读取。**

US 已知值：
- us-east-1a: `us-east-1a-log-kafka-producer.addx.live:9092`
- us-east-1b: `us-east-1b-log-kafka-producer.addx.live:9092`
- us-east-1c: `us-east-1c-log-kafka-producer.addx.live:9092`

EU 已知值：
- eu-central-1a: `eu-central-1a-log-kafka-producer.addx.live:9092`
- eu-central-1b: `eu-central-1b-log-kafka-producer.addx.live:9092`
- eu-central-1c: `eu-central-1c-log-kafka-producer.addx.live:9092`

**3 个 broker 是 3 个独立的 Kafka 集群**（不是一个多 broker 的集群）。证据：同一 topic 的 partition-0 在 3 个集群的 offset 完全不同（session 里验证过 92M/102M/75M）。每个 zone 的 FluentBit DS 只写本 zone 的 kafka，避免跨 AZ 流量。

### US/EU: fluent-bit.conf @INCLUDE 插入

在 ConfigMap 的 `fluent-bit.conf` 数据键中，按已有条目的位置追加：

```ini
# ... 已有 INPUT includes ...
@INCLUDE input-{app}-staging.conf
@INCLUDE input-{app}-pre.conf
@INCLUDE input-{app}-prod.conf

# ... 已有 OUTPUT includes ...
@INCLUDE output-kafka-{app}-staging.conf
@INCLUDE output-kafka-{app}-pre.conf
@INCLUDE output-kafka-{app}-prod.conf
```

### CN: 内联配置

CN 不使用 @INCLUDE，直接在 `fluent-bit.conf` 中追加。Kafka Broker 固定为 `10.0.5.12:9092`。

INPUT 模板同上。OUTPUT 模板：

```ini
[OUTPUT]
    Name        kafka
    Match       {app}-{env}.*
    Brokers     10.0.5.12:9092
    Topics      addx-cn-{env}-{app}
    Timestamp_Key  @timestamp
    Timestamp_Format iso8601
    rdkafka.request.required.acks 1
```

CN 额外支持 `test` 环境。

---

## Vector 配置模板 - US/EU

US/EU 的 Vector 配置在一个 K8s ConfigMap YAML 文件中，内含多个 TOML 数据键，按环境分组：

```
sources-staging.toml / sources-pre.toml / sources-prod.toml
transforms-staging.toml / transforms-pre.toml / transforms-prod.toml
sinks-staging.toml / sinks-pre.toml / sinks-prod.toml
```

### Source: 添加 Topic

在 `sources-{env}.toml` 的 topics 数组末尾追加：
```toml
    "addx-{region}-{env}-{app}"
```

### Transform: 添加 Remap

> **第一步先选模板**：不要直接 copy 现有 transform。应用的日志行格式决定模板选型——选错会让 Vector **静默丢弃全部事件**（strict `parse_json!` 在非 JSON 上 panic，事件被 drop）。见下方 "Vector transform 模板选择"。

#### Vector transform 模板选择

preflight Q7 给出应用日志格式判定后，按下表选模板：

| 日志行格式 | 典型例子（已接入） | 用模板 | 输出 shape（ES 看到的字段） |
|---|---|---|---|
| nginx / apache 访问日志、lua/nginx error 日志、plain text | **apisix-gateway**, **frps** | **A（默认，最安全）** | `{@timestamp, stream, logtag, log}`，`log` 保留原始 nginx 文本 |
| 应用输出单层 JSON（`{"level":"info","msg":"..."}` 一行一条） | **naturehood-api**（Go/Node.js 结构化日志） | **B** | 应用 JSON 字段平铺到顶层（`level`, `caller`, `content` 等） |
| FB 外层 + 应用 JSON + 再有内嵌 `.log.message` JSON 的 legacy 结构 | **iot-service**, **iot-consumer**（老 Java stack） | **C** | 应用字段 + 内嵌 message 字段全部平铺 |
| 不确定 / 混合 / 同一 pod 既有 access 又有 error 文本 | — | **A（保守）** | 同 A；后续想深挖可升级 B/C |

**默认选 A**。只有确定应用是 JSON logger 且格式稳定，才选 B 或 C。

> 所有模板 `inputs` 中的 source 名从同环境已有 transform 条目中获取（如 `a4x-{region}-{env}-kafka-topics`）。

---

#### 模板 A · 纯文本 / 混合日志（默认）

在 `transforms-{env}.toml` 末尾追加：

```toml
[transforms.journal-shaping-addx-{region}-{env}-{app}]
inputs = ["a4x-{region}-{env}-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-{region}-{env}-{app}" {
    . = parse_json!(.message)
  } else {
    abort
  }
'''
```

**做什么**：只 parse FluentBit 外层 JSON 包装（这层永远是合法 JSON），把 `.log` / `.stream` / `@timestamp` 展开到顶层，`.log` 保留为原始字符串交给 ES 全文检索。

**不要这么写**：加一行 `.log = parse_json!(.log)` —— 对非 JSON 日志会 panic、drop 事件（真踩过的坑：apisix-gateway ES 收到 0 业务文档直到修成模板 A）。

---

#### 模板 B · 单层 JSON 应用日志

```toml
[transforms.journal-shaping-addx-{region}-{env}-{app}]
inputs = ["a4x-{region}-{env}-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-{region}-{env}-{app}" {
    . = parse_json!(.message)
    .log = parse_json!(.log)
    . = merge!(., .log)
    del(.time)
    del(.stream)
    del(.log)
  } else {
    abort
  }
'''
```

**做什么**：FB 外层 parse → `.log` 再 parse（应用的一行 JSON）→ merge 到顶层。应用 JSON 的 `level` / `caller` / `trace` 等字段成为顶层字段，Kibana 可直接 filter。

**前置验证**：随机拉应用 20 行日志，`tail -20 | jq .` 不报错（即每一行都是合法 JSON）。否则用 A。

---

#### 模板 C · 嵌套 JSON（legacy Java：`log.message` 再包一层）

```toml
[transforms.journal-shaping-addx-{region}-{env}-{app}]
inputs = ["a4x-{region}-{env}-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-{region}-{env}-{app}" {
    ., err = parse_json(.message)
    .log, err = parse_json(.log)
    ., err = merge(., .log)
    .log.message, err = parse_json(.log.message)
    if err == null {
      ., err = merge(., .log.message)
    }
    del(.time)
    del(.stream)
    del(.log)
  } else {
    abort
  }
'''
```

**做什么**：在 B 的基础上再 parse 一层 `.log.message`（老 Java 有时会把业务 JSON 再包一层）。用 `err` 容错模式（非 strict），碰到非 JSON 时跳过内层 merge 而不 drop 事件。

**典型特征**：ES 里已有该服务的文档，顶层有 `level`/`trace`，但业务字段堆在 `message` 字符串里。

---

### 已有服务 → 模板索引（便于 grep 范式）

| 地区 | 环境 | 服务 | topic | 用了模板 |
|---|---|---|---|---|
| us | staging | apisix-gateway | `addx-us-staging-apisix-gateway` | A |
| us | staging | frps | `addx-us-staging-frps` | A |
| us | staging/prod | naturehood-api | `addx-us-{env}-naturehood-api` | B |
| us | staging/prod | iot-service | `addx-us-{env}-iot-service` | C |
| us | staging/prod | iot-consumer | `addx-us-{env}-iot-consumer` | C |

接新服务前先 grep 同类服务范式：
```bash
grep -B1 -A15 "journal-shaping-addx-{region}-{env}-<类似服务>" clusters/aws-*/vector/configmap/vector-config.yaml
```

### Sink: 添加 Elasticsearch

在 `sinks-{env}.toml` 末尾追加：

```toml
[sinks.target-addx-{region}-{env}-{app}]
type = "elasticsearch"
inputs = ["journal-shaping-addx-{region}-{env}-{app}"]
endpoints = ["{es_endpoint}"]
bulk.index = "addx-{region}-{env}-{app}-%Y.%m.%d"
```

> `{es_endpoint}` 从同文件已有 sink 条目中读取。

---

## Vector 配置模板 - CN

CN 的 Vector 运行在独立主机 `cn-public-log-vector-01` (49.232.30.205)。

### 配置文件结构

配置目录：`/etc/vector/`

| 文件 | 用途 |
|------|------|
| `vector.toml` | 全局配置（API、metrics） |
| `a4x_cn_test_backend.toml` | test 环境后端服务 |
| `a4x_cn_staging_backend.toml` | staging 环境后端服务 |
| `a4x_cn_pre_backend.toml` | pre 环境后端服务 |
| `a4x_cn_prod_backend.toml` | prod 环境后端服务 |
| `a4x_cn_test_ai.toml` | test 环境 AI 服务 |
| `a4x_cn_staging_ai.toml` | staging 环境 AI 服务 |
| `a4x_cn_pre_ai.toml` | pre 环境 AI 服务 |
| `a4x_cn_prod_ai.toml` | prod 环境 AI 服务 |
| `a4x_cn_all_kiss.toml` | kiss 服务（所有环境） |

普通后端应用修改 `_backend.toml` 文件，AI/算法应用修改 `_ai.toml` 文件。

### SSH 访问

```bash
ssh -i ~/.ssh_addx/id_rsa root@49.232.30.205
```

**修改前必须先执行本机备份（SKILL.md Step 2 中的备份步骤），确保备份完成后才能继续。** 这是因为 CN 配置不在 git 中，没有版本历史可回退。

### Source: 添加 Topic

在 `a4x_cn_{env}_backend.toml` 的 topics 数组末尾追加：
```toml
            "addx-cn-{env}-{app}"
```

### Transform: 添加 Remap

CN 的 transform 模式与 US/EU 略有不同（使用 `err` 模式而非 `!` 断言）：

```toml
[transforms.journal-shaping-a4x-cn-{env}-{app}]
inputs = ["a4x-cn-{env}-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-cn-{env}-{app}" {
    ., err = parse_json(.message)
    .message, err = parse_json(.message)
    ., err = merge(., .message)
    .log, err = parse_json(.message)
    if err == null {
        ., err = merge(., .log)
    }
    del(.time)
    del(.stream)
    del(.log)
  } else {
    abort
  }
'''
```

### Sink: 添加 Elasticsearch

**`bulk.index` 按服务类型选**（详见上方"CN ES index 按服务类型分叉"小节）。以下示例对应 backend 类（`_backend.toml`）：

```toml
[sinks.target-a4x-cn-{env}-{app}]
type = "elasticsearch"
inputs = ["journal-shaping-a4x-cn-{env}-{app}"]
endpoints = ["http://10.0.5.20:9200"]
bulk.index = "k8s-{env}-cn-{app}-%Y.%m.%d"
```

AI 服务改 `_ai.toml`，`bulk.index` 改成 `"a4x-algorithm-cn-{env}-{app}-%Y.%m.%d"`（或 `-k8s` 变体，**先 grep 同文件确认**）。KISS 走 `a4x_cn_all_kiss.toml`，`bulk.index` 是 `"addx-cn-{env}-kiss-%Y.%m.%d"`。

### 重启 Vector

```bash
ssh -i ~/.ssh_addx/id_rsa root@49.232.30.205 "systemctl restart vector && systemctl status vector"
```

---

## 地区参数速查

### 跨集群拓扑（关键阅读）

**FluentBit 和 Vector 可能不在同一个集群**。一个地区里：

- **FluentBit DaemonSet** 是 per-cluster 的。它是 DaemonSet，只能 tail 本集群节点上的 `/var/log/containers/*.log`。所以应用在哪个集群，就要改**那个集群**的 FB ConfigMap。
- **Vector Deployment** 是 per-region 集中的。它从 log kafka 消费（kafka 跨集群可达），通常只在本地区的一个"主" prod 集群跑，其他集群的 git 里可能也有 vector 配置但 `replicas=0`。Vector 配置只改**主** prod 集群的 vector ConfigMap 即可。
- **log kafka** 是地区共享的（3 个 AZ 独立集群），FB 从任何集群写 topic，Vector 在主集群消费，Topic 名一致。

所以接入一个新应用的标准判断流程：

1. preflight Q1 先确定应用所在 context → 决定改哪个 FB 配置
2. 根据地区查"Vector 主集群"（下表）→ 决定改哪个 Vector 配置
3. 两个改动通常在**不同**的 `clusters/<cluster>/...` 目录下

---

### US（FB 跨集群拓扑 / Vector 集中 us-prod）

| 参数 | 值 |
|---|---|
| **FB 集群 A** — tech-service 类 workloads | `aws-002497567426-us-tech-service`（cluster alias 可能叫 `us-staging`，注意不要被 context/cluster 名称对照迷惑）|
| **FB 集群 B** — prod workloads | `aws-302571458622-us-prod` |
| 两个集群都已有 git 管 zone FB DS | `clusters/<cluster>/fluent-bit/fluent-bit-configmap-us-east-1{a,b,c}.yaml` + `fluent-bit-ds-us-east-1{a,b,c}.yaml` |
| **Vector 主集群** | `aws-302571458622-us-prod` —— 2/2/2 Running，消费 log kafka（tech-service 里 Vector deployment `replicas=0`，不生效） |
| Vector 配置 git 路径 | `clusters/aws-302571458622-us-prod/vector/configmap/vector-config.yaml` |
| Vector KAFKA_SERVER | 按 AZ 分别是 `us-east-1{a,b,c}-log-kafka-consumer.addx.live:9092` |
| Kafka Broker (FB 写) | `us-east-1{a,b,c}-log-kafka-producer.addx.live:9092`（3 个 AZ 独立集群，任何 FB 集群都用这同一套 DNS，根据自身 zone 选 a/b/c） |
| Topic 前缀 | `addx-us-` |
| ES Index 格式 | `addx-us-{env}-{app}-%Y.%m.%d` |
| FluentBit namespace | `logging` |
| Vector namespace | `vector` |

> **常见误判**：ArgoCD app name 含 `-us` 不一定在 `us-prod`。`naturehood-api-staging-us` 对应的 pod 在 tech-service 集群（context `aws-002497567426-us-tech-service`）的 `staging-us` namespace。务必用 preflight Q1 实际 grep pod 验证。

---

### EU（FB 跨集群拓扑 / Vector 集中 eu-prod）

| 参数 | 值 |
|---|---|
| **FB 集群 A** — tech-service 类 workloads | `aws-010840394398-eu-tech-service`（见下方 "EU tech-service 细节"） |
| **FB 集群 B** — prod workloads | `aws-740315635167-eu-prod` |
| 两个集群都已有 git 管 zone FB DS | `clusters/<cluster>/fluent-bit/fluent-bit-configmap-eu-central-1{a,b,c}.yaml` + `fluent-bit-ds-eu-central-1{a,b,c}.yaml` |
| **Vector 主集群** | `aws-740315635167-eu-prod` —— 3 zone deployment 各 2 replicas Running |
| Vector 配置 git 路径 | `clusters/aws-740315635167-eu-prod/vector/configmap/vector-config.yaml` |
| Vector KAFKA_SERVER | 按 AZ 分别是 `eu-central-1{a,b,c}-log-kafka-consumer.addx.live:9092` |
| Kafka Broker (FB 写) | `eu-central-1{a,b,c}-log-kafka-producer.addx.live:9092`（3 个 AZ 独立集群） |
| Topic 前缀 | `addx-eu-` |
| ES Endpoint | `http://vpc-elasticsearch-eu-yfklxtdj2matdledzkd6j4icrm.eu-central-1.es.amazonaws.com:80` |
| ES Index 格式 | `addx-eu-{env}-{app}-%Y.%m.%d` |
| Prometheus kafka 指标 job | `eu-prod-public-kafka-metrics`（log kafka，3 个 exporter 端口 19309/29309/39309 对应 1a/1b/1c） |
| FluentBit namespace | `logging` |
| Vector namespace | `vector` |

#### EU tech-service 细节 (`aws-010840394398-eu-tech-service`)

2026-04-13 前该集群只有 Helm stub FB（OUTPUT 只有 stdout），没有真正的日志管道。session 里接入 naturehood-api 时采用 "eu-prod 同款共存模式"：
- 在 `clusters/aws-010840394398-eu-tech-service/fluent-bit/` 新建 3 个 zone ConfigMap + 3 个 zone DaemonSet
- 新 DS 的 `serviceAccountName: fluent-bit` 复用 Helm 创建的 SA
- apply 后手动 `kubectl delete ds fluent-bit -n logging` 删掉 Helm 的 DS（保留 SA/CR/CRB/CM/Service）
- 使用 eu-prod 同一套 log kafka / vector / ES
- 3 个 broker 的网络必须先从 tech-service 集群连通性验证（见 preflight-checklist Q5）

### CN (tencent-100014919455-cn-main)

| 参数 | 值 |
|------|-----|
| kubectl context | `tencent-100014919455-cn-main` |
| FluentBit ConfigMap 数量 | 1 |
| Kafka Broker | `10.0.5.12:9092` (CKafka: cn-public-log-kafka) |
| Topic 前缀 | `addx-cn-` |
| Vector 主机 | `cn-public-log-vector-01` (49.232.30.205 / 10.0.64.16) |
| Vector 配置目录 | `/etc/vector/` |
| Vector 管理方式 | systemd 服务 |
| ES Endpoint | `http://10.0.5.20:9200` |
| ES Index 格式 | backend: `k8s-{env}-cn-{app}-%Y.%m.%d`<br>AI: `a4x-algorithm-cn-{env}-{app}[-k8s]-%Y.%m.%d`<br>KISS: `addx-cn-{env}-kiss-%Y.%m.%d`（详见"CN ES index 按服务类型分叉"） |
| 额外环境 | `test` |
| tccli profile | `tencent-100014919455-cn-main` |

---

## 完整示例：接入 naturehood-api (EU, staging)

本示例取自 2026-04-13 真实接入的工作状态。关键参数：

- `{app}` = `naturehood-api`（业务名）
- `{container}` = `naturehood`（真实 K8s container 名；通过 `kubectl get pod -o jsonpath='{.spec.containers[*].name}'` 查到）
- `{namespace}` = `staging-eu`
- 目标集群：`aws-010840394398-eu-tech-service`（tech-service 空壳接管模式，见前 "EU tech-service" 小节）
- kafka 和 vector 复用 eu-prod (`aws-740315635167-eu-prod`)

### FluentBit (fluent-bit-configmap-eu-central-1a.yaml)

fluent-bit.conf 中新增 @INCLUDE：
```
@INCLUDE input-naturehood-api-staging.conf
...
@INCLUDE output-kafka-naturehood-api-staging.conf
```

新增数据键 `input-naturehood-api-staging.conf`：
```ini
[INPUT]
    Name              tail
    Tag               naturehood-api-staging.*
    Path              /var/log/containers/naturehood-*_staging-eu_naturehood-*.log
    Parser            cri
    DB                /var/log/flb_naturehood_api_staging.db
    Mem_Buf_Limit     5MB
    Skip_Long_Lines   On
    Refresh_Interval  10
```

> 注意 `{app}=naturehood-api` 和 `{container}=naturehood` 不同。Tag/DB/数据键名用 `{app}`，Path glob 用 `{container}`。

新增数据键 `output-kafka-naturehood-api-staging.conf`：
```ini
[OUTPUT]
    Name        kafka
    Match       naturehood-api-staging.*
    Brokers     eu-central-1a-log-kafka-producer.addx.live:9092
    Topics      addx-eu-staging-naturehood-api
    Timestamp_Key  @timestamp
    Timestamp_Format iso8601
    rdkafka.request.required.acks 1
```

### Vector (vector-config.yaml)

`sources-staging.toml` 中 topics 数组追加：
```toml
    "addx-eu-staging-naturehood-api"
```

`transforms-staging.toml` 末尾追加：
```toml
[transforms.journal-shaping-addx-eu-staging-naturehood-api]
inputs = ["a4x-eu-staging-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-eu-staging-naturehood-api" {
    ., err = parse_json(.message)
    .log, err = parse_json(.log)
    ., err = merge(., .log)
    .log.message, err = parse_json(.log.message)
    if err == null {
      ., err = merge(., .log.message)
    }
    del(.time)
    del(.stream)
    del(.log)
  } else {
    abort
  }
'''
```

`sinks-staging.toml` 末尾追加：
```toml
[sinks.target-addx-eu-staging-naturehood-api]
type = "elasticsearch"
inputs = ["journal-shaping-addx-eu-staging-naturehood-api"]
endpoints = ["http://vpc-elasticsearch-eu-yfklxtdj2matdledzkd6j4icrm.eu-central-1.es.amazonaws.com:80"]
bulk.index = "addx-eu-staging-naturehood-api-%Y.%m.%d"
```

---

## 完整示例：CN 接入（占位 / 未经本 skill 二期验证）

> 本小节使用占位 `{app}` / `{container}` / `{env}`，未在 2026-04-13 session 中验证过。使用时请先用 preflight Q1-Q6 做一轮，特别是 Q2 的 container 名验证。

### FluentBit (fluent-bit-cm.yaml 内联)

在 fluent-bit.conf 的 INPUT 区域末尾追加：
```ini
[INPUT]
    Name              tail
    Tag               {app}-{env}.*
    Path              /var/log/containers/{container}-*_{namespace}_{container}-*.log
    Parser            cri
    DB                /var/log/flb_{app_underscore}_{env}.db
    Mem_Buf_Limit     5MB
    Skip_Long_Lines   On
    Refresh_Interval  10
```

在 OUTPUT 区域末尾追加：
```ini
[OUTPUT]
    Name        kafka
    Match       {app}-{env}.*
    Brokers     10.0.5.12:9092
    Topics      addx-cn-{env}-{app}
    Timestamp_Key  @timestamp
    Timestamp_Format iso8601
    rdkafka.request.required.acks 1
```

### Vector (SSH 修改 /etc/vector/a4x_cn_{env}_backend.toml)

topics 数组追加：
```toml
            "addx-cn-{env}-{app}"
```

文件末尾追加 transform：
```toml
[transforms.journal-shaping-a4x-cn-{env}-{app}]
inputs = ["a4x-cn-{env}-kafka-topics"]
type = "remap"
source = '''
  if .topic == "addx-cn-{env}-{app}" {
    ., err = parse_json(.message)
    .message, err = parse_json(.message)
    ., err = merge(., .message)
    .log, err = parse_json(.message)
    if err == null {
        ., err = merge(., .log)
    }
    del(.time)
    del(.stream)
    del(.log)
  } else {
    abort
  }
'''
```

文件末尾追加 sink（示例是 backend 类；AI / KISS 参考上文 "CN ES index 按服务类型分叉" 选用对应 `bulk.index` 格式）：
```toml
[sinks.target-a4x-cn-{env}-{app}]
type = "elasticsearch"
inputs = ["journal-shaping-a4x-cn-{env}-{app}"]
endpoints = ["http://10.0.5.20:9200"]
bulk.index = "k8s-{env}-cn-{app}-%Y.%m.%d"
```

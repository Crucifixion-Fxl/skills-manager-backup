---
name: superset-periodic-report
description: 基于 Superset 的周期性数据分析与报告生成框架。触发后必先检查与 SKILL 同目录的 .env（Superset 凭据）与 .local-config.md 是否存在且可用；缺失则仅口头引导补齐，禁止调 API。从看板拉取指标数据生成数据报告，验证数据口径，交叉分析多表一致性，分析错误码/失败率/恢复率/漏斗丢弃率。当用户提到以下关键词时触发：数据报告、业务指标周期报告、SLA 数据报告、生成数据报告、更新数据报告、绑定周期报告、拉取看板数据、chart 数据、数据口径验证、交叉分析、去重逻辑、错误码分析、失败率分析、恢复率、丢弃率、漏斗分析。注意：工作日报/周报请使用 standup-writer skill；实时告警排查请使用 sla-alert-analysis skill；用户原声/评论类 VOC 分析请使用 voc-analysis skill。
---

# Superset 周期报告 — 数据分析与周报通用框架

## Description

基于 Superset REST API 的数据分析与周报生成框架。通过 chart/data API 和 SQL Lab 从看板拉取指标数据，支持周报生成、数据口径验证、交叉分析、错误码排查、恢复率计算、漏斗丢弃率分析等场景。适用于绑定、直播、回看、离线、OTA、激活等任意业务域。

## 适用场景

- 从 Superset 看板拉取任意业务指标，生成/更新周期报告
- 验证数据口径（去重逻辑、过滤条件是否合理）
- 交叉验证多张表的数据一致性
- 错误码/失败原因分布分析
- 恢复率/转化率等衍生指标计算
- 漏斗丢弃率分析

**与 sla-alert-analysis 的分工**：本 Skill 负责**周期性分析和报告生成**（计划驱动）。如果分析过程中发现实时告警需要排查，引导用户使用 `sla-alert-analysis` skill 做告警下钻。反之，`sla-alert-analysis` 排查告警时如需口径验证或历史报告对比，可引导到本 Skill。

## 配置

### 凭据（.env）

```
SUPERSET_URL=<superset_url>
SUPERSET_USERNAME=<username>
SUPERSET_PASSWORD=<password>
```

### 业务域配置（.local-config.md）

每个业务域的具体数据源（表名、Dataset ID、Chart ID、字段名、看板链接、代码路径等）存放在同目录 `.local-config.md`。**使用前必须先读取该文件。**

## Rules

### Rule 1 — 触发后必先检查前置条件（未通过则停止）

本 Skill **一旦被触发**，在发起**任何** Superset 登录、`chart/data`、SQL Lab 或其它 API 调用**之前**，必须先完成下列检查；**不通过则仅口头引导用户补齐配置，禁止继续调 API**。

1. **凭据**：`SUPERSET_URL`、`SUPERSET_USERNAME`、`SUPERSET_PASSWORD` 必须可用且非空。优先读取与 `SKILL.md` **同目录**的 `.env`（见「配置」节变量名）；若无 `.env`，则确认当前执行环境已 `export` 上述变量且已核实非空。缺失或仍为占位符 → **停止**，说明在同目录创建 `.env` 并填入三项，**不得**执行 login。
2. **`.local-config.md`**：与 `SKILL.md` **同目录**下文件必须**存在**。不存在 → **停止**，说明需新建 `.local-config.md` 并写入目标业务域的表名、Dataset ID、Chart ID、看板链接等（可对照团队已有业务域示例结构），**不得**臆造 ID 调 API。

仅当前置条件全部满足后，才进入 Rule 2 及之后的步骤。

### Rule 2 — 使用前必须读取 .local-config.md

每个业务域的数据源（表名、Dataset ID、Chart ID、字段名、看板链接）存放在同目录 `.local-config.md`。**执行任何查询前必须先读取该文件**，获取目标业务域的具体配置。若某业务域为 TODO：仅在前置凭据（Rule 1）已就绪时，方可通过 Superset API 探索 chart/dataset 并回填 `.local-config.md`；若凭据未就绪，只引导配置，不调 API。

### Rule 3 — 认证三步不可省略

必须按顺序执行：JWT Token → CSRF Token（带 cookie jar）→ 后续请求携带全部凭证。缺少任何一步都会导致 403/CSRF 错误。

### Rule 4 — 注意 Dataset 默认时间范围

许多 Dataset 底层 SQL 使用 Jinja 模板，不传 time_range 时默认只查近 1 天。**chart/data API 必须显式传 time_range**，否则数据量严重偏小。首次查询新 Dataset 时，先通过 `GET /api/v1/dataset/ID` 检查其 SQL 定义中的时间默认值。

### Rule 5 — 看板数据需交叉验证

看板数字不能直接信任。常见问题：
- 失败率口径为"至少失败一次"，需交叉成功表算真实失败率
- 去重逻辑可能排除有效数据，需对比"有过滤 vs 无过滤"的差异
- 不同 Dataset 可能对同一指标有不同计算口径

### Rule 6 — SQL 安全红线

- 必须加 `LIMIT`（默认不超过 1000 行）
- 只允许 SELECT，禁止 DDL/DML
- 大表查询必须加分区过滤（如日期分区）

## Superset 认证（三步）

```bash
COOKIE_JAR="/tmp/superset_cookies.txt"

# Step 1: JWT Token
SUPERSET_TOKEN=$(curl -s -c "$COOKIE_JAR" -X POST "$SUPERSET_URL/api/v1/security/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SUPERSET_USERNAME\",\"password\":\"$SUPERSET_PASSWORD\",\"provider\":\"db\"}" \
  | jq -r '.access_token')

# Step 2: CSRF Token（POST 请求需要）
CSRF_TOKEN=$(curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$SUPERSET_URL/api/v1/security/csrf_token/" \
  -H "Authorization: Bearer $SUPERSET_TOKEN" | jq -r '.result')

# Step 3: 后续请求同时携带 cookie jar + Bearer + CSRF + Referer
```

**关键坑点**：
- POST 请求**必须用 cookie jar 保持会话**，否则 CSRF 校验失败
- Token 有效期约 1 小时，过期后重新执行 Step 1-2

## 查询模式

### 模式 A：chart/data API（推荐，已有看板时使用）

```bash
curl -s -b "$COOKIE_JAR" -X POST "$SUPERSET_URL/api/v1/chart/data" \
  -H "Authorization: Bearer $SUPERSET_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -H "Referer: $SUPERSET_URL/" \
  -d '{
    "datasource": {"id": DATASET_ID, "type": "table"},
    "force": true,
    "queries": [{
      "columns": ["分组字段"],
      "metrics": ["指标名或自定义SQL指标"],
      "filters": [{"col": "字段", "op": "IN", "val": ["值"]}],
      "time_range": "FROM_DT : TO_DT",
      "extras": {"where": "额外SQL条件"},
      "orderby": [["排序指标", false]],
      "row_limit": 10,
      "order_desc": true
    }],
    "result_format": "json",
    "result_type": "full"
  }'
```

自定义 SQL 指标格式：
```json
{"expressionType": "SQL", "sqlExpression": "COUNT(*)", "label": "cnt"}
```

### 模式 B：SQL Lab 自定义查询

```bash
curl -s -b "$COOKIE_JAR" -X POST "$SUPERSET_URL/api/v1/sqllab/execute/" \
  -H "Authorization: Bearer $SUPERSET_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -H "Referer: $SUPERSET_URL/" \
  -d '{"database_id": DB_ID, "sql": "SELECT ... LIMIT 1000", "schema": "SCHEMA"}'
```

### 模式 C：探索未知 Chart/Dataset

```bash
# 获取 chart 配置
curl -s "$SUPERSET_URL/api/v1/chart/CHART_ID" -H "Authorization: Bearer $SUPERSET_TOKEN"

# 获取 dataset SQL 定义和 metrics
curl -s "$SUPERSET_URL/api/v1/dataset/DATASET_ID" -H "Authorization: Bearer $SUPERSET_TOKEN"

# 搜索 chart by name
curl -s "$SUPERSET_URL/api/v1/chart/?q=ENCODED_FILTER" -H "Authorization: Bearer $SUPERSET_TOKEN"
```

## 常见坑点

### 1. Dataset 默认时间范围

许多 Dataset 底层 SQL 使用 Jinja 模板，不传 time_range 时可能默认只查近1天：
```sql
{% if from_dttm is defined %}
  BETWEEN '{{from_dttm}}' AND '{{to_dttm}}'
{% else %}
  >= date_add('day', -1, current_date)  -- 默认近1天
{% endif %}
```
**解决**：chart/data API 显式传 `"time_range": "FROM : TO"`

### 2. Athena DATE 类型

```sql
-- 错误：dt >= '2026-03-01'  (TYPE_MISMATCH)
-- 正确：dt >= DATE '2026-03-01'
```

### 3. mixed_timeseries Chart

混合图表有两组查询（metrics/metrics_b, filters/filters_b），需分别处理。

### 4. 看板失败率 vs 真实失败率

看板常用"至少失败过一次的占比"，会偏高。需交叉验证成功表，计算"失败后最终成功"的恢复率，得出真实失败率。

## 分析工作流

### 1. 周报生成

```
Rule 1 前置检查（.env 凭据 + .local-config.md 存在）→ 读取 .local-config.md
  → 认证 Superset → chart/data API 拉取各维度数据
  → 整理到报告模板 → 计算环比 → 标注异常值
```

### 2. 数据口径验证

```
获取 dataset SQL 定义 → 分析过滤/去重逻辑
  → SQL Lab 跑对比实验（有过滤 vs 无过滤）
    → 查被排除数据的特征 → 判断过滤是否合理
```

### 3. 错误码/失败分析

```
拉取错误码分布（按版本/固件）→ 交叉验证恢复率
  → 区分用户侧 vs 系统侧问题 → 定位代码实现（如有源码）
```

### 4. 漏斗丢弃率分析

```
拉取漏斗各步骤数据 → 计算转化率和丢弃率
  → 按版本/平台对比 → 建议补充埋点
```

## 报告模板

具体分哪些维度以看板与 Dataset 为准（见 `.local-config.md`），常见切片包括但不限于：版本、平台（iOS/Android）、租户/产品线、地域/机房、配网方式、错误码、固件、App 渠道等。

```markdown
# [业务域] 周期数据报告
> 报告周期：YYYY-MM-DD ~ YYYY-MM-DD
> 数据来源：Superset

## 一、核心指标总览
## 二、分维度明细（按实际 chart 分组/过滤字段展开，勿写死单一维度）
## 三、数据来源说明
## 四、数据口径验证（如做过）
```

## Examples

### Bad

```bash
# 不传 time_range 直接查 chart/data API — 拿到的数据只有近1天，量严重偏小
curl -X POST "$SUPERSET_URL/api/v1/chart/data" \
  -d '{"datasource": {"id": 1100, "type": "table"}, "queries": [{"columns": ["version"], "metrics": ["success_rate"]}]}'
# 结果：某版本只有 4 个用户，实际近30天有 2000+
```

```bash
# 不用 cookie jar，直接带 CSRF token — 必然 403
curl -X POST "$SUPERSET_URL/api/v1/sqllab/execute/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-CSRFToken: $CSRF" \
  -d '{"database_id": 2, "sql": "SELECT ..."}'
# 错误：400 Bad Request: The CSRF session token is missing.
```

```bash
# Athena DATE 字段用字符串比较 — 类型不匹配
{"sql": "SELECT * FROM table WHERE dt >= '2026-03-01'"}
# 错误：TYPE_MISMATCH: Cannot apply operator: date <= varchar(10)
```

### Good

```bash
# 正确：认证三步 + cookie jar + 显式 time_range
COOKIE_JAR="/tmp/superset_cookies.txt"
SUPERSET_TOKEN=$(curl -s -c "$COOKIE_JAR" -X POST "$SUPERSET_URL/api/v1/security/login" ...)
CSRF_TOKEN=$(curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$SUPERSET_URL/api/v1/security/csrf_token/" ...)

curl -s -b "$COOKIE_JAR" -X POST "$SUPERSET_URL/api/v1/chart/data" \
  -H "Authorization: Bearer $SUPERSET_TOKEN" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -H "Referer: $SUPERSET_URL/" \
  -d '{"datasource": {"id": 1100, "type": "table"}, "force": true,
    "queries": [{"columns": ["version"], "metrics": ["success_rate"],
      "time_range": "2026-03-03T00:00:00 : 2026-04-02T23:59:59"}]}'
```

```bash
# 正确：Athena DATE 字段用 DATE 关键字
{"sql": "SELECT * FROM table WHERE dt >= DATE '2026-03-01' LIMIT 500"}
```

```
# 正确：看板数据交叉验证
看板失败率 57% → 交叉成功表 → 86% 失败设备最终成功 → 真实失败率 8%
→ 看板口径是"至少失败一次"，不等于"最终未成功"
```

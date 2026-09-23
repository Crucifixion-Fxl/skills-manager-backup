---
name: superset
description: Apache Superset 数据可视化与 BI 平台操作。查询 Dashboard、管理 Chart、浏览 Dataset、执行 SQL Lab 查询、查看数据库连接、或查询某业务库表在数仓的对应表名 / 某数仓表来自哪个业务库源表（经 data_sync 映射）时使用。
---

# superset

通过 Apache Superset REST API 管理 Dashboard、Chart、Dataset，执行 SQL 查询，浏览数据可视化资产。

## Description

适用场景：Dashboard 浏览、Chart 管理、SQL Lab 即席查询、Dataset 巡检。

## Rules

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `SUPERSET_URL` | 固定为 `https://superset-us.addx.live` | 是 |
| `SUPERSET_USERNAME` | 登录用户名 | 是 |
| `SUPERSET_PASSWORD` | 登录密码 | 是 |

### 认证（两步 JWT）

Superset 使用 JWT Token 认证，需先通过 login 接口获取 access_token，再在后续请求中携带 Bearer Token：

```bash
# Step 1: 获取 JWT Token
export SUPERSET_TOKEN=$(curl -s -X POST "$SUPERSET_URL/api/v1/security/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SUPERSET_USERNAME\",\"password\":\"$SUPERSET_PASSWORD\",\"provider\":\"db\"}" \
  | jq -r '.access_token')

# Step 2: 后续所有请求携带 -H "Authorization: Bearer $SUPERSET_TOKEN"
```

> Token 有效期约 1 小时，过期后重新执行 Step 1。

### 认证（login2：Superset 里为 agent 创建的账号密码）

给 agent 用的服务账号（在 Superset 里创建、有本地密码）通过 `/login2/?next=...` 表单登录建立浏览器会话，完整契约与安全探针见 [login2 会话认证](references/login2-session.md)：保留登录页 CSRF、`Referer`、`User-Agent` 与同一个 cookie jar，以「目标页和目标 API 可读」作为成功判据。**login2 登录失败会重定向到 `/login/`**——那是 SSO 入口，是失败信号而不是可行路径；先核对用户名/密码是否同一服务账号的配对，不要据此判定密码失效。

个人用户不走这条：个人在浏览器 SSO 登录、拿到 token 后粘贴使用，不进本 skill 的程序化登录路径。

### API 参考

API 用法通过 Context7 MCP 查询或访问 Swagger (`https://superset-us.addx.live/swagger/v1`)。

### 数据安全红线

- 业务数据 SELECT 查询**必须加 `LIMIT`**（默认不超过 1000 行），禁止无限制全表扫描
- 只允许 SELECT 和用于识别数据源定义的只读元数据语句（`SHOW CREATE TABLE`、`SHOW CREATE VIEW`）；禁止 DDL（CREATE/DROP/ALTER）和 DML（INSERT/UPDATE/DELETE）
- PII 字段（手机号、邮箱、身份证号）在结果中必须脱敏展示
- 大表查询必须加分区过滤条件（如日期分区），避免高额计算成本

### SQL Lab API 长查询（同步超时与异步轮询）

程序化执行 SQL 时，同步接口约 280s 会被断连（socket 超时）。重查询走 runAsync 轮询：

1. `POST /api/v1/sqllab/execute/`，body 带 `"runAsync": true` → 返回 `query.queryId`（state=pending）
2. 轮询 `GET /api/v1/query/<queryId>` → `result.status` 变 `success` 后取 `result.results_key`
3. 取结果 `GET /api/v1/sqllab/results/?q=(key:'<results_key>')`（注意 `q` 是 rison 编码，直接 `?key=...` 会报 400）

两个易踩的坑：POST 除 Bearer Token 外还必须带 `X-CSRFToken`（先 GET `/api/v1/security/csrf_token/`）和会话 Cookie；所有 sqllab 请求必须带 `Referer: $SUPERSET_URL/sqllab/`，否则 400 "The referrer header is missing"。

### 新建 Chart / Dashboard 分区规则

- **创建前必须确认真实分区字段** — 新建 Chart 前，先通过 DataHub / Data Search、Dataset 元数据或数据库元数据确认每张源表的分区字段及分区表达式；新建 Dashboard 时，其包含的每个 Chart 都必须通过此检查
- **不确定时检查真实 DDL** — 表执行 `SHOW CREATE TABLE <table>`；View 执行 `SHOW CREATE VIEW <view>` 找到底层源表，再对底层表执行 `SHOW CREATE TABLE`。不得根据 `dt`、`created_at` 等字段名猜测分区字段
- **每张源表必须独立过滤** — Dataset SQL 中 JOIN、UNION、CTE 涉及的每张物理源表都要使用各自的分区字段限制扫描；只在最外层过滤或添加 `LIMIT` 不能替代分区过滤
- **过滤必须进入实际查询** — 分区条件必须写入 Dataset SQL 或 Chart 保存的过滤条件，并使用有上下界的有限时间范围；创建后通过 Chart data 查询验证条件实际生效
- **分区字段因表而异** — 必须使用 DDL / 元数据确认出的实际分区字段，不得把一个表的字段照搬给其他表。例如：若某埋点事件表的 DDL 显示按 `days(dvce_created_tstamp)` 分区，则使用 `dvce_created_tstamp >= TIMESTAMP '2026-07-22 00:00:00' AND dvce_created_tstamp < TIMESTAMP '2026-07-29 00:00:00'` 限制扫描；其他表按各自的真实分区字段过滤
- **无法确认或无法过滤时停止创建** — 分区字段、底层源表或有效过滤条件尚未确认时，不得继续创建 Chart / Dashboard

### DataHub 联动

数据资产元数据（表/列/血缘）优先通过 DataHub / Data Search MCP 查询，Superset 中的 Dashboard/Chart 在 DataHub 中有元数据注册。

### 数仓表名溯源（业务库 ↔ 数仓映射）

用户问"某业务库表在数仓叫什么 / 数仓完整表名是什么"，或"这个 `ods_xxx` 数仓表是从哪个业务库源表同步来的"——这类业务库表 ↔ 数仓表的映射不在 Superset，而在 NocoDB `data_sync.jobs`（每条入仓同步任务都记录了 `source_table_name` + 源连接 → `dest_database.dest_table`）。用 **nocodb skill 的「查询数仓映射关系（业务库表 ↔ 数仓表，只读）」** 章节做双向查询，拿到数仓全名后再回 Superset SQL Lab / Dataset 查。

> **本节依赖 nocodb skill。** 若它不在你当前可用的 skills 列表里（用户本地没装），先引导用户安装本项目（同一仓库）的 nocodb skill 再继续：Claude Code 用 `/plugin install addx@addx`（没加过 marketplace 时，按本仓库 README「快速开始」先加 `addx` marketplace）；其它平台用 `npx skills add <本仓库 git 地址> --skill nocodb`（git 地址见本仓库 README）。装好并 reload 后再走上面的查询。

> 该查询需账号有 NocoDB `data_sync` 项目权限。没有就联系**数据团队同学邀请你进项目**，再到 https://nocodb.addx.live （头像 → API Tokens）自己生成 token；数据同学只负责拉你进项目，不代查。

### Dashboard 创建红线

- **Dashboard 创建后必须验证** — 不能只检查 API 返回 200，必须验证 chart 数据能正常返回（见"Dashboard 创建与验证"章节的验证清单）
- **viz_type 不能硬编码** — 每个 Superset 实例注册的 viz_type 不同，必须先从已有的正常 Dashboard 获取白名单
- **position_json 的 UUID 必须匹配真实 chart** — 从 GET /api/v1/chart/{id} 获取 uuid 字段，禁止伪造

### 常见工作流

- **Dashboard 浏览：** 列出 Dashboard → 按标题/标签筛选 → 查看详情获取 Chart 列表 → 逐个查看 Chart 数据源
- **即席查询：** 列出数据库连接 → 选择目标 database_id → 执行 SQL → 查看结果
- **数据资产审计：** 列出 Dataset → 检查 columns 和 metrics → 确认数据源连接状态

### Dashboard 创建与验证

#### 创建流程（五步）

```
Step 1: 发现已注册的 viz_type
  GET /api/v1/dashboard/{known_working_id}/charts
  → 提取所有 viz_type 值 → 构建白名单
  ⚠ 每个 Superset 实例注册的 viz_type 不同，不能硬编码。
    常见可用: table, big_number_total, echarts_timeseries_bar
    常见不可用: dist_bar, pie, echarts_bar, echarts_pie, echarts_funnel

Step 2: 确认分区并创建 Dataset
  → 确认每张源表的真实分区字段；不确定时执行 SHOW CREATE TABLE/VIEW
  → Dataset SQL 为每张源表添加各自的分区过滤，不得照搬其他表的字段
  POST /api/v1/dataset/ with database, schema, table_name

Step 3: 创建 Chart（只使用白名单内的 viz_type）
  POST /api/v1/chart/ with params 必须包含 datasource 字段
  params JSON 示例: {"datasource": "{dataset_id}__table", ...}

Step 4: 创建 Dashboard 并设置 position_json
  POST /api/v1/dashboard/
  ⚠ position_json 中的 chart UUID 必须匹配真实值：
    先 GET /api/v1/chart/{id} 获取 uuid 字段，再填入 position_json

Step 5: 验证（必须执行，不可跳过）
  见下方"验证清单"
```

#### 验证清单（每次创建 Dashboard 后必须执行）

```python
# 创建 Dashboard 后必须逐项验证，不能仅凭 API 返回 200 就认为成功
CHECKS = [
    "all physical source tables have confirmed partition fields",      # 已确认每张物理源表的分区字段
    "dataset SQL or chart filters use every source partition field",   # 每张源表均有分区过滤
    "partition predicates match SHOW CREATE TABLE/VIEW metadata",       # 过滤字段与真实 DDL / 元数据一致
    "viz_type in whitelist",                                      # viz_type 在白名单内
    "params contains datasource: '{id}__table'",                  # params 包含 datasource
    "query_context is not null",                                  # query_context 非空
    "POST /api/v1/chart/data with query_context returns data",    # chart 能返回数据
    "no duplicate labels (x_axis ∩ groupby = ∅)",                 # 无重复标签
    "position_json chart UUIDs match GET /api/v1/chart/{id} UUIDs", # UUID 匹配
    "GET /api/v1/dashboard/{id}/charts returns expected count",   # chart 数量一致
]
```

#### 常见陷阱

| 问题 | 症状 | 修复 |
|------|------|------|
| 未注册的 viz_type | "Item with key X is not registered" | 先从已有正常 Dashboard 获取白名单 |
| params 缺少 datasource | Chart 显示空白 | 在 params 中添加 `"datasource": "{id}__table"` |
| position_json 使用伪造 UUID | "no chart definition" | 通过 GET /api/v1/chart/{id} 获取真实 UUID |
| x_axis + groupby 重叠 | "Duplicate column/metric labels" | echarts_timeseries_bar 使用 `groupby: []`，仅设 x_axis |
| 缺少 query_context | Chart 不渲染数据 | 显式设置 query_context，或在 Explore 界面打开并保存 |
| echarts_bar 与 echarts_timeseries_bar 混淆 | viz_type 注册错误 | 使用 `echarts_timeseries_bar`，`echarts_bar` 通常未注册 |

#### 验证 API 模式

```bash
# 创建 Dashboard 后执行以下验证循环：

# 1. 检查 viz_type 是否在白名单内
for chart_id in $CHART_IDS; do
  viz=$(curl -s -H "Authorization: Bearer $SUPERSET_TOKEN" \
    "$SUPERSET_URL/api/v1/chart/$chart_id" | jq -r '.result.viz_type')
  # assert viz in WHITELIST
done

# 2. 检查 chart 数据能否正常返回
for chart_id in $CHART_IDS; do
  qc=$(curl -s -H "Authorization: Bearer $SUPERSET_TOKEN" \
    "$SUPERSET_URL/api/v1/chart/$chart_id" | jq '.result.query_context')
  # query_context 不能为 null
  result=$(curl -s -X POST -H "Authorization: Bearer $SUPERSET_TOKEN" \
    -H "Content-Type: application/json" \
    "$SUPERSET_URL/api/v1/chart/data" -d "$qc")
  # assert result[0].data 非空
done

# 3. 检查 Dashboard 包含所有预期 chart
charts=$(curl -s -H "Authorization: Bearer $SUPERSET_TOKEN" \
  "$SUPERSET_URL/api/v1/dashboard/$DASH_ID/charts" | jq '.result | length')
# assert charts == expected_count
```

## Examples

### Bad

```bash
# 无 LIMIT 全表扫描 — 超时 + 高额查询成本
{"database_id": 1, "sql": "SELECT * FROM huge_events_table"}

# 假设 DDL 显示该表示例按 days(dvce_created_tstamp) 分区：仅过滤业务时间字段无法保证分区裁剪
{"database_id": 1, "sql": "SELECT event_name, collector_tstamp FROM analytics.dwd_base_event_hi WHERE collector_tstamp >= TIMESTAMP '2026-07-22 00:00:00' LIMIT 500"}

# DDL 操作 — 被安全策略禁止
{"database_id": 1, "sql": "DROP TABLE user_data"}
```

### Good

```bash
# 示例：DDL 已确认该表按 days(dvce_created_tstamp) 分区，因此使用有界 dvce_created_tstamp 过滤和 LIMIT
{"database_id": 1, "sql": "SELECT event_name, collector_tstamp FROM analytics.dwd_base_event_hi WHERE dvce_created_tstamp >= TIMESTAMP '2026-07-22 00:00:00' AND dvce_created_tstamp < TIMESTAMP '2026-07-29 00:00:00' LIMIT 500", "schema": "analytics"}
```

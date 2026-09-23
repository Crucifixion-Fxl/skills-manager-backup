# dapp SLA 配置 API 参考

## 概述

dapp（https://dapp.addx.live）是 SLA 指标配置平台。底层存储为 **NocoDB**，可通过以下方式操作：
1. **后端 API（推荐）** — 支持完整 CRUD，可自动化创建/修改/查询 SLA 指标
2. **NocoDB REST API（只读查询备用）** — 仅限查询，不可用于新增/修改
3. **Web UI** — 手动操作（飞书 OAuth 登录）

## 架构

```
Superset (图表) → NocoDB (配置存储) → Grafana (监控告警)
                      ↑
              后端 API (JWT Bearer) ← 推荐，完整 CRUD
              NocoDB REST API (xc-token) ← 只读查询备用
              Web UI (飞书 OAuth)
```

## 方式一：后端 API（推荐，支持完整 CRUD）

### 认证

- **Header**: `Authorization: Bearer <jwt_token>`
- **基地址**: `https://dapp-api.addx.live`

**获取 JWT Token（会话 Token，推荐）：**

```bash
POST /api/v1/auth/token/session
Content-Type: application/json

{"username": "user@company.com", "expire_hours": 12}

# 返回: {"data": "eyJ..."}
```

- `expire_hours` 可选，默认 12 小时
- Token 过期后需重新获取

**获取 JWT Token（长期 Token）：**

```bash
POST /api/v1/auth/token/generate
Content-Type: application/json

{"username": "user@company.com", "token_type": "user"}

# 返回: {"data": "eyJ..."}
```

- 长期 Token 过期时间 2099 年，适合配置到环境变量 `DAPP_TOKEN` 中

### 端点

#### 创建指标

```bash
POST /api/v1/sla_metric/create
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
  "name": "sla_daily_order_count",
  "title": "每日VIP订单增量",
  "time_grain": "day",
  "metric_config": "{JSON字符串}",
  "threshold_config": "{JSON字符串}",
  "biz_domain_id": 2,
  "biz_process_id": 3,
  "app_domain_id": 1,
  "metric_app_monitor_config": "{JSON字符串，可选}",
  "upstream_app_monitor_configs": "[]",
  "deps": "[]"
}

# 返回: {"data": {"Id": 123, "name": "sla_daily_order_count"}, "message": "创建成功"}
```

**必填字段：** `name`、`title`、`time_grain`、`metric_config`、`threshold_config`、`biz_domain_id`、`biz_process_id`

**校验规则：**
- `name` 必须唯一
- `time_grain` 可选值：`hour`、`day`、`week`
- `threshold_config` 必须包含合法的 `op`（`lt`/`gt`）和至少一个告警等级（`warning`/`critical`）
- 所有 JSON 字段必须是合法的 JSON 字符串

**创建时默认值：**
- `owners` — 自动设为当前 Token 用户
- `subscribers` — 初始为 `[]`
- `feishu_group_chats` — 初始为 null（使用业务域默认群）
- `metric_source` — 固定为 `"superset"`

#### 更新指标

```bash
POST /api/v1/sla_metric/update
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
  "name": "sla_daily_order_count",
  "title": "新标题",
  "threshold_config": "{新的阈值JSON字符串}"
}

# 返回: {"data": {"Id": 123, "name": "sla_daily_order_count"}, "message": "更新成功"}
```

- `name` 必填，用于定位目标记录
- 其余字段均为可选，仅传需要更新的字段
- **权限检查：** 只有 `owners` 中的用户可以修改
- `feishu_group_chats` 格式为 `Dict[str, Dict]`，key 是 chat_id，value 含 `chat_id` 和 `chat_name`。示例：`"{\"oc_xxx\":{\"chat_id\":\"oc_xxx\",\"chat_name\":\"群名\"}}"` 。传 `"null"` 字符串表示恢复为业务域默认群

**可更新字段：** `title`、`time_grain`、`metric_config`、`threshold_config`、`metric_app_monitor_config`、`upstream_app_monitor_configs`、`app_domain_id`、`biz_domain_id`、`biz_process_id`、`deps`、`feishu_group_chats`

#### 查询指标详情

```bash
GET /api/v1/sla_metric/get?name={metric_name}
Authorization: Bearer <jwt_token>
```

返回**完整字段**（含 `threshold_config`、`metric_config`、`metric_app_monitor_config`、`upstream_app_monitor_configs`、`subscribers`、`feishu_group_chats`）。

#### 列表查询

```bash
POST /api/v1/sla_metric/list
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
  "name": "keyword",
  "page_index": 0,
  "page_size": 20,
  "biz_domain_id": 2,
  "app_domain_id": 1
}
```

返回**摘要字段**（不含配置字段），仅包含：`name`、`title`、`metric_source`、`time_grain`、`deps`、`owners`、`biz_domain_id`、`app_domain_id`、`biz_process_id`、`created_at`、`updated_at`。如需完整配置请用 `GET /get`。

**可选过滤字段：** `name`（模糊）、`title`（模糊）、`time_grain`、`biz_domain_id`、`app_domain_id`、`created_at`、`updated_at`

#### 业务域列表

```bash
GET /api/v1/sla_metric/biz_domains
Authorization: Bearer <jwt_token>

# 返回: {"data": [{"id": 1, "name": "域名称"}, ...]}
```

#### 应用域列表

```bash
GET /api/v1/sla_metric/app_domains
Authorization: Bearer <jwt_token>

# 返回: {"data": [{"id": 1, "name": "域名称"}, ...]}
```

#### 业务过程列表

```bash
GET /api/v1/sla_metric/biz_processes?biz_domain_id={id}
Authorization: Bearer <jwt_token>

# 返回: {"data": [{"id": 1, "name": "过程名称", "domain_id": 2}, ...]}
```

- `biz_domain_id` 可选，用于按业务域过滤

#### 查询可用飞书群

```bash
GET /api/v1/sla_metric/feishu_group_chats
Authorization: Bearer <jwt_token>

# 返回已添加「积加自助数据平台」机器人的飞书群列表
# {"data": [{"chat_id": "oc_xxx", "name": "群名称"}, ...]}
```

#### 查询告警事件

```bash
POST /api/v1/sla_metric/events
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
  "metric_name": "metric_key",
  "since_at": "2026-03-01 00:00:00",
  "before_at": "2026-03-17 00:00:00",
  "sort_field": "-metric_at",
  "page_index": 0,
  "page_size": 20
}
```

**可选过滤字段：** `metric_name`、`since_at`、`before_at`、`biz_domain_id`、`app_domain_id`、`firing`（0/1）、`created_at`、`updated_at`

**排序：** `sort_field` 支持 `metric_at`、`created_at`、`updated_at`，前缀 `-` 表示倒序（如 `-metric_at`）

**时间参数格式：** `since_at`/`before_at` 支持三种格式：
- Unix 时间戳（int/float）：`1709251200`
- 10 位数字字符串：`"1709251200"`
- 日期时间字符串：`"2026-03-01 00:00:00"`

**注意：** 至少需要携带一个查询条件。

### 常见错误

| 错误消息 | 原因 | 解决 |
|---------|------|------|
| `name 不能为空` | 创建/更新缺少 name 字段 | 提供 name |
| `SLA指标已存在: {name}` | name 重复 | 换一个唯一 name |
| `SLA指标不存在: {name}` | 更新时 name 未匹配到记录 | 检查 name 拼写 |
| `time_grain 不合法` | 传了非法的 time_grain | 可选值：`hour`/`day`/`week` |
| `无权修改此SLA指标` | 当前用户不在 owners 列表中 | 联系 owners 中的用户操作 |
| `{field} 格式错误：必须是合法的JSON字符串` | JSON 字段格式不合法 | 检查 JSON 序列化 |
| `threshold_config.op 必须为 lt 或 gt` | op 值非法 | `lt`（跌破）或 `gt`（突破） |
| `threshold_config.thresholds 不能为空` | 未配置任何告警等级 | 至少配置 warning 或 critical |
| `未提供任何需要更新的字段` | 更新请求只有 name，无其他字段 | 添加要更新的字段 |
| `请在请求参数中至少携带一个查询条件` | events 查询无任何过滤条件 | 添加至少一个过滤条件 |
| `解析时间失败` | since_at/before_at 格式无法识别 | 使用支持的时间格式 |

### SLA 指标数据模型

完整的创建/更新 payload 结构：

```json
{
  "name": "sla_daily_order_count",
  "title": "每日VIP订单增量",
  "time_grain": "day",

  "metric_config": "{JSON string, see below}",
  "threshold_config": "{JSON string, see below}",
  "metric_app_monitor_config": "{JSON string, see below}",
  "upstream_app_monitor_configs": "[]",

  "biz_domain_id": 2,
  "biz_process_id": 3,
  "app_domain_id": 1,
  "deps": "[\"dagster_asset_name\"]"
}
```

**重要：所有复杂字段必须 JSON 序列化为字符串后传递。**

#### metric_config（Superset 指标配置）

```json
{
  "metric_source": "superset",
  "slice_id": 5150,
  "form_data_key": null,
  "slice_name": "SLA-每日订单增量监控",
  "query_index": 0,
  "metric_id": 0,
  "metric_name": "daily_order_count",
  "filter_mode": "snapshot",
  "filter_snapshot": [
    {"expressionType": "SIMPLE", "subject": "dt", "operator": "TEMPORAL_RANGE", "comparator": "No filter", "clause": "WHERE"}
  ],
  "form_filters": [],
  "custom_filters": false,
  "snapshot_source": "saved"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `slice_id` | int | Superset 图表 ID |
| `form_data_key` | string/null | 过滤条件快照 key |
| `metric_name` | string | 指标名称 |
| `filter_mode` | string | `snapshot`（固定快照）或 `auto`（跟随图表） |
| `filter_snapshot` | array | 过滤条件快照。**必须包含时间列的 TEMPORAL_RANGE 过滤器**（`subject` 为图表的时间列名，如 `dt`） |
| `metric_id` | int | 指标 ID。自定义指标取 `column.id`（通常为 0），预定义指标取 `metric.id` |
| `query_index` | int | 查询索引（混合图表用，默认 0） |

> **重要：** `metric_name` 对应 Superset 图表中 metrics 的 `label` 字段。Superset 图表的 `sqlExpression` 必须包含聚合函数（如 `MAX(match_rate)`），但 `metric_name` / `label` 使用简短名称（如 `match_rate`）即可。

#### threshold_config（阈值配置）

```json
{
  "compare_object": "value",
  "op": "lt",
  "thresholds": {
    "warning": { "threshold_value": 8222 },
    "critical": { "threshold_value": 6000 }
  },
  "value_if_no_data": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `compare_object` | string | `value`（指标值）/ `mom`（环比）/ `yoy`（同比） |
| `op` | string | `lt`（跌破）/ `gt`（突破） |
| `thresholds` | object | 按告警等级设置阈值。key: `warning` / `critical` |
| `value_if_no_data` | float/null | 无数据时默认值 |

**告警等级：**
- `warning` — 普通告警（对应 WARN）
- `critical` — 严重告警（对应 P3 及以上）

#### metric_app_monitor_config（应用版本监控）

```json
{
  "enable_version_monitor": false,
  "app_name": null,
  "app_id": 0,
  "app_version_field": "__null__",
  "monitor_version_list": []
}
```

不启用版本监控时使用以上默认值。

#### time_grain（时间粒度）

| 值 | 含义 | Grafana 时间范围 |
|----|------|-----------------|
| `hour` | 小时 | now-25h ~ now-1h |
| `day` | 天 | now-8d ~ now-1d |
| `week` | 周 | now-8w ~ now-1w |

## 方式二：NocoDB REST API（只读查询备用）

> **仅限只读查询**，新增/修改必须使用后端 API。认证 Header: `xc-token: <token>`，环境变量 `NOCODB_ADDR`（默认 `https://nocodb.addx.live`）、`NOCODB_TOKEN`。

**可用表：** `BI.sla_metric`（生产）、`BI.sla_metric_dev`（开发）、`BI.sla_metric_query_event`（告警事件）

```bash
# 示例：模糊查询指标
curl -s "https://nocodb.addx.live/api/v1/db/data/v1/BI/sla_metric?where=(name,like,%25keyword%25)&limit=20" \
  -H "xc-token: $NOCODB_TOKEN"
```

## 方式三：Web UI（飞书 OAuth）

dapp 使用飞书 OAuth 授权登录，适用于手动操作场景：

| 操作 | 入口路径 |
|------|----------|
| SLA 指标列表 | dapp 首页 → SLA 指标列表 |
| 新增 SLA 指标 | SLA 指标列表页 → 左上角【新增SLA指标】 |
| 编辑 SLA 指标 | 详情页 → 右上角【操作】→【编辑SLA指标】 |
| 搜索 | 列表页 → 搜索框（关键字/业务域/应用域） |
| 构建 Grafana 资源 | 详情页 → 构建按钮（幂等操作）。链接：`https://dapp.addx.live/sla_metric?sub=metric&id={Id}&action=view` |

## 源码参考

| 文件 | 说明 |
|------|------|
| `/project/dbt/utils/sla/metric.py` | SLAMetricModel — 数据模型与 Grafana 集成 |
| `/project/dbt/utils/sla/config/metric/` | SupersetMetricConfig — 指标配置 |
| `/project/dbt/utils/sla/config/threshold/` | 阈值配置模型 |
| `/project/dbt/utils/_nocodb/client.py` | NocoDB 客户端 |
| `/project/dbt/a4x_backend/api/sla_metric.py` | 后端 API 端点 |
| `/project/dbt/a4x_bi/pages/sla_metric.py` | Web UI 实现 |

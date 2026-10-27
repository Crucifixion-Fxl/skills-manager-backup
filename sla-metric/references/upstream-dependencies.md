# SLA 上游数据资产依赖发现与校验

## 适用范围与原因

新建 SLA，以及修改指标来源或查询配置时，必须在提交配置前完成本流程。`deps` 表示 Dagster 数据资产依赖，用于等待上游数据就绪；它与 `upstream_app_monitor_configs`（上游应用版本监控）是两个不同字段。

dapp Web 保存前会自动匹配依赖并检查时间窗口；当前已核实的 dapp API `/create`、`/update` 只校验 `deps` 是合法 JSON，不会发现资产，也不验证资产存在性。遗漏或默认传 `"[]"` 会丢失这一步。**不得假设 API 或构建 Grafana 会补齐依赖。**

以下查询均为只读。Superset、dapp、Dagster 必须对应同一区域及目标环境；Dagster 地址和认证方式按 `dagster` skill 与实际配置核实，不凭域名后缀猜测。完成权限验证后再查询，凭据不得写入文件或日志。

## 1. 固定当前指标的实际查询

1. 读取图表及数据集元数据，记录数据库连接、catalog/schema、数据集 SQL 或物理表、时间列。
2. 按 `query_index` 选中目标查询，再按 `metric_id` 选中实际 metric；预定义 metric 使用名称，自定义 metric 使用完整查询结构，不能仅用显示 label 替代。
3. `filter_mode=snapshot` 时采用当前 `filter_snapshot`；否则采用选中查询的当前图表过滤器。存在 `form_data_key` 时必须读取对应快照，不能退回保存版本。
4. 使用 SLA 的 `time_grain`，映射为 `hour=PT1H`、`day=P1D`、`week=P1W`；保留实际时间列、普通过滤器及 SQL WHERE/HAVING 条件。
5. 请求生成该查询的最终 SQL。不能仅凭图表名、数据集名或未展开的虚拟数据集 SQL 判定依赖。

真实元数据请求：

```http
GET /api/v1/explore/?slice_id={slice_id}&form_data_key={optional_key}
GET /api/v1/dataset/{dataset_id}
```

省略未使用的 `form_data_key`。查询结果缺少图表、数据集、metric 或快照时停止，先解决配置问题。

### Superset SQL 预览请求

复用已登录 Superset 会话及其认证、CSRF 信息。以下是单个日粒度指标的结构示例，`123`、`dt`、metric、过滤器和日期须替换为上述实际配置，不能照抄示例数据：

```http
POST /api/v1/chart/data?form_data=%7B%22slice_id%22%3A0%7D
Content-Type: application/json
```

```json
{
  "datasource": {"id": 123, "type": "table"},
  "force": false,
  "queries": [{
    "filters": [{
      "col": "dt",
      "op": "TEMPORAL_RANGE",
      "val": "2026-10-04T00:00:00 : 2026-10-11T00:00:00"
    }],
    "extras": {"time_grain_sqla": "P1D", "having": "", "where": ""},
    "columns": [{
      "timeGrain": "P1D",
      "columnType": "BASE_AXIS",
      "sqlExpression": "dt",
      "label": "time",
      "expressionType": "SQL"
    }],
    "metrics": ["actual_saved_metric_name"],
    "row_limit": 10000,
    "order_desc": true
  }],
  "result_format": "json",
  "result_type": "query"
}
```

`slice_id=0` 是当前 Web 客户端自行构造查询时的实际参数；真正的数据源和指标由 body 提供。`query_index` 用于选择要构造的查询，本例 body 只含该查询，因此读取 `result[0].query`，不能再用原 `query_index` 索引它。

确认响应成功且 SQL 非空。`202` 异步响应须通过已验证的客户端等待最终结果；超时、错误或无 SQL 均视为发现未完成，不能推导为空依赖。最终 SQL 仅用于分析，不执行 DDL/DML。

## 2. 从物理来源匹配 Dagster 资产

使用与数据库相符的 SQL 方言解析最终 SQL；当前 Web 实现使用 `sqlglot` 的 Presto 方言。展开子查询，区分物理表与 CTE 引用、别名，不能把 CTE 名当成数仓资产。视图须有可核验的资产映射或底层来源依据；动态 SQL、无法解析的来源不能静默略过。

向目标 Dagster 的 `/graphql` 发送以下只读请求，读取当前资产定义：

```json
{
  "query": "query { assetNodes { assetKey { path } isMaterializable repository { name location { name } } } }"
}
```

逐表核验：

- 仅将 `isMaterializable=true` 的节点作为可调度依赖候选；完整 key 为 `assetKey.path` 按 `/` 连接，如 `analytics/dws_example_di`。
- Web 当前按物理表名末段匹配 key 末段；这只能生成候选。必须结合 catalog/schema、区域、环境、repository/location 及已核实的模型映射排除同名歧义。
- 不能把 SQL 的 `schema.table` 直接替换成 `schema/table` 后当成真实 key；也不能只存裸表名或自行添加所有间接祖先。
- 每个物理来源都要有匹配结果或明确的排除依据。无匹配、多个无法区分的候选、跨区域/环境候选均停止提交，先澄清映射。
- 记录“物理来源 → 完整资产 key → 匹配依据”，对最终 key 去重。HTTP 成功但含 GraphQL `errors`、资产列表不完整或无权读取，不算发现完成。

### 小时粒度的现有例外

当前 Web 只在 `time_grain=hour` 时排除以下三个完整 key：

- `user/dim_user_base_info_df`
- `device/dim_device_base_df`
- `device/dim_device_model_info_df`

在配置摘要中列出排除项及此规则的依据；日、周粒度不应用该排除。不得推广为忽略全部维表或全部慢依赖，规则变化需重新核实实际实现。

## 3. 验证依赖时间窗口

对每个保留的候选读取分区定义，`variables.key.path` 使用真实 asset key 的完整路径：

```json
{
  "query": "query($key: AssetKeyInput!) { assetNodeOrError(assetKey: $key) { __typename ... on AssetNode { assetKey { path } partitionKeysByDimension { name type partitionKeys } } } }",
  "variables": {"key": {"path": ["analytics", "dws_example_di"]}}
}
```

当前 Web 的 `time_window` 是客户端计算值，**不是 GraphQL 字段**：读取相邻时间分区，按 UTC 解析 `%Y-%m-%d` 或 `%Y-%m-%d-%H:%M`，通过秒差计算窗口。单一时间维度且有至少两个可解析分区时，按此方式核验其窗口及稳定性。

| SLA 粒度 | 窗口上限 |
|----------|----------|
| `hour` | 3600 秒 |
| `day` | 86400 秒 |
| `week` | 604800 秒 |

所有保留依赖的窗口必须不大于 SLA 窗口。不能按 `_hi`、`_di` 后缀或一次物化时间猜测频率；大于上限时需调整粒度或解决数据来源问题，禁止靠删依赖绕过。

出现 GraphQL 错误、非 `AssetNode`、空分区、少于两个分区、非时间分区、多维分区或窗口无法明确判定时，停止自动校验并说明缺口。应补充定义/调度证据，不能将“未知”当作兼容或空依赖。

## 4. 构造、确认与保存

`deps` 是**完整资产 key 数组序列化后的 JSON 字符串**，例如：

```json
{"deps": "[\"analytics/dws_example_di\",\"vip/dwd_order_di\"]"}
```

提交前展示区域/环境、物理来源与匹配依据、最终 `deps`、排除项和窗口兼容性，与指标来源、阈值等一并确认。数组内只能有已核实的非空 key 字符串。

**禁止失败时用 `"[]"` 兜底。** 仅当所有来源均已核实，确实没有适用的 Dagster 依赖时，才能解释原因、列明排除依据，并在用户确认后显式提交 `"[]"`；“没匹配到”不构成无依赖证据。

### 修改已有 SLA

- 先 `GET /api/v1/sla_metric/get?name={name}` 回读完整配置及已有 `deps`。
- 修改 `slice_id`、`query_index`、`metric_id`、数据集/SQL、过滤条件或快照、`filter_mode`、`form_data_key`、`time_grain` 时重新发现并校验，连同新配置提交 `deps`。
- 将新发现集合与旧集合比较，展示新增、删除和保留项。已有手工依赖不能被自动覆盖或删除；先核实用途、验证其存在性和窗口，再确认最终差异。
- 仅修改阈值、标题、分类或通知配置时保留原 `deps`，payload 不传该字段；不能因未执行发现而把它重置为空。

### 写后回读

创建或更新成功后，用相同区域的 `GET /api/v1/sla_metric/get?name={name}` 回读。将 `deps` 解码为字符串数组（已为数组则直接校验），去重排序后与确认集合比较，并核对指标来源与粒度。

回读不一致、字段格式异常或失败时不能宣布依赖配置完成。提交响应未知时先按唯一 `name` 查询确认实际状态，禁止盲目重复创建。配置保存、Grafana 构建、Dagster 定义加载及实际调度是不同状态，回读成功只证明配置已保存。

## 源码依据与验证边界

以上契约依据 DATA/dbt 本地源码固定 SHA `4fba980c09e64f0dc0e32b87031cd4bb01923f29`：

- `a4x_bi/pages/sla_metric.py`：`_auto_match_deps`（1568–1621）、保存前调用（1740–1743）。
- `a4x_backend/api/sla_metric.py`：JSON 校验与原样保存（294–326）；`utils/sla/metric.py`：`deps` 模型（62）。
- `utils/_superset/client.py`：Explore（373–384）、Chart Data（899–929）、查询 body 与 SQL 返回（1011–1089）。
- `utils/_dagster/asset_analyzer.py`：完整 key 和可物化节点（225–276）、时间窗口计算（551–582）。

这些是源码依据（source-only），未证明目标环境已部署该 SHA，也不是在线验收记录。执行时须核实实际接口返回与当前资产定义；契约不符时先定位差异，不虚构接口或跳过校验。

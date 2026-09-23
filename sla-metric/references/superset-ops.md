# Superset 指标图表操作参考

## 概述

SLA 指标的数据来源是 Superset 图表。本文档说明如何通过 Superset MCP 工具完成指标图表的查询、创建和验证。

## 认证

Superset 认证通过 MCP 工具完成，要求用户提供凭据（用户名/密码）后调用登录接口获取 JWT Token。Token 有效期约 1 小时，过期后重新获取。

## 可用的 MCP 工具

| 工具 | 用途 |
|------|------|
| `mcp__superset-mcp__list_charts` | 列出图表，按名称/标签筛选 |
| `mcp__superset-mcp__get_current_chart_config` | 获取图表当前配置 |
| `mcp__superset-mcp__get_chart_filters` | 获取图表过滤条件 |
| `mcp__superset-mcp__list_datasets` | 列出数据集 |
| `mcp__superset-mcp__get_dataset` | 获取数据集详情 |
| `mcp__superset-mcp__get_dataset_columns` | 获取数据集字段列表 |
| `mcp__superset-mcp__execute_sql` | 执行 SQL 查询验证数据 |
| `mcp__superset-mcp__create_chart` | 创建新图表 |
| `mcp__superset-mcp__create_dataset` | 创建新数据集 |
| `mcp__superset-mcp__list_databases` | 列出可用数据库连接 |

## SLA 图表要求

- **图表类型：** 必须为折线图（line chart）— dapp 仅支持解析 line 类型，使用其他类型（bar、pie、table 等）会导致 dapp 无法读取指标数据
- **命名规范：** 通过本 skill 创建的图表，名称必须以 `sla_metric_` 为前缀（如 `sla_metric_daily_order_count`）
- **时间字段：** 必须有时间字段，且 `adhoc_filters` 必须包含该时间列的 TEMPORAL_RANGE 过滤器
- **单一指标：** 查询结果不应包含除时间维度外的其他维度（SLA 不支持多维度监控）
- **时间粒度限制：** 时间字段为 `date` 类型时，不支持小时粒度
- **metrics 必须使用聚合函数：**
  - `expressionType: 'SQL'` 时，`sqlExpression` 必须包含聚合函数，如 `MAX(match_rate)`、`AVG(value)`。裸列名（如 `match_rate`）会导致 Superset 生成的 SQL 在 GROUP BY 时报 `EXPRESSION_NOT_AGGREGATE` 错误
  - `expressionType: 'SIMPLE'` 时，必须指定 `aggregate` 字段（如 `AVG`、`MAX`、`SUM`）
  - 对于每日只有一行数据的指标，使用 `MAX()` 即可
- **SQL 数据集必须返回多日历史数据：**
  - 禁止在数据集 SQL 中硬编码 `WHERE dt = current_date` 或 `date_add('day', -1, current_date) as dt` 等单日逻辑
  - 数据集应返回至少 15 天的数据，通过 TEMPORAL_RANGE 过滤器控制展示范围
  - 正确示例：`WHERE dt >= date_add('day', -15, current_date)`
  - 错误示例：`WHERE dt = current_date`（只有 1 个点，无法显示趋势线）

## 常用操作流程

### 1. 查找现有指标图表

```
步骤：
1. 使用 list_charts 按关键字搜索目标图表
2. 使用 get_current_chart_config 获取图表配置详情
3. 使用 get_chart_filters 查看已有过滤条件
4. 确认图表链接格式：https://superset-us.addx.live/explore/?slice_id={chart_id}
```

### 2. 创建新指标图表

```
步骤：
1. 使用 list_databases 确认目标数据库连接
2. 使用 list_datasets 查找或 create_dataset 创建数据集
3. 使用 get_dataset_columns 确认字段列表（需包含时间字段）
4. 使用 create_chart 创建图表，配置指标和过滤条件
5. 获取图表链接，供 dapp SLA 配置使用
```

### 3. 验证指标数据

```
步骤：
1. 使用 execute_sql 执行查询验证指标值
2. 对比查询结果与 Superset 图表展示是否一致
```

## 图表链接格式

dapp 配置 SLA 指标时需要粘贴 Superset 图表链接，格式如下：

```
https://superset-us.addx.live/explore/?slice_id={chart_id}&form_data_key={key}
```

- `slice_id`: 图表 ID
- `form_data_key`: 过滤条件快照 key（用于自定义过滤条件场景）

## 注意事项

- 如需自定义过滤条件但不修改图表已保存状态，使用 "Update chart" 后通过链接中的 `form_data_key` 快照

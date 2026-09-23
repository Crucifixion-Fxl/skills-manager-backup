# 圈人条件

将用户要求转换为 `validate_audience_query` 和 `create_audience_query` 接受的结构化 `criteria`。

1. 读取已认证 Project 的 `get_query_capabilities`，使用响应中的 `registry_version`、逻辑关系、字段、值类型和操作符。
2. 将每项条件映射到已公布的 `relation_id`、`field_id` 和操作符，使用契约支持的分组组合条件。
3. 验证完整条件。若某项要求不受支持，应说明缺少的语义路径，不得静默丢弃。
4. 使用 `name`、已验证的 `criteria` 和 `idempotency_key` 创建圈人计划。

根节点包含 `schema_version=audience-criteria-v1`、在线返回的 `registry_version` 和 `where` 表达式。
字段谓词包含 `kind=field`、`relation_id`、`field_id`、`operator`，以及需要时携带的对应类型值。
聚合谓词包含 `kind=aggregate`、在线边的 `edge_id`、该边公布的 `aggregate_id`、`operator`，
以及需要时的 `value` 或 `values`；可选 `where` 仅用于过滤该边子表的行。
先从同一已认证 Project 的在线边读取指标的 `function`、`field_id`、`value_type` 和 `operators`，
再构造请求；不自行拼接函数、物理列或表达式，不从示例臆造指标 ID。
关系存在性使用 `kind=relationship`、已公布的 `edge_id`、`quantifier=exists|not_exists` 和必填 `where`。
只支持从 profile 出发的一层关系或聚合；子表 `where` 不能再嵌套关系或聚合。
分组、边、null 和值的精确结构，以已安装原生操作的 schema 或完整仓库 clone 中的 Project OpenAPI 为准。

支持的指标函数为 COUNT/SUM/MIN/MAX/AVG，但某项是否可执行仍由在线能力决定。
COUNT(*) 统计匹配行，COUNT(column) 只统计非空值，空集合都返回 0；
SUM/MIN/MAX/AVG 对空集合或全 NULL 返回 NULL，不自动转成 0。
“没有记录”和“记录中的值为空”不能混为一谈；仅使用指标公布的比较或 `is_null` 操作符。
聚合用于筛选 profile 成员，不会把聚合值作为成员明细输出。

[画像筛选](audience-profile-selection.md)解释业务含义；[Project 圈人查询](project-query.md)说明不支持路径的接入与执行流程。
[已审阅基线](project-query-whitelist.md)是带日期的参考，当前同一 Project 的能力才是权威依据。

用业务语言解释圈定人群，并展示聚合预览人数。Platform 提供范围约束并执行物理查询。
不要请求或构建 SQL、表名、tenant ID 或服务商请求载荷。

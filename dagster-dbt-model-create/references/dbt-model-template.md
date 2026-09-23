# dbt Model Template

本模板不是通用 dbt 教科书模板，而是根据 `DATA/dbt` 项目现有模型抽出的项目内写法。
可以参考目标目录附近或临近业务的真实模型来收敛写法，但不要把某个具体业务模型名带进模板文档。

使用方式：

1. 先读目标目录附近的现有模型；如果样例不足，再参考临近业务模型。
2. 再参考本模板生成第一版。
3. 最后按当前目录的真实风格收敛，不要机械照搬。

## 模板产物

每次新建模型，默认至少产出这两个文件：

```text
dbt_athena/models/<layer>/<domain>/<model_name>.sql
dbt_athena/models/<layer>/<domain>/<model_name>.yml
```

不要只生成 `.sql`，把 `.yml` 留空。

## 调度约定

调度方式同时决定两件事：

1. `config()` 里的调度 tag
2. SQL 里的时间过滤表达式

固定规则如下：

| 调度方式 | `config().tags` | 时间过滤表达式 |
|------|------|------|
| 按小时调度 | `var("schedule_hourly")` | `{{ dh_filter(lag=0, dh_expr="<event_ts_column>") }}` |
| 按天调度 | `var("partition/pastday")` | `{{ dt_filter(lag=0, dt_expr="<event_ts_column>") }}` |

不要把调度 tag 和时间过滤表达式拆开分别判断。

## 物化约定

`materialized` 也要先判断，再写进 `config()`。固定优先级如下：

| 物化方式 | 适用场景 | 约束 |
|------|------|------|
| `view` | 轻量逻辑层、主要做投影或简单聚合、无需落地存储 | 不要再补 `incremental_strategy`、`unique_key`、`partitioned_by` |
| `table` | 需要落地成表，但没有明确增量更新策略 | 可以保留 `group`、`schema`、`tags`；不要保留增量专属配置 |
| `incremental` | 目标目录附近已有同类模式，且唯一键、分区字段、重跑策略都清楚 | 才允许写 `incremental_strategy`、`unique_key`、`partitioned_by`、`incremental_predicates` |

优先原则：

1. 先看同目录邻近模型怎么做。
2. 如果没有足够信息，不要默认写成 `incremental`。
3. `view` 和 `table` 的配置要比 `incremental` 更简。

## `config()` 参数规范

写 `config()` 时，不要整段照抄样例；要逐个参数判断“要不要写、可以写什么、为什么这么写”。

| 参数 | 作用 | 是否必选 | 可选值 / 格式 | 如何选择 |
|------|------|------|------|------|
| `materialized` | 定义模型如何物化给下游使用，是生成视图、全量表还是增量更新表 | 必选 | `view` / `table` / `incremental` | `view` 用于逻辑视图；`table` 表示整表产出；`incremental` 表示增量更新表，也是当前项目最常见模式，但仍要先遵守“物化约定”并参考邻近模型，信息不足时不要直接默认 |
| `group` | 声明模型所属业务组，供项目内组织和治理使用 | 必选 | 仅允许使用目标目录邻近模型已存在的值，如 `analytics` | 先看同目录或同域模型；拿不准就问用户，不要发明新值 |
| `schema` | 定义模型最终落到哪个 schema | 必选 | 仅允许使用目标目录邻近模型已存在的值，如 `analytics` | 优先与同目录模型保持一致；不要因为表名变化就随意改 |
| `tags` | 声明调度方式，供外部调度系统识别 | 必选 | `[var("schedule_hourly")]` / `[var("partition/pastday")]` | 完全由“调度约定”决定；如果邻近模型还有额外 tag，只在语义明确一致时复用 |
| `table_type` | 定义物理表类型 | 条件必选 | `iceberg` | 仅在 `table` 或 `incremental` 这类物理表模型中使用；`view` 不要写 |
| `incremental_strategy` | 定义增量表的更新策略 | 条件必选 | `merge` | 仅当 `materialized='incremental'` 且邻近模型明确采用这套策略时使用；否则省略 |
| `incremental_predicates` | 定义增量重跑或扫描窗口 | 条件必选 | `["dynamic_date_range", "<partition_column>"]` 或邻近模型同类写法 | 仅当 `materialized='incremental'` 时使用；分区列名要跟目标目录现有模型一致 |
| `unique_key` | 定义增量合并时用于去重和更新的唯一键 | 条件必选 | `['<column_name>']` 或多列列表 | 仅当增量合并需要唯一键时写；先由行粒度推出，再与邻近模型比对 |
| `partitioned_by` | 定义物理表的分区列 | 条件必选 | `['<partition_column>']` 或邻近模型已有列表 | 仅在物理表且邻近模型确实分区时写；分区粒度跟调度和现有目录约定一起判断 |
| `on_schema_change` | 定义增量表遇到字段变更时的处理策略 | 条件可选 | `sync_all_columns` | 只在 `incremental` 且邻近模型使用时保留；`view` / `table` 默认不写 |
| `format` | 定义物理表存储格式 | 条件可选 | `parquet` | 仅在物理表模型中保留；`view` 不要写 |

补充规则：

1. `view` 只保留最小必要配置：`materialized`、`group`、`schema`、`tags`。
2. `table` 比 `incremental` 少一层增量语义：不要写 `incremental_strategy`、`incremental_predicates`、`unique_key`、`on_schema_change`。
3. `incremental` 必须同时能解释清楚唯一键、分区字段、重跑窗口和合并策略。
4. 如果某个参数解释不清，就不要先写满一整套“高级配置”。

## 上游引用约定

SQL 中禁止直接出现物理表名，如 `<db>.<table>`。原因是项目不允许跨环境直接查询，权限控制依赖 dbt 引用机制做环境隔离；所有上游输入都必须通过 dbt 引用机制接入：

| 引用方式 | 适用对象 | 规则 |
|------|------|------|
| `ref('<model_name>')` | 已存在的 dbt 模型 | 只用于引用项目内已定义好的模型 |
| `source('<source_name>', '<table_name>')` | 外部表 | 只用于引用外部表，且必须先在 `dbt_athena/models/sources.yml` 中定义 |

补充规则：

1. 如果上游已经是 dbt 模型，必须使用 `ref()`，不要回退成物理表名。
2. 如果上游是外部表，必须先确认或补齐 `dbt_athena/models/sources.yml` 定义，再使用 `source()`。
3. 不要在 SQL 中混用“部分 `ref()` / `source()` + 部分物理表名”的写法，避免绕过环境隔离与权限控制。
4. 如果拿不准某张表是已有模型还是外部表，先问清楚，不要硬写。

### `config()` 推荐写法

#### 1. `view`

```sql
{{ config(
    materialized='view',
    group='<group>',
    schema='<schema>',
    tags=[<schedule_tag_expr>]
) }}
```

#### 2. `table`

```sql
{{ config(
    materialized='table',
    group='<group>',
    schema='<schema>',
    tags=[<schedule_tag_expr>],
    table_type='iceberg',
    partitioned_by=['<partition_column>'],
    format='parquet'
) }}
```

如果邻近 `table` 模型没有分区，就删掉 `partitioned_by`，不要硬补。

#### 3. `incremental`

```sql
{{ config(
    materialized='incremental',
    group='<group>',
    schema='<schema>',
    tags=[<schedule_tag_expr>],
    table_type='iceberg',
    incremental_strategy='merge',
    incremental_predicates=["dynamic_date_range", "<partition_column>"],
    unique_key=['<unique_key>'],
    partitioned_by=['<partition_column>'],
    on_schema_change='sync_all_columns',
    format='parquet'
) }}
```

只有当邻近模型已经稳定使用这套模式时，才直接采用这段写法。

## 文件 1：模型文件模板

```sql
{{ config(
    materialized='<materialized>',
    group='<group>',
    schema='<schema>',
    tags=[<schedule_tag_expr>]
) }}

-- 如果是 `table` 或 `incremental`，先按上面的“config() 推荐写法”补充对应参数；
-- 不要把所有可选参数一次性全抄进来。

-- <model_summary>
-- 一行 = <row_grain>
-- 上游: <upstream_model_1>, <upstream_model_2>
-- 下游消费: <consumer_1>, <consumer_2>

WITH base_data AS (
    SELECT
        <unique_key>,
        CAST(<event_ts_column> AS TIMESTAMP(3)) AS <event_ts_alias>,
        CAST(date_trunc('day', <event_ts_column>) AS TIMESTAMP(3)) AS <partition_column>,
        <business_key_1>,
        <business_key_2>,
        <metric_or_flag_1>,
        <metric_or_flag_2>
    FROM {{ ref('<upstream_model>') }}
    WHERE <time_filter_clause>
),

joined_data AS (
    SELECT
        b.<unique_key>,
        b.<event_ts_alias>,
        b.<partition_column>,
        b.<business_key_1>,
        b.<business_key_2>,
        j.<joined_column_1>,
        j.<joined_column_2>
    FROM base_data b
    LEFT JOIN {{ ref('<joined_model>') }} j
        ON b.<join_key> = j.<join_key>
),

final AS (
    SELECT
        <unique_key>,
        <event_ts_alias>,
        <partition_column>,
        <business_key_1>,
        <business_key_2>,
        <joined_column_1>,
        <joined_column_2>,
        <derived_column_1>,
        <derived_column_2>
    FROM joined_data
)

SELECT *
FROM final
```

### SQL 模板规则

- 所有上游输入必须通过 `ref()` 或 `source()`；禁止直接写 `<db>.<table>` 这类物理表名。
- `ref()` 只用于已有模型；`source()` 只用于外部表，且对应定义必须先在 `dbt_athena/models/sources.yml` 中存在。
- 先按“`config()` 参数规范”逐项决定参数，再写 SQL 主体。
- `materialized` 必须先遵守“物化约定”，再参考真实样例。
- 只有在目标目录附近明确也是同类模式时，才使用 `incremental + iceberg + merge`。
- `group`、`schema`、`tags`、`partitioned_by`、`unique_key` 必须参考同目录现有模型。
- `<schedule_tag_expr>` 必须遵守“调度约定”，不要自行发明。
- `<time_filter_clause>` 必须与“调度约定”保持一致，不要单独改。
- 如果目标模型不是按天分区，`<partition_column>` 的生成方式要跟邻近模型一致。
- 如果目标目录附近模型没有这类按项目约定的过滤逻辑，不要凭空加。
- 不适用的参数要直接删掉，不要把占位参数留在 `config()` 里。
- 这个 SQL 模板只用于起第一版骨架；CTE 结构、JOIN 数量、分区列、物化方式和增量配置都必须按目标目录附近的真实模型收敛。

## 文件 2：定义文件模板

```yaml
version: 2

models:
  - name: <model_name>
    description: >
      <model_description>。
      每行 = <row_grain>。
      给 <consumer_1> / <consumer_2> 消费。
    columns:
      - name: <unique_key>
        description: <unique_key_description>
        data_tests: [unique, not_null]
      - name: <business_key_1>
        description: <business_key_1_description>
        data_tests: [not_null]
      - name: <business_key_2>
        description: <business_key_2_description>
      - name: <flag_or_enum_column>
        description: "<flag_or_enum_description>"
        data_tests:
          - accepted_values:
              values: [<value_1>, <value_2>, <value_3>]
              config:
                where: "<flag_or_enum_column> is not null"
      - name: <partition_column>
        description: <partition_column_description>
```

### YAML 模板规则

- 优先参考样例使用 `data_tests`。
- 模型描述写业务语义，不写实现过程。
- 至少给核心主键、主要维度、关键指标、分区字段补描述。
- `unique` / `not_null` 只加在语义真的成立的字段上。
- 枚举字段可以参考样例补 `accepted_values`。

## 可选：`unit_tests` 模板

如果目标目录附近已经在用 `unit_tests`，可以补最小可解释样例：

```yaml
unit_tests:
  - name: <unit_test_name>
    description: <test_scenario_description>
    model: <model_name>
    overrides:
      macros:
        dh_filter: "true"
    given:
      - input: ref('<upstream_model>')
        rows:
          - {<input_column_1>: <value_1>, <input_column_2>: <value_2>}
      - input: ref('<joined_model>')
        rows:
          - {<join_key>: <join_value>, <joined_column_1>: <joined_value>}
    expect:
      rows:
        - {<output_column_1>: <expected_value_1>, <output_column_2>: <expected_value_2>}
```

### `unit_tests` 使用规则

- 只在邻近模型也采用这套写法时补。
- 先写最小场景，不要复制大段无关测试数据。
- 如果只是先搭骨架，`unit_tests` 可以先不写，但要在回传里明确说明。

## 最小交付标准

交付给用户前，至少满足：

1. `.sql` 与 `.yml` 都已创建或更新。
2. `config()` 已与邻近模型风格对齐。
3. 模型描述与核心字段描述已补齐。
4. `unique_key`、分区字段、物化方式可解释。
5. 如仍有不确定项，明确列出待确认问题。

## 回传模板

```text
已新增/修改 dbt 模型：
- SQL: dbt_athena/models/<layer>/<domain>/<model_name>.sql
- YAML: dbt_athena/models/<layer>/<domain>/<model_name>.yml

本次已完成：
- 确认模型落点与命名
- 按项目现有风格产出第一版 SQL
- 补齐模型描述、核心字段说明和基础 tests

待你确认：
- <open_question_1>
- <open_question_2>

如果下一步要继续 parse / compile / run / build，这一步转交 dagster-dbt-model-execute。
```

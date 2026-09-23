# DWM 业务指标宽表模式

业务指标链路①的"最后一公里"：把分散在多张 `dwd_app_*_hi` 原子事件表里的信息，按**业务主事件的粒度**拍平成一张宽表，让 GrowthBook / SLA / Superset 三端可以**零 JOIN** 直接消费。

## 为什么需要 dwm 宽表

业务指标的消费方有三类，各自不会写复杂 SQL：

| 消费端 | 能力 | 期望的数据形态 |
|--------|------|---------------|
| **GrowthBook** | 只接受 `SELECT ... FROM <single_table>` 定义 fact table | 一张宽表，一行一个实验单位 |
| **SLA / Alerting** | 简单 `SELECT COUNT(*) WHERE ... GROUP BY ...` | 同上，dimension 扁平 |
| **Superset / BI** | Dataset 不能注册多表 JOIN 逻辑 | 同上 |

如果让每端自己 JOIN `dwd_app_scene_entry_hi` + `dwd_app_recipe_evaluation_hi` + `dwd_ab_user_experiments_f` + 设备活跃 …：

- 口径会漂移（A 端用 inner join 丢数据，B 端用 left join 多数据）
- 上游 schema 一变，三个平台的 SQL 要各自改
- GrowthBook 根本不允许复杂 JOIN 的 fact table SQL

**解法**：在 dwm 层物化一张**粒度 = 业务主事件、列 = 业务需要的所有信息**的宽表，上游口径变化只需改这一张表。

## 表名后缀规约（`_hi` / `_di` / `_mi` / `_df` / ...）

数仓层约定：**表名后缀标记刷新频率 × 物化类型**，消费端看后缀就知道这张表怎么更新。

| 后缀 | 含义 | 典型使用场景 |
|---|---|---|
| `_hi` | **h**ourly **i**ncremental（小时增量） | 事件表、短延迟宽表、实时监控中间层 |
| `_di` | **d**aily **i**ncremental（日增量） | 长窗 lookforward（D7/D30 回填）、日度聚合 |
| `_mi` | **m**onthly **i**ncremental（月增量） | 月度大盘、cohort 留存 |
| `_df` | **d**aily **f**ull（日全量覆盖） | 维度表快照、配置表全量刷新 |
| `_mf` | **m**onthly **f**ull | 月末快照、对账 |
| （无后缀） | 非标表 / 遗留命名 | 应当补后缀后 rename（见下） |

**为什么要后缀**：
- 消费端 SQL 里一看到表名就知道 freshness 边界（`_hi` 最新 1h / `_di` 最新 1d）
- dbt 调度配置按后缀自动推断 schedule（可选做法：统一 config）
- 回填逻辑、时区、partition 键选择都和后缀隐含语义对齐

**遗留无后缀表的处理**：
- 已生产的表如果缺后缀（例如 `dwm_smart_popup_funnel`）→ 开 dbt rename MR 补后缀（`dwm_smart_popup_funnel_hi`）
- 迁移期消费端需同步改 source 引用（GrowthBook `metrics.yml`、Superset dataset SQL、Grafana PromQL 等）
- 不要"新表加后缀、旧表保留无后缀"——混搭命名比统一晚一天迁移成本高
- 实施模式：dbt alias + 消费端改引用 + 几周后删 alias

## 粒度选择

**找"业务主事件"做粒度**：触发整条业务流的那个事件，每次触发对应业务侧关心的一个独立分析单元。

| 业务 | 主事件 | 为什么 |
|------|-------|-------|
| SmartPopup | `scene_entry`（进入场景） | control 组也有 scene_entry 但没 recipe_evaluation，选 scene_entry 才能做组间对比 |
| 推送通知 | `push_send`（服务端触发） | 覆盖"发了但用户没收到"的漏斗顶端 |
| 注册漏斗 | `signup_page_view` | 覆盖"看到但没提交"的流失 |

**反例**：选终端事件（如 `click`、`purchase`）做粒度 → 永远丢 treatment-shown-but-not-clicked 的数据，AB 对比失真。

## 列分组规范（五类）

一张 dwm 表的列按 5 类组织，每类放在 SELECT 的一个段里，顺序固定便于 diff review：

```
SELECT
  -- ① 主键 & 时间
  event_id, dvce_created_tstamp, dt,

  -- ② 用户 / 设备维度（来自主事件的 context）
  user_id, serial_number, app_id, country_no,

  -- ③ 业务维度（来自主事件的 unstruct_event）
  scene_id, recipe_id, rules_version, engine_version,

  -- ④ AB 分组（LEFT JOIN AB 表）
  experiment_id, variation_key,

  -- ⑤ 漏斗下游事件的 detail（LEFT JOIN 下游事件）
  hit_id,           -- correlation id
  condition_met,    -- 来自 recipe_evaluation
  fatigue_passed,
  fatigue_blocked_by,
  shown,
  action,           -- 来自 popup_interaction

  -- ⑥ 长窗口指标（事件后 N 天才能计算，由 backfill job 回填）
  device_active_d7,
  device_active_d30
```

| 类别 | 作用 | 填充方式 |
|------|------|---------|
| ① 主键 & 时间 | unique_key / 分区 | 主事件直接给 |
| ② 用户设备 | GrowthBook assignment，设备维度过滤 | 主事件 context |
| ③ 业务维度 | Dashboard group-by，实验维度切片 | 主事件 unstruct |
| ④ AB 分组 | GrowthBook 分析 variation 对比 | LEFT JOIN AB 表 |
| ⑤ 漏斗 detail | 近端指标、漏斗步骤、交互质量 | LEFT JOIN 下游事件 |
| ⑥ 长窗口 | 留存类远端指标 | 独立 backfill job 回填 |

## JOIN 模式

### JOIN 1：主事件 → 下游漏斗事件

按 **correlation id**（如 `hit_id`）+ **时间窗口**：

```sql
LEFT JOIN evaluations ev
    ON  se.scene_id  = ev.eval_scene_id
    AND se.recipe_id = ev.eval_recipe_id
    AND ev.eval_tstamp BETWEEN se.dvce_created_tstamp
                           AND se.dvce_created_tstamp + INTERVAL '5' SECOND
LEFT JOIN interactions ix
    ON ev.hit_id = ix.interact_hit_id
```

- 时间窗口约束（`BETWEEN ... AND ... + 5 SECOND`）防止误匹配到同用户后续场景
- 下游事件用 correlation id（`hit_id`）串联，避免重复依赖用户+时间
- **LEFT JOIN 必要**：control 组没 recipe_evaluation → 字段 NULL（不能丢行）

### JOIN 2：AB 分组

```sql
LEFT JOIN {{ ref('dwd_ab_user_experiments_f') }} ab
    ON  se.user_id = ab.user_id
    AND ab.experiment_id = CONCAT('smart_popup_', se.scene_id)
```

- `dwd_ab_user_experiments_f` 是用户-实验分组的 final 视图（每 user × experiment × variation 一行，带 dvce_created_tstamp 代表分配时间）
- 按 user_id + experiment_id 匹配
- LEFT JOIN：不在实验里的用户 variation_key NULL（可过滤做整体基线分析）

### JOIN 3：设备/用户维度表（如活跃）

见 [backfill job 章节](#回填-job-模式) — 不在主 dwm 表里直接 JOIN（因为要等时间窗口），而是 backfill job 后续写回。

## 物化策略

参考 SmartPopup dwm 的 config（与 dwd 一致）：

```sql
{{ config(
    materialized='incremental',
    group='analytics',
    schema='analytics',
    tags=[var("schedule_hourly")],
    table_type='iceberg',
    incremental_strategy='merge',
    incremental_predicates=["dynamic_date_range", "dt"],
    unique_key=['event_id'],
    partitioned_by=['dt'],
    on_schema_change='sync_all_columns',
    format='parquet'
) }}
```

| 配置 | 作用 |
|------|------|
| `incremental + merge` | 只处理增量数据，backfill job 能更新已存在行 |
| `unique_key=event_id` | 保证去重，backfill 按 event_id 定位 |
| `partitioned_by=['dt']` | 日分区，查询和 backfill 都能分区裁剪 |
| `tags=[schedule_hourly]` | Dagster 按小时调度 |
| `on_schema_change='sync_all_columns'` | 新增列自动加，不需要重建 |

**lookback_lag**：`{% set lookback_lag = 1 %}` + `WHERE dh_filter(lag=1, ...)` → 每次增量跑看回过去 1 小时（给上游落盘留 buffer）。

## 回填 Job 模式

长窗口指标（D7/D30 留存、长期 LTV、次月复购）不能在主 dwm job 里实时算 — 要等窗口到了才有结果。用**独立 backfill job**：

### 回填 job 模板（基于 `dwm_smart_popup_funnel_backfill.sql`）

```sql
{{ config(
    materialized='incremental',
    tags=[var("schedule_daily")],      -- ← 每天跑，不是小时
    incremental_strategy='merge',
    unique_key=['event_id'],
    partitioned_by=['dt'],
    on_schema_change='sync_all_columns'
) }}

-- Step 1: 筛出"窗口已到 + 列仍为 NULL"的历史行（幂等的双条件）
WITH to_backfill AS (
    SELECT event_id, dt, serial_number, dvce_created_tstamp,
           device_active_d7, device_active_d30
    FROM {{ ref('dwm_smart_popup_funnel_hi') }}
    WHERE serial_number IS NOT NULL
      AND (
          (dt <= current_date - INTERVAL '7' DAY AND device_active_d7 IS NULL)
          OR
          (dt <= current_date - INTERVAL '30' DAY AND device_active_d30 IS NULL)
      )
      AND dt >= current_date - INTERVAL '37' DAY   -- 上界防止每天扫全表
),

-- Step 2: 计算指标（这里：事件后 N 天内设备是否活跃）
device_activity AS (
    SELECT serial_number, dt AS active_dt
    FROM {{ ref('dws_device_active_stats_hi') }}
    WHERE dt >= current_date - INTERVAL '67' DAY
    GROUP BY serial_number, dt
),

d7_check AS (
    SELECT bf.event_id,
           CASE WHEN COUNT(da.active_dt) > 0 THEN TRUE ELSE FALSE END AS is_active_d7
    FROM to_backfill bf
    LEFT JOIN device_activity da
        ON bf.serial_number = da.serial_number
        AND da.active_dt BETWEEN bf.dt AND bf.dt + INTERVAL '7' DAY
    WHERE bf.dt <= current_date - INTERVAL '7' DAY
      AND bf.device_active_d7 IS NULL
    GROUP BY bf.event_id
),

d30_check AS (
    -- 同上，窗口 30 天
    ...
)

-- Step 3: SELECT 所有列，用 COALESCE 把新值 merge 回原行
SELECT
    f.event_id, f.dt, ... ,  -- 所有其他列原样带出
    COALESCE(d7.is_active_d7,  f.device_active_d7)  AS device_active_d7,
    COALESCE(d30.is_active_d30, f.device_active_d30) AS device_active_d30
FROM {{ ref('dwm_smart_popup_funnel_hi') }} f
LEFT JOIN d7_check  d7  ON f.event_id = d7.event_id
LEFT JOIN d30_check d30 ON f.event_id = d30.event_id
WHERE f.event_id IN (SELECT event_id FROM to_backfill)
```

**关键设计**：

1. **双条件**：`dt <= today - N day AND column IS NULL` — 窗口已到 **且** 还没回填 → 天然幂等，可以每天跑不怕重复写
2. **上界 `dt >= today - (N+7) day`** — 防止每天扫全历史（这里 37 天给 D30 留 7 天 buffer）
3. **COALESCE 保留已回填值** — 避免重跑清空之前算出的结果
4. **incremental merge + unique_key=event_id** — 回填的行会按 event_id 更新原行
5. **独立 tag `schedule_daily`** — 和 hourly 主 job 分开调度

### 回填 job vs 主 job 的职责边界

| 职责 | 主 dwm job (`dwm_*_funnel`) | 回填 job (`dwm_*_funnel_backfill`) |
|------|-------------------------|---------------------------------|
| 粒度 | 每小时一次 | 每天一次 |
| 范围 | 新的 scene_entry | 已存在行中窗口已到的 |
| 写入列 | 主事件 + 漏斗事件 + AB | 长窗口列（D7/D30） |
| 默认值 | 长窗口列写 `CAST(NULL AS BOOLEAN)` | COALESCE 补齐 |

## 空值约定（Document it or pay later）

GrowthBook 和 SLA 的 proportion metric 对 NULL 的处理差别很大（NULL 在分母/分子/filter 里行为不同），必须在 dwm 表的 `.yml` 里明文写清楚每一列的 NULL 语义：

| 列 | NULL 含义 | 消费端注意 |
|----|----------|-----------|
| `shown` | control 组（跳过评估）或评估阶段失败 | `shown_rate = COUNT(shown=true) / COUNT(*)` |
| `action` | 未展示，或展示后用户没交互过 | `click_rate = COUNT(action='click_primary') / COUNT(shown=true)` |
| `fatigue_blocked_by` | 没被 fatigue 拦截 | 只在 `fatigue_passed=false` 时非 null |
| `variation_key` | 用户不在实验里 | GrowthBook 会自动过滤 |
| `device_active_d7` | 窗口未到，或设备无 serial_number | backfill 会逐步填 |

写在 `.yml` 里（dbt column description）→ DataHub 自动展示 → 消费方查 schema 能看到。

## 消费端友好性

### GrowthBook fact table 定义（直接 SELECT）

```yaml
factTables:
  - id: smart_popup_funnel
    data:
      name: SmartPopup Funnel
      datasource: ds_xxx
      projects: ["smart-popup"]
      sql: |
        SELECT
          event_id, user_id, dt, dvce_created_tstamp AS timestamp,
          serial_number, scene_id, recipe_id, variation_key,
          shown, condition_met, fatigue_passed, action,
          device_active_d7, device_active_d30
        FROM analytics.dwm_smart_popup_funnel_hi
      userIdTypes: [user_id]
```

**零 JOIN** — GrowthBook 只需要这一张表就能定义所有 fact metric。见 [growthbook-gitlab-cicd-pattern.md](growthbook-gitlab-cicd-pattern.md)。

### SLA SQL（直接 GROUP BY）

```sql
SELECT dt,
       variation_key,
       COUNT(*) FILTER (WHERE shown = TRUE) * 1.0 / COUNT(*) AS show_rate,
       COUNT(*) FILTER (WHERE action = 'click_primary') * 1.0
         / NULLIF(COUNT(*) FILTER (WHERE shown = TRUE), 0) AS ctr
FROM analytics.dwm_smart_popup_funnel_hi
WHERE dt >= current_date - INTERVAL '7' DAY
GROUP BY dt, variation_key
```

### Superset dataset

直接把 `analytics.dwm_smart_popup_funnel_hi` 注册为 dataset → 所有列变成 Superset metric / dimension，拖拽出图不写 SQL。

## 真实示例：`dwm_smart_popup_funnel_hi`

基于 `dbt_athena/models/dw/analytics/dwm_smart_popup_funnel_hi.sql`，完整结构：

```sql
{% set lookback_lag = 1 %}

{{ config(
    materialized='incremental',
    group='analytics',
    schema='analytics',
    tags=[var("schedule_hourly")],
    table_type='iceberg',
    incremental_strategy='merge',
    incremental_predicates=["dynamic_date_range", "dt"],
    unique_key=['event_id'],
    partitioned_by=['dt'],
    on_schema_change='sync_all_columns',
    format='parquet'
) }}

WITH scene_entries AS (
    SELECT
        event_id, dvce_created_tstamp,
        date_trunc('day', dvce_created_tstamp) AS dt,
        user_id_com_base_base_schema AS user_id,
        device_sn_com_base_base_schema AS serial_number,
        app_id,
        country_no_com_base_base_schema AS country_no,
        scene_id, recipe_id, rules_version, engine_version
    FROM {{ ref('dwd_app_scene_entry_hi') }}
    WHERE {{ dh_filter(lag=lookback_lag, dh_expr="dvce_created_tstamp") }}
),

evaluations AS (
    SELECT
        scene_id AS eval_scene_id,
        recipe_id AS eval_recipe_id,
        hit_id, condition_met, fatigue_passed, fatigue_blocked_by, shown,
        dvce_created_tstamp AS eval_tstamp
    FROM {{ ref('dwd_app_recipe_evaluation_hi') }}
    WHERE {{ dh_filter(lag=lookback_lag, dh_expr="dvce_created_tstamp") }}
),

interactions AS (
    SELECT hit_id AS interact_hit_id, action
    FROM {{ ref('dwd_app_popup_interaction_hi') }}
    WHERE {{ dh_filter(lag=lookback_lag, dh_expr="dvce_created_tstamp") }}
),

ab_groups AS (
    SELECT user_id AS ab_user_id, experiment_id,
           variation_id AS variation_key
    FROM {{ ref('dwd_ab_user_experiments_f') }}
)

SELECT
    se.event_id, se.dvce_created_tstamp, se.dt,
    se.user_id, se.serial_number, se.app_id, se.country_no,
    se.scene_id, se.recipe_id, se.rules_version, se.engine_version,
    ab.experiment_id, ab.variation_key,
    ev.hit_id, ev.condition_met, ev.fatigue_passed, ev.fatigue_blocked_by, ev.shown,
    ix.action,
    CAST(NULL AS BOOLEAN) AS device_active_d7,     -- backfill 回填
    CAST(NULL AS BOOLEAN) AS device_active_d30     -- backfill 回填
FROM scene_entries se
LEFT JOIN evaluations ev
    ON  se.scene_id  = ev.eval_scene_id
    AND se.recipe_id = ev.eval_recipe_id
    AND ev.eval_tstamp BETWEEN se.dvce_created_tstamp
                           AND se.dvce_created_tstamp + INTERVAL '5' SECOND
LEFT JOIN interactions ix
    ON ev.hit_id = ix.interact_hit_id
LEFT JOIN ab_groups ab
    ON  se.user_id = ab.ab_user_id
    AND ab.experiment_id = CONCAT('smart_popup_', se.scene_id)
```

配套 `.yml` 里写清楚每列的 NULL 语义和 unique/not_null 测试。

配套 backfill job `dwm_smart_popup_funnel_backfill.sql` 按日调度，JOIN `dws_device_active_stats_hi` 补 D7/D30。

## 检查清单（新增 dwm 表时）

- [ ] 粒度是业务主事件（不是漏斗终点事件，避免 control 组行缺失）
- [ ] SELECT 顺序按 5 类分段，review 时一眼能找到
- [ ] 所有 JOIN 是 LEFT JOIN（除非业务必须 INNER）
- [ ] 下游事件 JOIN 带时间窗口约束
- [ ] AB JOIN 用 `dwd_ab_user_experiments_f`（不是 `_hi` 或 `_history`）
- [ ] 长窗口列先写 `CAST(NULL AS <type>)` 占位，独立 backfill job 回填
- [ ] `.yml` 里每个 NULL-able 列注明 NULL 含义
- [ ] GrowthBook `sql:` 直接 SELECT 这张表，不需要 JOIN
- [ ] Superset 注册为 dataset（委派 `superset` skill）
- [ ] DataHub 可搜、lineage 正确

## 参考

- `dbt_athena/models/dw/analytics/dwm_smart_popup_funnel_hi.sql` / `.yml`
- `dbt_athena/models/dw/analytics/dwm_smart_popup_funnel_backfill.sql`
- `dbt_athena/models/dw/analytics/dwm_event_session_hi.sql`（session 化模式参考）
- `dbt_athena/models/dw/analytics/dwm_mobile_sessions_hi.sql`（window function 模式参考）
- `dbt_athena/models/dw/ab/dwd_ab_user_experiments_f.sql`（AB 分组 final 表）
- `dbt_athena/models/dw/device/dws_device_active_stats_hi.sql`（设备活跃来源）
- [event-parsing-pipeline.md](event-parsing-pipeline.md) — dwd 层怎么来的
- [growthbook-gitlab-cicd-pattern.md](growthbook-gitlab-cicd-pattern.md) — 这张表怎么变成 metric

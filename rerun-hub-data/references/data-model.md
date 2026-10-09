# 取数时看到的数据（launch_monitor 数据模型 v2）

正本：infra/ai-data-platform 的 `docs/integration/launch_monitor/model.html`（§4 catalog、§5 会话、§6 recording、§9 trackman、§10 shots、§11 表、§12 时钟、§17 怎么读，ADR-0012）。面向用户的摘要：https://pages.addx.ai/infra/ai-data-platform/site/docs/data-model.html#query-view 。下面是摘要；**实际以 `hub_data.py probe` 的输出为准**。

## 结构

- **project**：Hub 按业务线分 project（`launch_monitor`、`team_sport`），条目名的前缀就是 project 名。
- **会话 Dataset**：每个 project 默认一对，`<project>.default_internal_user`（内部 / 测试账号的上传）和 `<project>.default_external_user`（外部用户的上传，ADR-0008）；也可能有别的会话 Dataset（如 `indoor_validation`）。一个 Segment = 一次会话，`segment_id = session_id`。办公网预览的样例数据在 `launch_monitor.default_internal_user`。
- **会话属性**在 segment table 的 `property:<name>:<field>` 列上，例如 `property:session:started_at`、`property:session:record_count`、`property:device:device_name`、`property:account:account_id`。用 `ds.segment_table()` 读取。
- **层**：`recording`（`/lm/…`）、`algo_dump`（`/lm/algo/…`）、`calibration`（`/lm/calibration/…`）、`trackman`（`/trackman/{link_id}/…`）、`shots`（`/shots`，派生层），另有预留的 `shot_review`、`app`、`metrics`。
- **Trackman 数据只在会话的 `trackman` 层**，没有单独的原始数据集（`trackman_raw` 已取消，平台 #47）。
- **表**每个 project 一对：`<project>.ingest_registry` 和 `<project>.ingest_status`（登记和摄取状态，平台 #50）。**没有杆表**。
- `*__assets`、`*__blueprint` 是系统附属条目，忽略。

## 杆在哪里

**杆是 `shots` 层里的一行**，不是 Segment，也不是表里的一行。读法：`ds.filter_contents(["/shots"]).reader(index="event_time")`。

`/shots` 上的 AnyValues 字段（列名形如 `/shots:status`，值是单元素 list，取值用 `col("/shots:status")[0]`）：

- `shot_id`：`lm:{lm_shot_id}` 或 `tm:{link_id}:{shot_key}`
- `shot_index`：按代表时刻排序，从 1 开始
- `status`：`matched` / `lm_only` / `trackman_only` / `ambiguous` / `excluded`
- `lm_shot_id`、`lm_event_time_ms`、`lm_rejected`
- `tm_link_id`、`tm_shot_key`、`tm_stroke_id`、`tm_event_time_ms`
- `dt_ms`：matched 时扣掉时钟偏差后的残差
- `match_method`、`calibration_missing`、`revision`

另有 `/shots/clock_map`（每个开机分组的 LM↔Trackman 时钟偏差）和 `/shots/summary`（static 的计数与输入来源）。每行的 `event_time` 是这一杆的代表时刻，有 LM 记录的行同时写 `device_mono_ns`。

shots 派生作业在生产环境还是规划状态；演示数据按规格写了 `lm_only` 和 `trackman_only` 两类，没有 `matched`。

## 时间线（`reader(index=...)`）

| 时间线 | 类型 | 哪些层有 | 能比较的范围 | 时间窗怎么写 |
|---|---|---|---|---|
| `event_time` | timestamp（UTC） | 所有带时间的层 | 跨层、跨来源都能比，但 LM 与 Trackman 各用自己的墙钟，偏差见 `/shots/clock_map` | ISO-8601 或纪元纳秒；SDK 里用 `np.datetime64(..., "ns")` |
| `device_mono_ns` | duration（纳秒） | `recording`、`algo_dump`、`shots`（有 LM 的行） | 只在同一次开机内；跨开机会重复 | 整数纳秒；SDK 里用 `np.timedelta64(n, "ns")` |

Trackman 层只有 `event_time`。要逐杆精确比较 LM 和 Trackman，读 `shots` 的 `dt_ms`，不要自己拿两边的 `event_time` 相减。

## 常用路径

- LM 指标：`/lm/metrics/{name}`（Scalars，另有 `lm_shot_id`、`source`、`status`、`unit`），例如 `ball_speed`、`club_speed`、`carry`、`spin_rate`
- LM 记录锚点：`/lm/record`（每条击球记录一行：`lm_shot_id`、`club`、`boot_wallclock_ms`……）
- LM 事件：`/lm/events/impact`、`/lm/events/radar_trigger`……
- LM 视频：`/lm/video/main`、`/lm/video/slowmo`（AssetVideo + VideoFrameReference）
- Trackman：`/trackman/{link_id}/shot`（`stroke_id`、`shot_key`、`group_id`、`club`）、`/trackman/{link_id}/{raw|normalized}/{Name}`（Scalars，API 原名、原单位，如 `BallSpeed`）、`/trackman/{link_id}/{raw|normalized}/ball_trajectory`
- 路径里不出现杆 id；同一路径上的多条记录靠 `lm_shot_id` 分量区分

列名格式：Archetype 分量为 `{路径}:{Archetype}:{component}`（如 `/lm/metrics/ball_speed:Scalars:scalars`），AnyValues 分量为 `{路径}:{字段}`（如 `/shots:status`）。路径里的特殊字符已转义，从 probe 输出复制，不要手拼。

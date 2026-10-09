# 导入 rerun hub：写了什么、代码从哪来

正本是平台仓库 infra/ai-data-platform 的 `docs/integration/launch_monitor/model.html`（LM 数据模型 v2）：§9 trackman 层，§10 shots 与配对规则 `time_nearest_v1`，§11 两张表，§12 时钟。这里是摘要。

## 写入内容（一次 import）

| 写到哪 | 内容 | 方式 |
|---|---|---|
| 表 `<project>.ingest_registry`（会话所在 project 的） | 这条链接挂在哪个会话、时间窗；`link_key = trackman:<dataset>:<session_id>:<link_id>`，同一会话同一条链接复用 `link_id` | Catalog SDK 表 upsert |
| 会话 Segment 的 `trackman` 层 | 时间窗内的每一杆：`/trackman/{link_id}/shot`、`/raw|normalized/{指标}`、`ball_trajectory`，时间轴 `event_time`，不换算单位；这是 TrackMan 数据唯一的一份（没有单独的原始数据集，平台 #47） | Hub HTTP 上传，REPLACE |
| 会话 Segment 的 `shots` 层 | 每杆一行 `/shots`（status、lm_shot_id、tm_shot_key、dt_ms…）、`/shots/clock_map`、`/shots/summary`（`algo_version = skill:trackman-shot-export@<版本>`、`params_json`） | 同上 |
| 表 `<project>.ingest_status` | 每一步的状态（`PULLING → TRANSFORMING → REGISTERED`，失败 `QUARANTINED`），source 为 `trackman` / `lm_shots` | Catalog SDK 表 upsert |

口令需要：会话所在 project 的 owner（覆盖会话 Dataset 和这个 project 的两张表；表名前缀就是 project 名，平台 #50）。个人口令在 Hub 控制台「我的口令」里按当前项目签发；没有权限就找这个项目的 owner 在「成员」里授予。

## 代码从哪来

`scripts/cloud_ingest/` 是平台 cloud-ingest 的无 dagster 部分，**逐字节拷贝**（`scripts/cloud_ingest/__init__.py` 里写了来源 commit）：解析报告、写 RRD、配对、导入流程和平台的 dagster 作业是同一份代码。不要在这里改：在 ai-data-platform 改 `cloud-ingest/src/cloud_ingest/` 并跑它的测试，再用 `scripts/sync_trackman_skill.py <本目录>` 同步过来（`--check` 可核对是否一致）。

这条路直连 Hub，不经过 collect-gateway（ADR-0002 的例外，平台 issue #48）。已知限制：重复导入时被替换掉的旧对象暂不回收（#49）。删会话时 gateway 会停用这个会话的链接、清空 URL（#47）。

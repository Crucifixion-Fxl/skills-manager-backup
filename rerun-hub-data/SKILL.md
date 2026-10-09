---
name: rerun-hub-data
description: 从 ai-data-platform 的 rerun-hub（addx-rerun-hub，自建 Rerun 目录服务）取数做查询、分析、导出和训练数据加载：先用 hub_data.py probe 摸清数据集、时间线和列名，再用官方 rerun-sdk 0.38.1 的 CatalogClient / reader / dataloader 写查询，或用 hub_data.py export 导出 parquet/csv。当用户说"从 Hub 取数"、"查 launch_monitor 数据"、"导出某杆 / 某个 session 的数据"、"rerun hub query"、"把 Hub 数据接到 PyTorch / dataloader"、"对齐多路数据"时触发。不负责往 Hub 提交数据（那是 collect-sdk-integration），不做 Hub 部署和权限管理。
---

平台登录与认证 SSOT：[rerun-hub](../../hardware/rerun-hub/SKILL.md)。本 Skill 保留业务流程与门禁，认证事实由平台 owner 维护，日常访问调用 `web-access`。


# rerun-hub-data — 从 rerun-hub 取数

rerun-hub 实现的是官方 Rerun 目录协议（gRPC `RerunCloudService`），所以**取数用官方 `rerun-sdk[catalog]==0.38.1`**，官方 [Query and transform](https://rerun.io/docs/howto/query-and-transform) 和 [Dataloader](https://rerun.io/docs/howto/train/dataloader) 的写法原样适用。本 skill 只补官方文档没有的：怎么连这套 Hub、数据长什么样、哪里行为不同，以及两个脚本，用来固定"先摸底、再缩范围取数"的流程。

面向人的正本文档（先以它们为准，本 skill 与之冲突时按文档）：
- 取数与训练：https://pages.addx.ai/infra/ai-data-platform/site/docs/query-and-train.html
- 环境、token、网络、兼容性：https://pages.addx.ai/infra/ai-data-platform/site/docs/hub.html#environments
- 数据模型（时间线、entity 路径、Shots 表）：https://pages.addx.ai/infra/ai-data-platform/site/docs/data-model.html#query-view
- HTTP API 参考：https://pages.addx.ai/infra/ai-data-platform/site/docs/api.html

## Description — 现状（先查这里，不要凭记忆）

| 能力 | 状态 | 说明 |
|---|---|---|
| 查询 / 导出（`reader`、`filter_segments`、`filter_contents`、时间窗） | 可用 | 办公网预览上实测过，见 `references/query.md` |
| latest-at 对齐（`using_index_values` + `fill_latest_at`） | Hub ≥ m1 `0f44ab3` 可用 | 更早的版本返回空值（加 `filter_contents` 时 0 行）；旧版本按 `references/query.md` R3 的本地对齐写法 |
| 数据模型 | launch_monitor v2（ADR-0012） | 会话 = Segment，杆 = `shots` 层的一行，没有杆表；见 `references/data-model.md` |
| `get_index_ranges()` | Hub ≥ m1 `0f44ab3` 可用 | 更早的版本 segment table 缺 `<时间线>:start/:end` 列 |
| PyTorch dataloader（`rerun.experimental.dataloader`） | Hub ≥ m1 `0f44ab3` 已验证 | 11 条用例 + 吞吐基线（[报告](https://pages.addx.ai/infra/ai-data-platform/testing/evidence/2026-09-27-hub-dataloader/index.html)，infra/ai-data-platform#43）；要点见 `references/training.md` |
| 环境 | 办公网预览可用；staging 只有集群内地址 | 见 `references/connection.md` |

## Rules

- **SDK 版本必须是 0.38.1**，与服务端一致；`hub_data.py probe` 会检查，不一致就停下来，不要硬跑。
- **token 和预签名 URL 是凭据**：只从环境变量 `RERUN_HUB_TOKEN` 读，不打印、不写进代码 / notebook 输出 / 文件 / issue / 聊天。脚本输出已做过滤，自己写的代码也要做到。
- **默认只读**。注册、删除、建表、子数据集、schema 发布这些写操作，只有用户明确要求才做，而且需要对应 project 的 owner / producer 角色。没有权限就如实告诉用户，去找对应 project 的 owner 在 Hub 控制台「成员」里授权，不要找别的 token。
- **不凭猜测写 entity 路径、列名和时间线**：一律来自 `hub_data.py probe` 的输出或 `dataset.schema()`。
- **先缩范围再取数**：先指定 segment（`filter_segments`），再圈 entity（`filter_contents`），给时间窗；先拿一个 segment 抽样确认，再放大。不要一上来读整个数据集。
- **`/hub/v1/sql` 只回答"数据在哪、有多少"**（chunk 索引），不能当数据查询用。
- 能用官方 API 做的，不自己写 HTTP / gRPC 客户端。

## 流程

### Step 1：连接

```bash
pip install "rerun-sdk[catalog]==0.38.1"        # [catalog] 带 datafusion，必需；要 to_pandas() 再装 pandas；训练再装 [dataloader]
export RERUN_HUB_URL=rerun+http://<hub-host>:51234      # gRPC 地址，从 references/connection.md 指向的环境表里取
export RERUN_HUB_TOKEN=...                              # 用户自己设置，不要让用户把 token 贴进对话
```

没有 token：告诉用户按 `references/connection.md` 申请（取数用 viewer 角色），不要替他签。

### Step 2：摸底（必做）

`<skill-dir>` 是本文件所在目录。

```bash
python <skill-dir>/scripts/hub_data.py probe                                   # 版本、数据集、表
python <skill-dir>/scripts/hub_data.py probe --dataset <name>                  # segment、时间线、按 entity 分组的列
python <skill-dir>/scripts/hub_data.py probe --table launch_monitor.ingest_registry   # 表的列和前几行（每个 project 只有登记 / 状态两张表，没有杆表）
```

加 `--json` 得到机器可读结果。据此确认：用哪个时间线（`index`）、哪些 entity、哪些 segment；业务含义对照 `references/data-model.md`。

### Step 3：选路径

| 用户要什么 | 做法 |
|---|---|
| 一份数据文件（parquet / csv） | `hub_data.py export`，见下 |
| 在代码 / notebook 里分析 | 按 `references/query.md` 的 recipe 写 SDK 代码 |
| 按杆筛选再取明细 | 先读 `shots` 层（`ds.filter_contents(["/shots"]).reader(index="event_time")`）筛杆，拿 `rerun_segment_id` 和代表时刻，再按时间窗取 `/lm/**`、`/trackman/**`（recipe R1） |
| 会话列表 / 会话属性 | `ds.segment_table()` 的 `property:*` 列（recipe R0） |
| 多路数据对齐到固定频率 | `reader(index=…, using_index_values=…, fill_latest_at=True)`（Hub ≥ `0f44ab3`）；旧版本取原始行后在本地用 `pandas.merge_asof` 对齐（recipe R3）。LM 与 Trackman 的逐杆偏差直接读 `shots` 的 `dt_ms` |
| 训练数据加载 | `references/training.md`；先看 `hub_data.py probe --dataset <name>` 输出的 `latest-at / get_index_ranges / dataloader` 一行，显示不支持就别写 loader，改为先导出 |
| 给别人看一段数据 | `ds.segment_url(...)`（桌面 Viewer）或 `POST /hub/v1/shares`（控制台链接），都不含凭据 |
| 只想知道数据量 / 分布 | `POST /hub/v1/sql`（chunk 索引） |

导出：

```bash
python <skill-dir>/scripts/hub_data.py export --dataset <name> --segment <id> [--segment <id> ...] \
    --index <timeline> --contents '/trackman/**' --start <ns|ISO> --end <ns|ISO> \
    [--columns <col> ...] [--limit N] --out <file>.parquet
```

- 必须 `--segment`，或显式 `--all-segments`；已存在的输出要 `--force` 才覆盖。
- `--start/--end`：duration / 整数时间线用整数纳秒；timestamp 时间线用 ISO-8601（UTC）或纪元纳秒；区间左闭右开。
- 旁边会写 `<out>.provenance.json`（数据集、segment、时间线、时间窗、行数、SDK 版本），交付时一并给用户。

### Step 4：交付

给用户：可复现的命令或脚本、输出文件路径、行数和列数、用了哪些数据集 / segment / 时间线 / 时间窗。结果为空或和预期不符时，先回 Step 2 核对时间线和时间窗的单位，不要换参数乱试。

## 示例

**用户**："把 launch_monitor.default_internal_user 里 event_time 最早的那一杆前后 1 秒的 Trackman 和 LM 指标导出成 parquet"

1. `hub_data.py probe --dataset launch_monitor.default_internal_user` → 时间线有 `event_time`（timestamp）和 `device_mono_ns`；杆在 `/shots`，指标在 `/lm/metrics/**`、`/trackman/**`。
2. 按 recipe R1 读 `shots` 层，取 `event_time` 最早的一行，拿到 `rerun_segment_id` 和代表时刻 `t`（纳秒）。
3. `hub_data.py export --dataset launch_monitor.default_internal_user --segment <rerun_segment_id> --index event_time --contents '/lm/metrics/**' --contents '/trackman/**' --start <t-1s 的纳秒> --end <t+1s 的纳秒> --out shot.parquet`
4. 回报：`shot.parquet`，N 行 × M 列，provenance 文件路径，所选杆的 `shot_id` 和 `status`。

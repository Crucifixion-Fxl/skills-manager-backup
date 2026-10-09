---
name: trackman-shot-export
description: 给做 Launch Monitor 实验的同事用：把 TrackMan 分享链接（tm-short.me / web-dynamic-reports）(a) 导出成每杆结果表 + 球轨迹表两个 CSV；或 (b) 挂到 rerun hub 里的一次实验会话上，把 TrackMan 每一杆按击球时间和 LM 的击球配对（写会话的 trackman 层和 shots 层），之后在 Hub / rerun-hub-data 里对比 LM 与 TrackMan。当用户说"导出 TrackMan 数据"、"把 TrackMan 链接导入 Hub"、"TrackMan 和 LM 对齐 / 配对"、"这次实验的 TrackMan 报告"时触发。不做仿真、机器人运动规划，也不负责从 Hub 取数分析（那是 rerun-hub-data）。
---

# TrackMan 导出 / 导入 rerun hub 并对齐到杆

## Description

给做 Launch Monitor 实验的同事用，两种用法，先问清用户要哪种：

- **导出 CSV**：只要数据文件，不碰 Hub。只需要 Python 3.10+ 标准库。
- **导入 rerun hub 并对齐到杆**：实验做完后，把这次实验的 TrackMan 报告挂到 Hub 里对应的会话上，按击球时间把 TrackMan 的每一杆和 LM 的每一杆配对，之后就能在 Hub 里并排比较。直连 Hub，只要一份 Hub 口令。

## Rules

- TrackMan 分享链接**等同于凭证**：拿到链接的人都能看报告。不写进命令行参数、文件、issue、群聊或日志；让用户在自己的终端里设置环境变量 `TRACKMAN_URLS`。Hub 口令同样只让用户自己 `export`，不要贴进对话。
- 只用本 skill 的脚本：不要从截图抄数、另写解析 / 写 Hub / 配对代码，也不要改 `scripts/cloud_ingest/`（平台代码的拷贝，见 [references/hub-import.md](references/hub-import.md)）。
- 导入前必须先 `preview` 并把结果讲给用户，用户确认后再 `import`。
- 配对阈值还没定稿，结果要当初步结论告诉用户；无权限（401/403）让用户找会话所在项目的 owner 在 Hub「成员」里授权，不要换别人的口令。

## 导出 CSV

调用本 skill 的 `scripts/export_trackman.py`。默认生成用户参考格式的两个 CSV：**每杆一行的结果表、每个球轨迹点一行的轨迹表**。Python 3.10+，仅标准库，无需浏览器或登录。不要从截图抄数、按固定指标丢列、重新模拟轨迹或另写重复提取代码。

### 执行

`<skill-dir>` 为当前 SKILL.md 所在目录。Windows 需要使用可工作的 Python 解释器，WindowsApps/python.exe 可能只是占位程序。

```powershell
& '<python-executable>' '<skill-dir>/scripts/export_trackman.py' 'https://tm-short.me/EXAMPLE1' 'https://tm-short.me/EXAMPLE2' --out-dir '<new-export-directory>'
```

- 一次合并所有输入，输出且仅输出 `<prefix>_all_swings.csv` 和 `<prefix>_ball_trajectory.csv`。
- 默认前缀是 `TrackMan_<GroupDate>`；多日期为 `TrackMan_multiple_dates`，无日期为 `TrackMan_undated`。不要把导出日期当作击球日期。可用 `--prefix TrackMan_custom` 指定文件名前缀。
- 保留参考结果表的 153 个基础列和轨迹表的 11 个基础列及顺序，新增字段追加在末尾。表头内置于脚本，运行不依赖原参考 CSV 或 Downloads 目录。
- sources 也可传一个或多个**原始 API 响应 JSON** 路径离线执行。生成的 CSV 或结果 JSON 不能当作原始报告输入。
- 只有用户需要旧 JSON 结构时加 `--format json`，输出 `shot_results.json`、`shot_trajectories.json`；不要默认同时交付四个数据文件。
- 输出目录必须尚不存在，不覆盖既有结果。默认每个网络请求超时 60 秒，可用 `--timeout` 调整；出错返回非零退出码，不发布部分报告，不无限重试。

### 数据解释

首次处理或修改导出逻辑时阅读 [references/schema.md](references/schema.md)。核心约定：

- `Raw_*` 来自 `Measurement`，`Normalized_*` 来自 `NormalizedMeasurement`。轨迹的 `DataSpace` 分别为 `raw`、`normalized`，`PointIndex` 从 0 开始，保留接口点序。
- URL `nd=true` 不保证存在标准化数据；没有返回就留空并报告警告，禁止将 Raw 复制成 Normalized。
- 同一导出内按 `(ExportId, ShotKey)` 关联两表，点主键再加 `(DataSpace, PointIndex)`。GroupId 在不同球杆组可能重复；不可仅按 GroupId 或 StrokeIndex 关联/去重。
- 保留所有 API 杆，含网页隐藏、缺指标、无轨迹、仅 Id 的占位记录。占位杆保留结果行并标记 `IsPlaceholderRecord=True`，无点时轨迹表零行。不能因缺 Carry 或无杆速删除记录。
- 指标缺失输出空单元格，数值 0 保持 0；CSV 中缺失与显式 null 都为空。数组用紧凑 JSON，布尔值用 True/False，编码 UTF-8 BOM。用户参考格式保留结果表中的轨迹 JSON 列，同时另表展开 BallTrajectory。
- 保留 API 原值，不做单位换算、坐标变换、插值、落点补齐或时间推算。报告轨迹不是逐帧雷达原始样本；未验证的单位/横向正方向不能用于机器人转换。

### 验收

检查退出码和控制台 `files`、`shot_count`、`ball_point_count`、`reports`、`warnings`。脚本写出后已回读验证 CSV 全部单元格；对于修改，还应核对：

1. 结果行数等于源 Strokes 总数，不能计入表头。轨迹行数等于两版本 BallTrajectory 点数总和。
2. 每个 `(ExportId, ShotKey, DataSpace)` 的点行数与对应 Raw/NormalizedBallTrajectoryPointCount 相同，PointIndex 从 0 连续，XYZ 与源数组一致。
3. 原始和标准化数据不串用，空值与 0 区分，占位记录仍存在，多分组/多来源无关联歧义。

```powershell
& '<python-executable>' -B '<skill-dir>/scripts/test_export_trackman.py'
```

交付两个 CSV 链接、杆数和点数、重要缺失情况。默认无需再输出 XLSX。若选择 JSON，再读 [references/json-schema.md](references/json-schema.md)。

## 导入 rerun hub 并对齐到杆

### 向用户要什么

| 要什么 | 说明 |
|---|---|
| TrackMan 分享链接 | 一条或多条。让用户自己执行 `export TRACKMAN_URLS='<链接1> <链接2>'`（Windows PowerShell：`$env:TRACKMAN_URLS='<链接1> <链接2>'`），不要让用户贴到对话里 |
| 实验会话 | `dataset`（会话所在 Dataset，如 `indoor_validation`）和 `session_id`（这次实验在 Hub 里的会话 id）。不知道就先用 rerun-hub-data 的 `hub_data.py probe --dataset <dataset>` 按时间找 |
| Hub 地址和口令 | `RERUN_HUB_URL`（`rerun+http://…`）、`RERUN_HUB_HTTP_URL`（`http(s)://…`）、`RERUN_HUB_TOKEN`。**环境和口令的申请方式待平台确定**，现在先找平台（jchen）要；口令让用户自己 `export`，不要贴进对话 |
| 时间窗（一般不用） | 默认用会话自己的开始 / 结束时间；会话没有这两个时间时用 LM 击球记录的时间范围前后各放宽 30 秒；都没有就要用户给 `--window-start/--window-end`（ISO-8601，带时区） |

不需要 LM 的 SN、TrackMan 设备号，也不需要 collect-sdk 或其他平台组件：会话已经确定了是哪台 LM。

### 环境（一次）

```bash
python3 -m pip install "rerun-sdk==0.38.1" pyarrow numpy
```

Windows / Mac / Linux 都可以。`rerun-sdk` 版本必须和 Hub 一致（0.38.1）。

### 执行：先预览，再导入

`<skill-dir>` 是本文件所在目录。

```bash
python3 <skill-dir>/scripts/trackman_hub.py preview --dataset <dataset> --session <session_id>   # 只读，什么都不写
python3 <skill-dir>/scripts/trackman_hub.py import  --dataset <dataset> --session <session_id>   # 写入 Hub
python3 <skill-dir>/scripts/trackman_hub.py shots   --dataset <dataset> --session <session_id>   # LM 数据后来才上传时，只重新配对
```

1. **先 preview**，把结果讲给用户听：
   - `window`：用的时间窗和来源；
   - 每条链接 `in_window` / `outside_window` / `no_timezone_or_time`：窗口外、没有时区的杆**不会**进 Hub 的会话（只留在原始报告里）。窗口外的杆很多，通常是手机和 TrackMan 的时钟差了，或链接挂错了会话，先和用户确认，必要时用 `--window-start/--window-end` 放宽；
   - `counts`：`matched`（配上）、`lm_only`（只有 LM）、`trackman_only`（只有 TrackMan）、`ambiguous`（有几个候选，没定）；
   - `clock_map` 的 `offset_ms`（TrackMan 时钟减 LM 时钟）和 `quality`（`estimated` 至少 3 对估出来的；`coarse` 配对太少，按 0 算）。
2. 用户确认后 **import**。同一会话再导入（比如补一条链接）会把之前挂过的链接一起重新拉取，整层替换，不会重复。
3. 输出是一份 JSON，不含链接和口令。退出码非 0 时把 stderr 的错误告诉用户；口令无权限（401/403）就让用户找会话所在项目的 owner 授权，不要换别人的口令。

配对规则是平台的 `time_nearest_v1`，阈值是建议初值（开机分组 60 s、估偏差窗口 30 s、配对容差 2 s，可用 `--B-s/--W-s/--T-s` 调），**还没用同杆样本定稿**，结果要当作初步结论告诉用户。人工改配对的入口还没有。

### 交付

- dataset / session_id、link_ids、各状态的杆数、`offset_ms` 和 `quality`、有没有 `ambiguous` 需要人工看。
- 让用户在 Hub 控制台打开这个会话看 `trackman`、`shots` 两层，或用 rerun-hub-data 取数：`hub_data.py export --segment <session_id> --index event_time --contents '/shots/**'`。
- 写进 Hub 的具体内容见 [references/hub-import.md](references/hub-import.md)。

TrackMan 接口来自官方网页当前实现，不保证长期稳定。结构变动时保留错误并如实告诉用户，不要猜测成功。

# 训练数据加载（PyTorch）

官方文档：https://rerun.io/docs/howto/train/dataloader 。验证报告：https://pages.addx.ai/infra/ai-data-platform/testing/evidence/2026-09-27-hub-dataloader/index.html（infra/ai-data-platform#43）。

## 状态：Hub ≥ m1-collect-ingest `0f44ab3` 已验证

2026-09-27 在 Hub 上跑了 11 条 L3 用例，sqlite 和 Postgres 都通过，覆盖：
- 数值、窗口、陈旧度；
- map / iterable 两种 Dataset，以及 DataLoader 批次；
- 直连 URL 和 FetchChunks 两条取数路径；
- 鉴权、URL 过期；
- H.264 视频；
- 多 segment 块打乱。

更早的 Hub 版本有两处问题：
- segment table 缺 `<时间线>:start/:end` 列，样本索引建不起来，会报 `No field named "<时间线>:start"`；
- latest-at 忽略 per-segment 取值，返回空值。

办公网预览已升级到该版本并实测过（2026-09-27）。连别的 Hub 时先跑 `hub_data.py probe --dataset <name>`：显示不支持，就先用 `hub_data.py export` 导出 parquet，再用自己的 Dataset 读。

## 和官方文档的差异

- 安装：`pip install "rerun-sdk[catalog,dataloader]==0.38.1"`，CPU 版 torch 即可。
- **必须设 `REDAP_TOKEN`**：worker 会自己新建 `CatalogClient`，不带 `token=`，只认这个环境变量。所以要 `export REDAP_TOKEN="$RERUN_HUB_TOKEN"`，token 限定到要读的数据集。
- timestamp / duration 时间线（`event_time`、`device_mono_ns`）必须给 `timeline_sampling=FixedRateSampling(rate_hz=...)`。
- `Field` 的 path 用 probe 输出里的完整列名，例如 `"/lm/metrics/ball_speed:Scalars:scalars"`。
- 预签名 URL 过期后，SDK 不会退回 FetchChunks，而是直接 403。它每个 block 都会拿新 URL，所以只要一个 block 能在有效期（默认 300 秒）内读完就行。
- 验证只跑了 `num_workers=0`。要多 worker 就用 `spawn` 启动方式，这种情况没测过。
- 某个 segment 在所选时间线上完全没有数据时，SDK 可能生成错误样本。先用 `filter_segments` 排除这类 segment。

## 吞吐基线（`num_workers=0`，batch 32，块打乱，MinIO + Postgres）

| 数据 | 取数路径 | 样本/秒 | 首批延迟 |
|---|---|---|---|
| 8 segment × 60 s × 30 Hz 数值 | 直连 URL | 1117 | 0.37 s |
| 同上 | FetchChunks | 774 | 0.23 s |
| 4 × 30 s、320×240 H.264 | 直连 URL | 7.7 | 15.7 s |
| 同上 | FetchChunks | 9.8 | 9.5 s |

视频是瓶颈，主要耗在单进程解码。训练视频模型先考虑多 worker，或者先把解码好的帧缓存下来。

## 骨架

```python
import os
import rerun as rr
from rerun.experimental.dataloader import DataSource, Field, FixedRateSampling, NumericDecoder, RerunIterableDataset
from torch.utils.data import DataLoader

client = rr.catalog.CatalogClient(os.environ["RERUN_HUB_URL"], token=os.environ["RERUN_HUB_TOKEN"])
source = DataSource(dataset=client.get_dataset(name="launch_monitor.default_internal_user"), segments=["<session_id>"])
ds = RerunIterableDataset(
    source=source,
    index="event_time",
    fields={"ball_speed": Field("/lm/metrics/ball_speed:Scalars:scalars", decode=NumericDecoder())},
    timeline_sampling=FixedRateSampling(rate_hz=30.0),
)
loader = DataLoader(ds, batch_size=32, num_workers=0)
```

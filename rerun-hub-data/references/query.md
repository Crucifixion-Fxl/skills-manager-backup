# 查询 recipe 与差异

API 细节看官方文档：[rerun.catalog 0.38.1](https://ref.rerun.io/docs/python/0.38.1/common/catalog/)、[Query and transform](https://rerun.io/docs/howto/query-and-transform)。以下 recipe 于 2026-09-27 在办公网预览的 v2 演示数据（`launch_monitor.default_internal_user`）上实跑过。

## 和官方文档的差异

| 事项 | 在这里 |
|---|---|
| 安装 | `pip install "rerun-sdk[catalog]==0.38.1"`。不带 `[catalog]` 就没有 datafusion，`CatalogClient` 会直接报错 |
| 顺序 | 先 `filter_segments`，再 `filter_contents`，最后 `reader(index=…)`。不缩范围会拉全部字节 |
| AnyValues 列 | 列名是 `{路径}:{字段}`，值是单元素 list，取值用 `col("/shots:status")[0]` |
| 纳秒时间戳 | 没装 pandas 时，`to_pylist()` 转不了纳秒时间戳，先 `col("event_time").cast(pa.int64())` |
| latest-at（`using_index_values` + `fill_latest_at`） | 需要 Hub ≥ m1 `0f44ab3`。更早的版本行数对，但时间列和值全为空，加上 `filter_contents` 时返回 0 行；旧版本按 R3 的本地写法 |
| `get_index_ranges()` | 需要 Hub ≥ `0f44ab3` |
| 结果类型 | DataFusion DataFrame；`to_arrow_table()` 不需要 pandas |
| 性能 | 服务端是 Python 实现：按 segment 分批，只选需要的列 |

## 公共开头

```python
import os
import numpy as np
import pyarrow as pa
import rerun as rr
from datafusion import col, lit, functions as F

client = rr.catalog.CatalogClient(os.environ["RERUN_HUB_URL"], token=os.environ["RERUN_HUB_TOKEN"])
ds = client.get_dataset(name="launch_monitor.default_internal_user")
```

## R0 会话目录

```python
sessions = ds.segment_table().select(
    "rerun_segment_id", "property:session:started_at", "property:session:record_count"
).to_arrow_table()
```

## R1 在 shots 层筛杆，再取这一杆前后的明细

```python
shots = ds.filter_contents(["/shots"]).reader(index="event_time")
rows = (shots.select(col("rerun_segment_id"),
                     col("event_time").cast(pa.int64()).alias("t_ns"),
                     col("/shots:shot_id")[0].alias("shot_id"),
                     col("/shots:status")[0].alias("status"))
             .filter(col("status") == lit("trackman_only"))      # 生产数据里一般筛 matched
             .sort(col("t_ns").sort())
             .limit(3).to_arrow_table().to_pylist())
seg, t = rows[0]["rerun_segment_id"], np.datetime64(rows[0]["t_ns"], "ns")

detail = (ds.filter_segments([seg]).filter_contents(["/lm/metrics/**", "/trackman/**"])
            .reader(index="event_time")
            .filter((col("event_time") >= lit(t - np.timedelta64(1, "s")))
                    & (col("event_time") < lit(t + np.timedelta64(1, "s")))))
table = detail.to_arrow_table()
```

## R2 导出

```python
import pyarrow.parquet as pq
pq.write_table(table, "shot.parquet")
```

也可以直接用 `scripts/hub_data.py export`，它会同时写 provenance 文件。

## R3 多路数据对齐到固定网格

Hub ≥ `0f44ab3`：

```python
grid = t + np.arange(10) * np.timedelta64(100, "ms")     # timestamp 用 datetime64[ns]，duration 用 int64 纳秒
aligned = ds.filter_segments([seg]).filter_contents(["/trackman/**"]).reader(
    index="event_time", using_index_values={seg: grid}, fill_latest_at=True,
).to_arrow_table()
```

旧版本（服务端 latest-at 返回空值）改为在本地对齐：

```python
import pandas as pd

c = "/trackman/<link_id>/raw/BallSpeed:Scalars:scalars"         # 从 probe 输出里复制
raw = (ds.filter_segments([seg]).filter_contents(["/trackman/**"]).reader(index="event_time")
         .select("event_time", c).to_pandas())
raw = raw[raw[c].notna()].sort_values("event_time")
grid = pd.DataFrame({"event_time": pd.to_datetime(t + np.arange(10) * np.timedelta64(100, "ms"))})
aligned = pd.merge_asof(grid, raw, on="event_time", direction="backward")   # 每个网格点取它之前最近的值
```

有多列时，逐列做 `merge_asof`（每列先去掉空值），再按网格合并。

## R4 链接

```python
url = ds.segment_url(seg, timeline="event_time", start=int(t.astype("int64")),
                     end=int((t + np.timedelta64(2, "s")).astype("int64")))   # 桌面 Viewer：rerun "<url>"
```

网页控制台的链接用 `POST /hub/v1/shares` 生成。两种链接都不含凭据。

## R5 按状态统计杆数

```python
counts = shots.aggregate([col("/shots:status")[0].alias("status")],
                         [F.count(lit(1)).alias("n")]).to_arrow_table().to_pylist()
```

## 数据量（不取数据值）

```bash
curl -s -H "Authorization: Bearer $RERUN_HUB_TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"launch_monitor.default_internal_user","sql":"SELECT segment_id, count(*) AS chunks, sum(byte_size) AS bytes FROM chunks GROUP BY segment_id"}' \
  "$RERUN_HUB_HTTP/hub/v1/sql"      # HTTP 地址（端口 8877），见环境表
```

`chunks` 表的列：`segment_id, layer, chunk_id, entity_path, chunk_is_static, byte_offset, byte_size, log_time_start, log_time_end`。

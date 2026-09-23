# BackfillPolicy（一次性回填）

本文档描述使用 `BackfillPolicy.single_run()` 把多个分区合并到同一次 asset 执行里的模式，适用于大量分区历史数据回填场景。

## 为什么需要

Dagster 默认的回填策略是 **per-partition**：在 UI 选中 N 个分区启动 backfill，Dagster 会发起 N 次独立的 asset run，每次只处理一个分区。

这在大量分区 + 外部 API rate limit 场景下非常危险：

- 回填 3 年 = 1095 个分区 → 1095 次 run 启动开销
- 每次 run 都要走一遍 init（load 依赖、Vault 拉 secret、建 HTTP session）
- 外部 API 的 5req/s 额度在 1095 次短连接里很难复用，容易被上游限流甚至封禁
- 失败重试粒度是"每分区一次"，中间失败大概率留下一地脏状态

`BackfillPolicy.single_run()` 把整个选中区间合并成**一次** asset 调用，函数内部自行切分批次，对外部 API 只发一条连接池 + 自己控频。

## 规则引用

- 资产模板：`[python-asset-template-day.md](python-asset-template-day.md)`
- 幂等写入：`[python-asset-idempotency.md](python-asset-idempotency.md)`（single_run 强烈建议配合 upsert）

## 核心规则

1. 批量回填历史数据（> 30 个分区）时**必须**用 `BackfillPolicy.single_run()`，不要走 per-partition 默认。
2. asset 函数内用 `context.partition_key_range` 拿 `(dt_start, dt_end)`，**不要**用 `context.partition_key`（后者在 multi-partition run 里没意义）。
3. 时间窗/实体切分逻辑写在 asset 内，保证单次 run 能一路跑完；切分粒度要能容忍单批失败（配合 upsert 幂等）。
4. 日常调度（每天增量 1 个分区）`partition_key_range` 的 start==end，逻辑自然退化为单分区。
5. **强烈建议配合 `primary_keys` upsert**：single_run 跑到一半失败时原数据不会丢失；下次 retry 重跑不会产生重复行。

## 完整示例

```python
"""
Python 资产示例（天调度 + 一次性回填 + Run Config）
- 增量场景：每日 cron 跑单天，partition_key_range = (today, today)
- 回填场景：Dagster UI 选中 2023-04-21 ~ 2026-04-16 发起 backfill，
            single_run 合并成一次 asset 调用，partition_key_range = (2023-04-21, 2026-04-16)
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from dagster import (
    AssetExecutionContext,
    AssetKey,
    BackfillPolicy,
    Config,
    DailyPartitionsDefinition,
)
from a4x_dagster.py_jobs.decorator import pyjob_asset
from a4x_dagster.common.iomanager import SeatunnelDatahouseIOManagerRequest

logger = logging.getLogger(__name__)

# 回填起点 + 推迟 5 天（给上游数据源发布窗口）
history_partition_def = DailyPartitionsDefinition(
    start_date="2023-04-21",
    end_offset=-5,
)


class ExampleHistoryConfig(Config):
    points_limit: Optional[int] = None
    days_per_batch: int = 365    # 一次调用外部 API 最多覆盖 1 年
    sleep_seconds: float = 0.3


@pyjob_asset(
    key=AssetKey(["common", "ods_example_history_daily_di"]),
    deps=[],
    partitions_def=history_partition_def,
    backfill_policy=BackfillPolicy.single_run(),   # ← 关键：一次性回填
    cron="0 10 * * *",
    deployment_environments=["us-dev"],
    cpu=1,
    memory=2,
    io_manager_key="seatunnel_datahouse",
    # 强烈建议配合 primary_keys upsert（见 python-asset-idempotency.md）
)
def ods_example_history_daily_di(
    context: AssetExecutionContext,
    config: ExampleHistoryConfig,
):
    import pandas as pd

    dt_start_str, dt_end_str = context.partition_key_range
    dt_start = datetime.strptime(dt_start_str, "%Y-%m-%d").date()
    dt_end = datetime.strptime(dt_end_str, "%Y-%m-%d").date()

    context.log.info(
        f"回填/增量范围: {dt_start} ~ {dt_end} "
        f"(共 {(dt_end - dt_start).days + 1} 天)"
    )

    # 按 days_per_batch 切窗，每段单独调用 API + 写一次 parquet / 逐段 concat
    all_rows = []
    window_start = dt_start
    while window_start <= dt_end:
        window_end = min(
            window_start + timedelta(days=config.days_per_batch - 1),
            dt_end,
        )
        context.log.info(f"处理窗口: {window_start} ~ {window_end}")

        # 原脚本核心逻辑
        # rows = scrape_api(start=window_start, end=window_end,
        #                   points_limit=config.points_limit,
        #                   sleep_seconds=config.sleep_seconds)
        rows = [{"dt": str(window_start), "id": 1}]   # 示例
        all_rows.extend(rows)
        window_start = window_end + timedelta(days=1)

    df = pd.DataFrame(all_rows)
    if df.empty:
        return None
    return SeatunnelDatahouseIOManagerRequest(data=df)


if __name__ == "__main__":
    from dagster import DagsterInstance, PartitionKeyRange, build_asset_context

    logging.basicConfig(level=logging.INFO)
    context = build_asset_context(
        instance=DagsterInstance.ephemeral(),
        partition_key_range=PartitionKeyRange(start="2026-03-20", end="2026-03-22"),
    )
    print(ods_example_history_daily_di(context=context, config=ExampleHistoryConfig()))
```

## 行为差异对照表

| 场景 | 默认 per-partition | `BackfillPolicy.single_run()` |
|---|---|---|
| 选中 1 个分区启动 | 1 次 run，处理 1 分区 | 1 次 run，`partition_key_range=(d,d)` |
| 选中 N 个分区启动 | N 次独立 run | 1 次 run，`partition_key_range=(d_start,d_end)` |
| asset 内 API 调用量 | N 次 init + N 次连接 | 1 次 init，内部自行控频 |
| 失败重试粒度 | 单分区 | 整段区间（但配合 upsert 幂等无副作用） |
| 日志定位 | 每分区独立 log | 一条 log 看整段 |
| UI 进度 | N 个独立 tile | 1 个合并 tile |

## 仓内参考实例

- `a4x_dagster/py_jobs/_assets/weather/weather_history.py`（3 年历史 + 每日增量，一套代码通吃）

## 常见坑

| 现象 | 原因 | 修复 |
|---|---|---|
| `context.partition_key` raise 或拿到奇怪的值 | single_run 下没有"单一 partition_key"概念 | 改用 `context.partition_key_range`，拆出 `dt_start, dt_end` |
| 回填到一半失败，下次重试产生重复行 | 默认 append 语义不幂等 | 配合 `primary_keys=[..., "dt"]` upsert（见 idempotency.md） |
| 一次 run 跑太久被 k8s OOMKilled | 整段 3 年数据在内存里 concat | asset 内按窗口分批写 parquet，或拆 asset 粒度（每年一个 window） |
| 日常增量（单天）也用 single_run | 没问题，逻辑会自然退化为单分区 | 保持 `backfill_policy=single_run()` 即可 |

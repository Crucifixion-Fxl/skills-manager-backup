# Python 资产模板（天调度）

本文档提供天调度场景下的 Dagster Python 资产模板。  
适用条件：已明确 `partitions_def=day_partition_def`。

## 规则引用

- 调度规则：`[scheduling-definition.md](scheduling-definition.md)`
- 部署环境规则：`[deployment-environment-definition.md](deployment-environment-definition.md)`
- Asset Key 规则：`[asset-key-definition.md](asset-key-definition.md)`

## dt 字段规范

- 仅适用于数据资产：
  - 输出数据必须显式包含 `dt` 字段
  - 若原脚本结果不包含 `dt`，默认使用 `partition_key[0]` 作为 `dt`
- 天调度格式：
  - `dt` 必须是 `yyyy-MM-dd`
- 实施方式：
  - 接入代码按调度类型推断并补齐 `dt`
  - 不需要额外编写 `dt` 格式校验逻辑（由 io-manager 统一校验）
- 非数据资产（仅调度任务）：
  - 不要求 `dt` 字段
  - `data` 可为 `None`，或直接 `return None` 跳过入仓

## 命名一致性规范

- 数据资产命名建议：`ods_<source_or_function>_<df|di>`
- `asset_name`（文件名去掉 `.py`）必须与以下内容完全一致：
  - `key[1]`（资产标识）
  - 资产函数名
- 本模板示例：`asset_name = ods_example_df`

## 进阶模式

- 需要 runtime 传参？→ 参考 [python-asset-run-config.md](python-asset-run-config.md)
- 要回填大量历史分区？→ 参考 [python-asset-backfill.md](python-asset-backfill.md)
- 数据需要幂等（重跑不重复）？→ 参考 [python-asset-idempotency.md](python-asset-idempotency.md)
- 多个相关 asset 共享逻辑？→ 参考 [python-asset-multi-file.md](python-asset-multi-file.md)

## 模板代码

```python
"""
Python资产示例（天调度，asset_name=ods_example_df）
场景：通过python脚本实现的数据处理（有数据则入仓，无数据则跳过入仓）
"""
import logging

from a4x_dagster.common.partition_def import day_partition_def
from dagster import AssetExecutionContext, AssetKey
from a4x_dagster.py_jobs.decorator import pyjob_asset
from a4x_dagster.common.iomanager import SeatunnelDatahouseIOManagerRequest

logger = logging.getLogger(__name__)


@pyjob_asset(
    key=AssetKey(["common", "ods_example_df"]),
    deps=[],
    partitions_def=day_partition_def,
    cron="5 1 * * *",  # 每天 01:05
    deployment_environments=["us-dev"],
    cpu=1,
    memory=1,
    # 仅在存在可入仓数据时保留 io_manager_key
    io_manager_key="seatunnel_datahouse",
)
def ods_example_df(context: AssetExecutionContext):
    import pandas as pd

    # 获取本次待处理的时间分区
    partition_keys = context.partition_keys  # yyyy-MM-dd
    # 示例：当前时间是 2026-04-22 01:05:00 ，
    # 则 partition_key=2026-04-21 （T+1原则）
    # 含义：表示当前任务处理的是2026年04月21日的数据
    partition_key = partition_keys[0]
    logger.info(f"当前任务处理事件分区范围：{partition_key}")
    context.log.info(f"当前任务处理事件分区范围：{partition_key}")

    # 数据资产：若原脚本已有 dt 字段，沿用原值；若无 dt，则默认使用 partition_key
    dt = str(partition_key)  # 天调度 dt 推断值：yyyy-MM-dd（格式校验由 io-manager 执行）
    data = {"dt": [dt, dt, dt], "id": [10, 20, 30]}
    df = pd.DataFrame(data)

    # 若脚本无数据产出（例如非数据资产），可直接返回 None，跳过数据入仓
    if df.empty:
        return None

    return SeatunnelDatahouseIOManagerRequest(data=df)
    # 非数据资产也可返回：
    # return SeatunnelDatahouseIOManagerRequest(data=None)


if __name__ == "__main__":
    from dagster import DagsterInstance, PartitionKeyRange, build_asset_context

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    context = build_asset_context(
        instance=DagsterInstance.ephemeral(),
        partition_key_range=PartitionKeyRange(
            start="2026-03-20",
            end="2026-03-20",
        ),
    )
    print(ods_example_df(context=context))
```

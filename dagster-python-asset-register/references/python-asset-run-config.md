# Run Config（运行时参数化）

本文档描述 `@pyjob_asset` 的 Run Config 模式：在 asset 函数签名中加入 `config: FooConfig` 参数，Dagster Launchpad 与 Backfill 表单会自动显示字段，允许运行时覆盖值。

## 何时使用

- **staging / prod 差异化**：同一份代码，staging 传 `points_limit=1` 跑单点验证，生产不传走默认全量。
- **灰度控制**：对外部 API 灰度、限 batch 大小等场景，运营在 UI 改值即可，不改代码。
- **一次性任务参数**：回填某段时间的实验 id、Run 时临时切换 endpoint/api key。
- **避免"改一行 config 提 MR + merge + deploy"的延迟链路**。

## 规则引用

- 资产模板：`[python-asset-template-day.md](python-asset-template-day.md)` / `[python-asset-template-hour.md](python-asset-template-hour.md)`
- 回填一次性执行：`[python-asset-backfill.md](python-asset-backfill.md)`

## 核心规则

1. `class FooConfig(Config)` 定义字段，类型要明确；可选字段用 `Optional[T] = None` 或给默认值。
2. asset 函数签名加 `config: FooConfig` 参数，位置在 `context` 之后。
3. **不要**把 secret/token 写进 Config 字段——继续走 Vault / 环境变量。
4. Config 字段建议粒度小、语义明确，避免一个字段塞多个开关。
5. 设默认值时尽量让默认值 == "生产正常运行"，这样日常定时调度不需要在 UI 覆盖任何值。

## 完整示例（天调度 + Run Config）

```python
"""
Python 资产示例（天调度，asset_name=ods_example_df，带 Run Config）
"""
import logging
from typing import Optional

from common import day_partition_def
from dagster import AssetExecutionContext, AssetKey, Config
from a4x_dagster.py_jobs.decorator import pyjob_asset
from a4x_dagster.common.iomanager import SeatunnelDatahouseIOManagerRequest

logger = logging.getLogger(__name__)


class ExampleConfig(Config):
    """Run config — override via Dagster launchpad or backfill form.

    staging 验证用 points_limit=1 跑单点；生产不覆盖走默认全量。
    """
    points_limit: Optional[int] = None    # None = 全量；传 1 做单点 smoke
    points_per_batch: int = 50            # 每批大小，灰度/限流时可调小
    sleep_seconds: float = 0.3            # API 调用间隔（rate limit 保险）


@pyjob_asset(
    key=AssetKey(["common", "ods_example_df"]),
    deps=[],
    partitions_def=day_partition_def,
    cron="5 1 * * *",
    deployment_environments=["us-dev"],
    cpu=1,
    memory=1,
    io_manager_key="seatunnel_datahouse",
)
def ods_example_df(
    context: AssetExecutionContext,
    config: ExampleConfig,   # ← 关键：加这个参数
):
    import pandas as pd

    dt_start, dt_end = context.partition_key_range
    context.log.info(
        f"dt=[{dt_start},{dt_end}] "
        f"points_limit={config.points_limit} "
        f"batch={config.points_per_batch}"
    )

    # 原脚本核心逻辑调用时把 config 参数透传下去
    # rows = scrape(points_limit=config.points_limit, batch=config.points_per_batch, ...)
    rows = [{"dt": dt_end, "id": i} for i in range(config.points_per_batch)]
    if config.points_limit is not None:
        rows = rows[: config.points_limit]

    df = pd.DataFrame(rows)
    if df.empty:
        return None
    return SeatunnelDatahouseIOManagerRequest(data=df)


if __name__ == "__main__":
    from dagster import DagsterInstance, PartitionKeyRange, build_asset_context

    logging.basicConfig(level=logging.INFO)
    context = build_asset_context(
        instance=DagsterInstance.ephemeral(),
        partition_key_range=PartitionKeyRange(start="2026-03-20", end="2026-03-20"),
    )
    # 本地调试时直接构造 Config 实例
    print(ods_example_df(context=context, config=ExampleConfig(points_limit=1)))
```

## 在 Dagster UI 里传 Config

### Launchpad（手动触发单次运行）

Launchpad 会根据 Config 字段自动生成 YAML 表单，示例：

```yaml
ops:
  ods_example_df:
    config:
      points_limit: 1
      points_per_batch: 10
      sleep_seconds: 0.5
```

### Backfill 表单（批量回填）

选择分区范围后，在"Backfill configuration"里同样填入上述 YAML，整段回填共用一份 config（配合 `BackfillPolicy.single_run()` 效果最好）。

## 仓内参考实例

- `a4x_dagster/py_jobs/_assets/customer_care/ods_smart_popup_dryrun_di.py`：`DryRunConfig` 传入 `config_json`、`rules_version`、`user_ids`、`sample_rate`，运营在 Admin 后台触发 DryRun 任务时通过 Dagster run config 传入。

## 常见坑

| 现象 | 原因 | 修复 |
|---|---|---|
| 本地 `__main__` 报 `config` missing positional arg | 忘了构造 `FooConfig(...)` 实例 | 显式传 `config=FooConfig(...)` |
| Launchpad 不显示 config 字段 | Config 类没继承 `dagster.Config` 或字段没类型注解 | 继承 `Config`，每个字段都写类型 |
| Backfill 时 Config 被应用到每个分区但只想传一次 | 默认 per-partition 策略会对每分区都发起一次 run，即使 config 相同也会展开多次 | 配合 `BackfillPolicy.single_run()`，一次 run 覆盖整段 range |
| 敏感值被意外暴露在 Dagster UI Run 记录 | 把 token/secret 当成 Config 字段 | 继续走 Vault / 环境变量，Config 只放业务参数 |

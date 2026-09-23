# 多文件 asset 目录组织（`_common.py`）

本文档说明当同一业务域有 ≥2 个相关 asset 需要共享逻辑时，如何用目录 + `_common.py` 模式组织代码，取代单文件 asset 模板。

## 适用场景

- 一个业务域有多个相关 asset（history + forecast、snapshot + increment、ods + dwd 预处理）。
- 它们共享同一个 HTTP 客户端 / Athena 查询 / parquet 落盘工具函数 / 常量。
- 不想：
  - 每个 asset 文件复制粘贴 50 行相同的客户端代码
  - 把共享逻辑放到 `a4x_dagster/common/` 里污染全局命名空间
  - 跨 `_assets/<a>` 和 `_assets/<b>` 互相 import

## 规则引用

- 资产模板：`[python-asset-template-day.md](python-asset-template-day.md)` / `[python-asset-template-hour.md](python-asset-template-hour.md)`
- SKILL.md 红线 #8：同一业务域 ≥2 个 asset 需要共享逻辑时，必须用 `_common.py` 而非跨目录 import 或复制粘贴。

## 推荐目录结构

```
a4x_dagster/py_jobs/_assets/<domain>/
├── __init__.py          # 空文件（保证 domain 是 package）
├── _common.py           # 共享逻辑：客户端、工具函数、常量、dataclass
├── <asset_a>.py         # 一个 @pyjob_asset
└── <asset_b>.py         # 另一个 @pyjob_asset
```

## 命名/组织约定

1. `_common.py` 用**下划线开头**——`py_jobs/assets.py` 的自动发现会跳过 `_` 开头的模块，避免被当成 asset 注册。
2. `_common.py` 里**不放** `@pyjob_asset`，只放：
   - HTTP/DB 客户端构造函数（返回带连接池的 session）
   - 数据拉取与落盘的纯函数（参数化 dt_start / dt_end / config）
   - 公用常量（API endpoint、batch size 默认值、字段类型映射）
   - dataclass / TypedDict（row 结构定义）
3. `_common.py` 可以 `from a4x_dagster...` import 框架组件（partition_def、IOManager 等），但不要写副作用代码（如模块级别的 `requests.get()`）。
4. asset 文件 `<asset_x>.py` 里 `from ._common import ...` 使用相对 import。
5. 每个 asset 文件保留独立可跑的 `__main__`，共享 `_common.py` 里的函数做 smoke test。
6. **共享逻辑不写全局状态**——全局变量、module-level cache 会在 Dagster worker 里行为不可预测。如需缓存，用函数参数传。

## 完整示例

### `_assets/weather/_common.py`

```python
"""weather 域共享组件：API 客户端 + zipcode 点位加载 + parquet 落盘。"""
from dataclasses import dataclass
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class GeoPoint:
    zipcode: str
    lat: float
    lon: float


def build_openmeteo_client(timeout: float = 30.0):
    """构造带连接池的 requests.Session，供 history/forecast 两个 asset 共用。"""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10))
    session.request = _wrap_timeout(session.request, timeout)
    return session


def _wrap_timeout(fn, timeout):
    def wrapped(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return fn(method, url, **kwargs)
    return wrapped


def load_zipcode_points(limit: Optional[int] = None) -> List[GeoPoint]:
    """通过 Athena 查 zipcode 坐标点位表。"""
    # from a4x_dagster.common.athena import athena_query
    # sql = "select zipcode, lat, lon from analytics.dim_zipcode_point"
    # rows = athena_query(sql)
    # points = [GeoPoint(**r) for r in rows]
    points = [GeoPoint("10001", 40.75, -73.99)]    # 示例
    return points[:limit] if limit else points


def scrape_archive(session, point: GeoPoint, dt_start: str, dt_end: str, sleep_seconds: float = 0.3):
    """拉取历史天气，返回 row list。"""
    # resp = session.get("https://archive-api.open-meteo.com/v1/archive",
    #                    params={"latitude": point.lat, "longitude": point.lon,
    #                            "start_date": dt_start, "end_date": dt_end, ...})
    # rows = resp.json()["daily"]
    import time
    time.sleep(sleep_seconds)
    return [{"dt": dt_end, "zipcode": point.zipcode, "temp_max": 25.0}]
```

### `_assets/weather/weather_history.py`

```python
"""天气历史回填 + 每日增量（single_run + upsert）。"""
import logging
from typing import Optional

from dagster import (
    AssetExecutionContext, AssetKey, BackfillPolicy, Config, DailyPartitionsDefinition,
)
from a4x_dagster.py_jobs.decorator import pyjob_asset
from a4x_dagster.common.iomanager import SeatunnelDatahouseIOManagerRequest

from ._common import build_openmeteo_client, load_zipcode_points, scrape_archive

logger = logging.getLogger(__name__)


class WeatherHistoryConfig(Config):
    points_limit: Optional[int] = None


@pyjob_asset(
    key=AssetKey(["common", "ods_weather_history_daily_di"]),
    partitions_def=DailyPartitionsDefinition(start_date="2023-04-21", end_offset=-5),
    backfill_policy=BackfillPolicy.single_run(),
    cron="0 10 * * *",
    deployment_environments=["us-dev"],
    cpu=1, memory=2,
    io_manager_key="seatunnel_datahouse",
)
def ods_weather_history_daily_di(
    context: AssetExecutionContext,
    config: WeatherHistoryConfig,
):
    import pandas as pd
    dt_start, dt_end = context.partition_key_range

    session = build_openmeteo_client()
    points = load_zipcode_points(limit=config.points_limit)
    rows = [r for p in points for r in scrape_archive(session, p, dt_start, dt_end)]

    df = pd.DataFrame(rows)
    if df.empty:
        return None
    # 选 upsert：3 年历史 + single_run，中断重跑安全
    return SeatunnelDatahouseIOManagerRequest(data=df, primary_keys=["zipcode", "dt"])
```

### `_assets/weather/weather_forecast.py`

```python
"""天气 16 天预报（每日刷新，无历史回填）。"""
from typing import Optional
from dagster import AssetExecutionContext, AssetKey, Config
from a4x_dagster.py_jobs.decorator import pyjob_asset
from a4x_dagster.common.iomanager import SeatunnelDatahouseIOManagerRequest
from a4x_dagster.common.partition_def import day_partition_def

from ._common import build_openmeteo_client, load_zipcode_points

class WeatherForecastConfig(Config):
    points_limit: Optional[int] = None


@pyjob_asset(
    key=AssetKey(["common", "ods_weather_forecast_daily_di"]),
    partitions_def=day_partition_def,
    cron="30 6 * * *",
    deployment_environments=["us-dev"],
    cpu=1, memory=1,
    io_manager_key="seatunnel_datahouse",
)
def ods_weather_forecast_daily_di(
    context: AssetExecutionContext,
    config: WeatherForecastConfig,
):
    import pandas as pd
    dt_start, dt_end = context.partition_key_range

    session = build_openmeteo_client()
    points = load_zipcode_points(limit=config.points_limit)
    # forecast 的 scrape 函数也在 _common.py 里定义，此处省略
    rows = []   # = [r for p in points for r in scrape_forecast(session, p, dt_end)]

    df = pd.DataFrame(rows)
    if df.empty:
        return None
    # 选 pre_sql：预报每天覆盖刷新，读性能优先，不需要 upsert
    pre_sql = "delete from {asset_table} where dt = date('{dt_end}')"
    return SeatunnelDatahouseIOManagerRequest(data=df, pre_sql=pre_sql)
```

## 与单文件 asset 模板的关系

| 场景 | 模板选择 |
|---|---|
| 单个独立 asset，没有其他兄弟 asset 共享代码 | `python-asset-template-day.md` / `-hour.md` 单文件模板 |
| ≥2 个 asset 共享客户端/工具函数 | 本文档的多文件 `_common.py` 模式 |
| 从单文件演化到多文件 | 抽出 `_common.py`，改 `from ._common import ...`；原单文件变目录 |

## 常见坑

| 现象 | 原因 | 修复 |
|---|---|---|
| `_common.py` 被当成 asset 注册 | 文件没以 `_` 开头 | 改名 `_common.py` |
| 相对 import `from ._common` 失败 | 目录里缺 `__init__.py` | 建空的 `__init__.py` |
| Dagster asset 发现不到新目录 | `a4x_dagster/py_jobs/assets.py` 的 scan 根目录没覆盖到 | 检查 scan 规则；一般 `_assets/<domain>/*.py` 自动被发现 |
| 两个 asset 的 `_common` 函数互相耦合太紧 | 共享逻辑和 asset 业务边界没分清 | 共享函数只接纯参数（dt_start、config 字段），不依赖 asset context |
| worker 间 `_common` 里的 module-level 变量状态不一致 | 写了全局状态 | 改成函数返回值传参，或用 `functools.lru_cache` 明确作用域 |

# 数据幂等（primary_keys vs pre_sql）

本文档说明 Seatunnel IOManager 写入 Iceberg 的两种幂等模式选型决策。**默认 append 语义不幂等**，同一分区重跑会产生重复行——所有数据资产必须显式选择一种幂等模式。

## 默认行为警告

```python
# ⚠️ 默认：追加写入，同一分区重跑会产生重复行
return SeatunnelDatahouseIOManagerRequest(data=df)
```

源码证据：

- `a4x_dagster/sync_jobs/seatunnel/connector/iceberg.py:72`
  ```python
  DEFAULT_ICEBERG_DATA_SAVE_MODE = EnumDataSaveMode.APPEND_DATA
  ```
- `a4x_dagster/sync_jobs/seatunnel/connector/iceberg.py:187-195`：`default_datahouse_config` 检测到 `primary_keys` 包含分区键 `dt` 时才自动 `iceberg_upsert = True`，否则继续走 append。

## 规则引用

- 资产模板：`[python-asset-template-day.md](python-asset-template-day.md)`
- 回填：`[python-asset-backfill.md](python-asset-backfill.md)`
- SKILL.md 红线 #7：数据资产必须明确选 upsert 或 pre_sql 其一，并在代码注释里写原因。

## 两种幂等模式

### 模式 A — Upsert by primary_keys

```python
return SeatunnelDatahouseIOManagerRequest(
    data=df,
    primary_keys=["user_id", "scene_id", "dt"],   # 必须包含分区键 dt
)
```

**机制**：Iceberg 表开启 upsert-mode，写入时通过 equality delete file 标记旧行，append 新行，读时合并。`dt` 必须在 primary_keys 里，才会触发自动 upsert（源码 iceberg.py:113-120）。

**特点**：
- 失败安全：写入中断不会删除旧数据（delete 是逻辑标记，commit 事务性）
- 读性能差 2-5x：每次查询要合并 equality delete file
- 适合：大量历史数据回填、有天然唯一键、对失败恢复要求高

### 模式 B — Partition overwrite via pre_sql

```python
pre_sql = "delete from {asset_table} where dt = date('{dt_end}')"
# 小时调度用：
# pre_sql = "delete from {asset_table} where dt = date_parse('{dt_end}:00', '%Y-%m-%d-%H:%i:%s')"

return SeatunnelDatahouseIOManagerRequest(
    data=df,
    pre_sql=pre_sql,
)
```

**机制**：Seatunnel 在写入前先通过 Athena 执行 delete 语句清空当前分区，再 append 新数据。

源码：`a4x_dagster/common/iomanager/seatunnel.py:227-242` `_execute_pre_sql`。

**特点**：
- 读性能好：就是普通 Iceberg append 表
- 失败不安全：delete 执行了但后续 append 失败 → 分区变空
- 适合：每日增量、无天然 PK、追求读性能、失败可接受手动重跑

## 决策表

| 业务诉求 | 推荐模式 |
|---|---|
| 回填大量历史分区（> 30 天） | **A (upsert)**——single_run 中断重跑安全 |
| 数据有天然唯一键（user_id / device_id / order_id） | **A (upsert)** |
| 每日增量追加，历史不变 | **B (pre_sql)**——读性能优先 |
| 无天然 PK，只能靠 dt 去重 | **B (pre_sql)** |
| 查询频繁 + 有 BI dashboard 要求读延迟 | **B (pre_sql)** |
| 失败静默污染数据不可接受（财务、合规） | **A (upsert)** |

## 仓内参考实例

- **Upsert 模式**：配合 `BackfillPolicy.single_run()` 的历史回填场景（见 `python-asset-backfill.md`）。
- **pre_sql 模式**：`a4x_dagster/py_jobs/_assets/scrapers/lingxing/lingxing.py:540-546` 每日增量场景示范：
  ```python
  if ':' in dt_end:
      pre_sql = "delete from {asset_table} where dt = date_parse('{dt_end}:00', '%Y-%m-%d-%H:%i:%s')"
  else:
      pre_sql = "delete from {asset_table} where dt = date('{dt_end}')"
  req = SeatunnelDatahouseIOManagerRequest(data=req, pre_sql=pre_sql)
  ```

## 选型的代码注释要求（红线 #7）

无论选哪种，必须在返回 `SeatunnelDatahouseIOManagerRequest` 附近写注释说明原因：

```python
# 选 primary_keys upsert：3 年历史回填 + single_run，失败中断不能丢旧数据。
# 读性能损失可接受（表主要给离线分析用，无实时 BI 查询）。
return SeatunnelDatahouseIOManagerRequest(
    data=df,
    primary_keys=["zipcode", "dt"],
)
```

```python
# 选 pre_sql：日增量，有 Superset dashboard 查历史，读性能优先。
# 失败手动重跑可接受；dt 字段已经是天然分区键无需额外 PK。
pre_sql = "delete from {asset_table} where dt = date('{dt_end}')"
return SeatunnelDatahouseIOManagerRequest(data=df, pre_sql=pre_sql)
```

## 常见坑

| 现象 | 原因 | 修复 |
|---|---|---|
| 重跑产生重复行 | 默认 append，没配 primary_keys 也没 pre_sql | 按决策表选一种幂等模式 |
| upsert 了但读查询还是有重复 | `primary_keys` 没包含 `dt` → 没触发自动 upsert | 把 `dt` 加进 primary_keys 列表 |
| pre_sql 执行了 asset 失败，分区变空 | delete 已提交但 append 失败 | 重跑 asset（pre_sql 会再次 delete + append）；或对关键表改用 upsert 模式 |
| 小时调度 pre_sql 语法错误 | 用了 `date('{dt_end}')`（天格式） | 小时场景用 `date_parse('{dt_end}:00', '%Y-%m-%d-%H:%i:%s')` |
| upsert 表读延迟明显变慢 | equality delete file 累积太多 | 定期 compaction / rewrite_data_files；或改回 pre_sql 模式 |

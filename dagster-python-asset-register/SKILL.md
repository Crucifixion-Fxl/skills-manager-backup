---
name: dagster-python-asset-register
description: 将已知 Python 脚本接入 Dagster 项目并注册为 Dagster 资产（仅支持 DATA/dbt）。当用户提到“注册 dagster asset”“按 references/python-asset-template-day.md 或 python-asset-template-hour.md 接入 pyjob_asset”“把脚本定时入仓”时使用。
---

# dagster-python-asset-register

将已有 Python 脚本改造为 `@pyjob_asset`，并通过 `a4x_dagster/py_jobs/_assets` 自动发现注册。

## Description

适用场景：

- 已有 Python 脚本，需要纳入 Dagster 调度
- 需要按天/小时分区运行
- 需要将数据资产入仓，或将非数据资产仅用于调度执行

前置条件：

| 环境变量 | 是否必需 | 说明 |
|---|---|---|
| `DBT_REPO_DIR` | 必需 | 必须指向本地 `DATA/dbt` 仓库 |
| `A4X_DAGSTER_VAULT_ACCESS_TOKEN` | 可选 | 本地调试脚本时会检查；未设置则跳过相关调试步骤 |

- 环境准备建议先执行 `dagster-project-prepare`

范围限制：

- 仅支持 `DATA/dbt` Dagster 项目

关键输出：

- 新增资产文件：`a4x_dagster/py_jobs/_assets/<asset_file>.py`
- 资产元信息配置完整（`key/deps/partitions_def/cron/deployment_environments/cpu/memory`）
- 资产可被 `a4x_dagster.py_jobs.assets` 正常发现
- 回填场景必须显式配置 `backfill_policy=BackfillPolicy.single_run()` 并在 asset 内按 `context.partition_key_range` 处理整段区间

触发词：

注册 dagster asset、接入 pyjob_asset、把脚本定时入仓、接入 Dagster 资产、把 Python 脚本改造成 Dagster asset

## 输入契约

执行前确认以下输入；缺失则先询问用户：

- 脚本路径或模块入口（必填）
- 资产文件名（必填）
- 资产类型由脚本意图推断：
  - 数据资产：脚本产出并交由 Dagster 入仓的数据
  - 非数据资产：仅调度任务，不产出入仓数据
  - 若无法直接判断，必须询问调用者：
    1) 脚本是否会产生数据？
    2) 产生的数据是否需要写入数仓？
  - 若回答为“会产生数据且需要写入数仓”，判定为数据资产
  - 若仅使用调度功能且不在 Dagster 产生任何数据，判定为非数据资产
- 调度周期（天/小时，必填）
- `cron`（必填，5 段表达式）
- `deployment_environments`（必填，格式 `<region>[-<env>]`）
- `key`（必填）：`[business_domain, asset_identifier]`
- `deps`（可空，但需明确）
- `dt` 策略（数据资产必填）：原脚本是否显式返回 `dt`，若无则使用 `partition_keys[0]` 推断
- 数据产出策略（必填）：有数据入仓；无数据 `return None`
- 完成以上输入确认后，先完成改造与调试；验证通过后再询问用户是否需要创建分支 `feat/<asset-name>` 并推送（可选）

## Rules

### 核心原则

1. 仅在 `DATA/dbt` 范围内执行
2. 先校验后改动，失败即停止
3. 资产函数与 `__main__` 复用同一核心逻辑
4. 验证阶段 Python 命令统一使用 `uv run python`
5. 回填历史数据（>30 个分区）时**必须**使用 `BackfillPolicy.single_run()`，避免按分区循环调用外部 API 耗尽额度。

### 红线

1. 禁止覆盖同名资产文件（除非用户明确确认）
2. 禁止在信息缺失时默认拍板（调度、部署、key）
3. 禁止在验证阶段直接使用系统 `python`
4. 禁止跳过 `a4x_dagster.py_jobs.assets` 的 import 校验
5. 禁止绕过 io-manager 的 `dt` 校验（数据资产场景）
6. 禁止在代码中明文暴露任何密钥信息（如 token、access key、secret）
7. 禁止对幂等性要求高的数据资产直接用默认 append 写入；必须明确选用 `primary_keys=[...,"dt"]` upsert 或 `pre_sql` 分区 delete 两种模式之一，并在代码注释里写明选型原因。
8. 同一业务域 ≥2 个 asset 需要共享逻辑时，使用 `_assets/<domain>/_common.py` 而非跨目录 import 或复制粘贴。

### 实施流程（Phase 1-5）

- Phase 1：前置检查与输入契约确认（Rule 1）
- Phase 2：按调度模板创建资产文件（Rule 2）
- Phase 3：配置元信息并迁移脚本逻辑（Rule 3 + Rule 4）
- Phase 4：可用性验证（Rule 5）
- Phase 5：可选创建分支并推送（Rule 6）

### Rule 1 - 前置检查与输入确认

先校验 `DBT_REPO_DIR` 与项目结构：

- `a4x_dagster/pyproject.toml` 存在
- `git remote origin` 指向 `DATA/dbt`

```powershell
$env:DBT_REPO_DIR
Test-Path "$env:DBT_REPO_DIR/a4x_dagster/pyproject.toml"
$origin = git -C $env:DBT_REPO_DIR remote get-url origin
$origin -match "DATA/dbt(\.git)?$"
```

### Rule 2 - 创建资产文件与命名

模板选择：

- 天调度：`references/python-asset-template-day.md`
- 小时调度：`references/python-asset-template-hour.md`
- 未明确调度周期：先询问用户

复制模板为目标文件后改造：

- 目标路径：`a4x_dagster/py_jobs/_assets/<asset_file>.py`
- 若同名文件已存在：停止并让用户确认

命名规则：

- 资产类型判定（必要时询问调用者）：
  - “会产生数据且需要写入数仓” => 数据资产
  - “仅调度功能，不在 Dagster 产生任何数据” => 非数据资产
- 数据资产：
  - 文件名：`ods_<source_or_function>_<df|di|hf|hi>.py`
  - 例如：`ods_aws_bill_di.py`
- 非数据资产（仅调度任务）：
  - 文件名：`<function_name>.py`
  - 例如：`clear_db_cache.py`

### Rule 3 - 配置资产元信息

`key` 规则（见 `references/asset-key-definition.md`）：

- `key = [business_domain, asset_identifier]`
- 数据资产：
  - `key[0]` 必须来自白名单业务域
  - `key[1]` 遵循 `ods_<source_or_function>_<df|di|hf|hi>`
- 非数据资产：
  - `key[1]` 直接使用功能名（如 `clear_db_cache`）
  - 示例：`AssetKey(["a4xmanage", "clear_db_cache"])`（展示为 `a4xmanage.clear_db_cache`）

其他参数：

- `deps`：上游依赖资产
- 调度：`partitions_def` 与 `cron` 按 `references/scheduling-definition.md`
- 部署：`deployment_environments` 按 `references/deployment-environment-definition.md`
- 资源：`cpu`、`memory`
- `io_manager_key`：仅数据资产时设置 `seatunnel_datahouse`

### Rule 4 - 接入脚本逻辑

1. 从 `context.partition_keys` 获取执行的目标分区 `partition_keys`
2. 调用原脚本核心逻辑
3. `dt` 处理：
   - 数据资产必须显式返回 `dt`
   - 若原脚本无 `dt`：
    - 天调度：`dt = partition_keys[0]`（`yyyy-MM-dd`）
    - 小时调度：`dt = partition_keys[0][:13]`（`yyyy-MM-dd-HH`）
   - 不需要额外写格式判断代码（io-manager 会校验）
   - 非数据资产不要求 `dt`
4. 返回策略：
   - 数据资产：返回 `SeatunnelDatahouseIOManagerRequest(data=...)`
   - 非数据资产：`return None`（跳过入仓）
5. 保留 `__main__` 调试入口

### Rule 5 - 可用性验证

```powershell
Set-Location "$env:DBT_REPO_DIR/a4x_dagster"
uv sync --extra dev

if (-not [string]::IsNullOrWhiteSpace($env:A4X_DAGSTER_VAULT_ACCESS_TOKEN)) {
  uv run python py_jobs/_assets/<asset_file>.py --dt-start <yyyy-MM-dd> --dt-end <yyyy-MM-dd>
}

uv run python -c "import a4x_dagster.py_jobs.assets as _; print('asset discovery import ok')"
```

### Rule 6 - 可选：创建分支并推送

在 Rule 5 验证通过后，先询问用户是否需要创建并推送功能分支。

- 若用户选择“需要”：
  - 创建分支：`feat/<asset-name>`
  - 提交并推送当前改动
- 若用户选择“不需要”：
  - 不执行 Git 分支/提交/推送操作，保留本地改动

```powershell
Set-Location $env:DBT_REPO_DIR
git status --short
git checkout -b feat/<asset-name>
git add a4x_dagster/py_jobs/_assets/<asset_file>.py
git commit -m "feat: register <asset-name> dagster asset"
git push -u origin feat/<asset-name>
```

### Rule 7 - 常见陷阱与修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 同名文件被覆盖 | 写入前未检查 | 先检查，存在即停止并确认 |
| `key[0]` 不合规 | 未按白名单约束 | 按 `asset-key-definition.md` 选择并确认 |
| `key[1]` 命名错误 | 未区分数据/非数据资产 | 数据资产用 `ods_*`，非数据资产用功能名 |
| `partitions_def` 与 `cron` 冲突 | 粒度不一致 | 按 `scheduling-definition.md` 修正 |
| 部署环境误填 | 未确认区域/环境 | 先让用户选择，禁止默认值 |
| 有数据但未入仓 | 漏配 `io_manager_key` | 数据入仓场景设置 `seatunnel_datahouse` |
| 无数据仍尝试入仓 | 返回策略错误 | 直接 `return None` |

## Examples

### Good

```text
把 aws_bill_sync.py 接入 Dagster，按天增量调度，命名 ods_aws_bill_di，cron=0 2 * * *，部署 us-prod。
```

```text
把 clear_cache.py 接入 Dagster 做小时调度任务，不入仓，key 使用 a4xmanage.clear_db_cache。
```

```text
按 hour 模板创建资产文件，若无数据产出则 return None，并确保 assets 模块 import 校验通过。
```

### Bad

```text
直接在普通 Python 项目注册 Dagster 资产，不配置 DBT_REPO_DIR。
```

```text
未确认调度周期，默认套用 day 模板并写入 cron。
```

```text
非数据任务命名为 ods_clear_cache_di，并强制配置入仓。
```

## References

- [Python 资产模板（天调度）](references/python-asset-template-day.md)
- [Python 资产模板（小时调度）](references/python-asset-template-hour.md)
- [Asset Key Definition](references/asset-key-definition.md)
- [Deployment Environment Definition](references/deployment-environment-definition.md)
- [Scheduling Definition](references/scheduling-definition.md)
- [Run Config（运行时参数化）](references/python-asset-run-config.md)
- [BackfillPolicy（一次性回填）](references/python-asset-backfill.md)
- [数据幂等（primary_keys vs pre_sql）](references/python-asset-idempotency.md)
- [多文件 asset 目录组织（`_common.py`）](references/python-asset-multi-file.md)

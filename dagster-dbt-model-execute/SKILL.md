---
name: dagster-dbt-model-execute
description: 在 DATA/dbt 的 Dagster 项目中执行 dbt 模型，统一通过 a4x_dagster.helper.dbt.DbtHelper 完成 parse、compile、run 或 build。本 Skill 用于“执行 dbt 模型”“跑一下某个 dbt model”“run 某个模型”“build 某个模型”“编译某个 dbt 模型”“验证某个 dbt model 能不能跑”等场景，也用于承接 dagster-dbt-model-create 之后的模型执行请求。依赖 dagster-project-prepare 先准备本地项目和运行环境。
---

# dagster-dbt-model-execute

在 `DATA/dbt` 的 Dagster 项目中执行 dbt 模型。这个 Skill 只负责“运行”，不负责环境准备，也不负责创建模型；如果本地项目尚未准备好，先执行 `dagster-project-prepare`。

## Description

适用场景：
- 用户要执行某个 dbt 模型
- 用户要先 `parse` 或 `compile` 验证模型
- 用户要本地 `run` 某个 dbt 模型
- 用户要本地 `build` 某个 dbt 模型
- 用户要检查某个模型在当前环境里是否能跑通
- 用户没有dagster凭证，但是要跑dbt模型

前置条件：
- `DBT_REPO_DIR` 已由 `dagster-project-prepare` 准备好，并指向本地 `DATA/dbt`
- 需要真实运行模型时，本地 `.env.local` 已准备完成
- 当前工作范围仅限 `dbt_athena`

范围限制：
- 仅支持 `DATA/dbt`
- 仅处理本地 dbt 模型执行，不处理 Dagster Cloud / GraphQL 远程运行
- 仅通过 `from a4x_dagster.helper.dbt import DbtHelper` 执行
- 不负责创建或修改 dbt 模型文件
- 仅用户开发调试，无法执行`prod`环境

关键输出：
- 明确的执行动作：`parse`、`compile`、`run` 或 `build`
- 实际执行命令与目标模型
- 执行结果、失败原因与下一步建议

## 输入契约

执行前确认以下输入；缺失则先询问用户：
- 执行动作：`parse`、`compile`、`run` 还是 `build`
- 目标模型名：通常为模型文件名去掉 `.sql`
- 若为 `run` 或 `build`：
  - `partition_key`：必填，日模型用 `yyyy-MM-dd`，小时模型用 `yyyy-MM-dd-HH`，小时以下（半小时）用 `yyyy-MM-dd-HH-mm`
  - `env_name`：默认 `dev`，如需 `staging` 需用户明确说明
  - `region_name`：默认 `us`
- 执行目的：只是校验可编译，还是要真实产出数据

## Rules

### 核心原则

1. 先确认环境已由 `dagster-project-prepare` 准备完成，再开始执行模型。
2. 统一通过 `DbtHelper` 执行；不要绕开 helper 直接写另一套 dbt 调用方式。
3. 只做用户明确要求的执行级别：`parse`、`compile`、`run`、`build`。
4. 真实 `run` 或 `build` 前先确认模型名、分区和目标环境，避免误跑。
5. 执行失败即停，并明确返回失败阶段和原因。

### 红线

1. 禁止在 `DBT_REPO_DIR` 未就绪或 repo 非 `DATA/dbt` 时继续执行。
2. 禁止在用户未给出 `partition_key` 时执行 `run` 或 `build`。
3. 禁止默认把 `compile` 升级成 `run` 或 `build`。
4. 禁止默认切换到非 `dev` 环境，除非用户明确要求。
5. 禁止把执行成功表述成“模型已可安全上线”。

### 实施流程（Phase 1-4）

- Phase 1：检查依赖 Skill 输出与本地环境（Rule 1）
- Phase 2：确认执行动作与参数（Rule 2）
- Phase 3：通过 `DbtHelper` 执行模型（Rule 3）
- Phase 4：回传执行结果与失败原因（Rule 4）

### Rule 1 - 检查依赖 Skill 输出

先确认 `dagster-project-prepare` 已经完成。有效目录必须包含：
- `dbt_athena/dbt_project.yml`
- `a4x_dagster/pyproject.toml`

若用户要求真实运行 `run` 或 `build`，还需要：
- `a4x_dagster/.env.local`

```powershell
$env:DBT_REPO_DIR
Test-Path "$env:DBT_REPO_DIR/dbt_athena/dbt_project.yml"
Test-Path "$env:DBT_REPO_DIR/a4x_dagster/pyproject.toml"
$origin = git -C $env:DBT_REPO_DIR remote get-url origin
$origin -match "DATA/dbt(\.git)?$"
```

```bash
echo "$DBT_REPO_DIR"
test -f "$DBT_REPO_DIR/dbt_athena/dbt_project.yml"
test -f "$DBT_REPO_DIR/a4x_dagster/pyproject.toml"
origin=$(git -C "$DBT_REPO_DIR" remote get-url origin)
echo "$origin" | grep -E "DATA/dbt(\.git)?$"
```

### Rule 2 - 确认执行动作与参数

按意图区分执行方式：
- 用户说“检查能不能解析”或“先做基础校验”：执行 `parse`
- 用户说“编译一下这个模型”：执行 `compile`
- 用户明确说“run 一下这个模型”或“只跑模型本体”：执行 `run`
- 用户明确说“build 一下这个模型”或“做完整构建验证”：执行 `build`
- 用户只说“跑一下这个模型”但未区分 `run` 还是 `build`：先问清楚，不要擅自选择

参数规则：
- `parse`：无模型参数
- `compile`：必须有 `<model_name>`
- `run`：必须有 `<model_name>` 和 `partition_key`
- `build`：必须有 `<model_name>` 和 `partition_key`
- 若用户未说明环境，默认 `region_name='us'`、`env_name='dev'`

### Rule 3 - 通过 DbtHelper 执行

固定在 `a4x_dagster` 目录下执行。

仅解析项目：
```powershell
Set-Location "$env:DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.parse()"
```

```bash
cd "$DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.parse()"
```

编译指定模型：
```powershell
Set-Location "$env:DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.compile('<model_name>')"
```

```bash
cd "$DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.compile('<model_name>')"
```

执行指定模型主体：
```powershell
Set-Location "$env:DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.run(select_model='<model_name>', partition_key='<yyyy-MM-dd>', region_name='us', env_name='dev')"
```

```bash
cd "$DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.run(select_model='<model_name>', partition_key='<yyyy-MM-dd>', region_name='us', env_name='dev')"
```

完整构建指定模型：
```powershell
Set-Location "$env:DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.build(select_model='<model_name>', partition_key='<yyyy-MM-dd>', region_name='us', env_name='dev')"
```

```bash
cd "$DBT_REPO_DIR/a4x_dagster"
uv run python -c "from a4x_dagster.helper.dbt import DbtHelper; DbtHelper.build(select_model='<model_name>', partition_key='<yyyy-MM-dd>', region_name='us', env_name='dev')"
```

小时模型把 `partition_key` 改成 `yyyy-MM-dd-HH`；小时以下（半小时）改成 `yyyy-MM-dd-HH-mm`。

### Rule 4 - 回传结果

结果回传时必须说明：
- 实际执行的是 `parse`、`compile`、`run` 还是 `build`
- 执行的目标模型与关键参数
- 执行是否成功
- 若失败：失败阶段、报错信息和建议修复方向

## Examples

### Good

```text
帮我 run 一下 dagster 项目里的 dwd_base_hi，按 2026-04-22-00-30 这个分区跑 dev 环境。
```

```text
帮我 build 一下 dagster 项目里的 dwd_base_hi，按 2026-04-22-00-30 这个分区做完整验证。
```

```text
先帮我 compile 一下 ads_order_summary 这个 dbt 模型，确认能不能编译通过。
```

```text
帮我 parse 一下当前 dbt 项目，看看模型结构有没有明显问题。
```

### Bad

```text
不用准备本地 DATA/dbt，直接帮我跑一个 dbt model。
```

```text
分区你自己猜一个，直接 build 就行。
```

```text
顺手把模型也一起改了再执行，不用区分创建和运行。
```

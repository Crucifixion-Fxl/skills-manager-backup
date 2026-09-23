---
name: dagster-dbt-model-create
description: 在 DATA/dbt 的 Dagster 项目中创建或修改 dbt 模型，指导用户完成模型目录选择、命名、SQL 与 YAML 成对编写。当用户提到“创建 dbt 模型”“新增 dbt model”“在 dagster 项目里写 dbt SQL”“补一个 dbt 模型”或“修改现有 dbt 模型”时使用。依赖 dagster-project-prepare 先准备本地项目；如需执行模型，转交 dagster-dbt-model-execute。
---

# dagster-dbt-model-create

在 `DATA/dbt` 的 Dagster 项目里创建或修改 dbt 模型。这个 Skill 聚焦 `dbt_athena` 下的模型开发；如果本地项目尚未准备好，先执行 `dagster-project-prepare`。

## Description

适用场景：
- 用户要新增 dbt 模型
- 用户要修改现有 dbt 模型的 SQL 或元信息
- 用户只有业务口径，需要先落成模型骨架
- 用户要先把模型写好，再交给后续 Dagster/dbt 流程使用

前置条件：
- `DBT_REPO_DIR` 已由 `dagster-project-prepare` 准备好，并指向本地 `DATA/dbt`
- 当前工作范围仅限 `dbt_athena`

范围限制：
- 仅支持 `DATA/dbt`
- 仅处理 `dbt_athena/models` 下的 dbt 模型与配套 `*.yml`
- 不负责 Python asset 注册、Dagster 调度接入或生产发布
- 不在信息不完整时擅自决定模型层级、命名或物化策略

关键输出：
- 新增或更新的 `dbt_athena/models/<layer>/<domain>/<model_name>.sql`
- 同目录配套 `dbt_athena/models/<layer>/<domain>/<model_name>.yml`
- 最小可用的模型说明、列描述与必要 tests
- 仍待用户确认的业务问题，以及后续是否需要转交执行 Skill

## 输入契约

执行前确认以下输入；缺失则先询问用户：
- 业务目标：这个模型要解决什么问题
- 目标模型：是新建还是修改现有模型
- 目标层级与领域目录：如 `dw` / `dim` / `ads` 及业务域
- 模型名称：需与同目录现有命名风格保持一致
- 上游输入：依赖哪些 `source()` / `ref()`
- 上游类型：哪些是已有模型，哪些是外部表；如果是外部表，是否已在 `dbt_athena/models/sources.yml` 中定义
- 粒度与主键：按天、按小时、按实体，是否有唯一键
- 产出字段：至少确认核心字段及含义
- 调度方式：按小时还是按天；这会直接决定 `tags` 和时间过滤表达式
- `config()` 关键决策：`materialized`、`group`、`schema`、`tags`，以及是否需要 `partitioned_by`、`unique_key`、增量重跑策略
- 物化方式：如 `view`、`table`、`incremental`；其中 `view` 是逻辑视图，`table` 是全量表，`incremental` 是增量更新表；不明确时先询问用户
- 是否需要在建模完成后继续执行模型；如果需要，则转交 `dagster-dbt-model-execute`

## Rules

### 核心原则

1. 先确认本地项目已由 `dagster-project-prepare` 准备完成，再开始模型开发。
2. 先读同目录现有模型，再决定新模型的层级、命名、配置和 YAML 写法；如果同目录样例不足，可以参考临近业务模型，但不要把具体业务样例机械带入目标模型。
3. `.sql` 与 `.yml` 必须成对维护；不要只改 SQL 不补元信息。
4. 优先复用邻近模型的目录结构、命名习惯和配置模式，不要凭空发明新规范。
5. 建模完成后如需真实执行或校验模型，转交 `dagster-dbt-model-execute`，不要在本 Skill 内继续承担执行职责。

新建模型或先搭骨架时，先读取 [references/dbt-model-template.md](references/dbt-model-template.md) 作为第一版模板，尤其先按里面的 `config()` 参数规范逐项决策，再根据目标目录附近的真实模型收敛。

### 红线

1. 禁止在 `DBT_REPO_DIR` 未就绪或 repo 非 `DATA/dbt` 时继续创建模型。
2. 禁止在层级、目录或命名不清楚时擅自落文件。
3. 禁止覆盖现有模型文件而不先向用户确认。
4. 禁止只创建 `.sql` 而缺失配套 `.yml`。
5. 禁止把未验证的模型表述成“已可稳定投入 Dagster 运行”。
6. 禁止在模型 SQL 中直接写 `<db>.<table>` 这类物理表名；必须统一使用 `ref()` 或 `source()`。原因是项目不允许跨环境直接查询，权限控制依赖 dbt 的引用机制做环境隔离；直接写物理表名容易绕过这层约束。

### 实施流程（Phase 1-5）

- Phase 1：依赖检查与目录确认（Rule 1）
- Phase 2：分析邻近模型并确认落点（Rule 2）
- Phase 3：创建或修改 SQL 模型（Rule 3）
- Phase 4：补齐 YAML 描述与基础 tests（Rule 4）
- Phase 5：回传建模结果，并在需要时转交执行 Skill（Rule 5）

### Rule 1 - 检查依赖 Skill 输出

先确认 `dagster-project-prepare` 已经完成，且当前 `DBT_REPO_DIR` 可用。有效目录必须包含：
- `dbt_athena/dbt_project.yml`
- `a4x_dagster/pyproject.toml`

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

如果依赖检查失败，先转入 `dagster-project-prepare`，不要继续创建模型。

### Rule 2 - 先读邻近模型，再确定落点

在动手前先查看目标目录附近至少一个同层模型，确认：
- 目录落点：`ads` / `dim` / `dw` 及业务域子目录
- 命名前缀与后缀：如 `dwd_`、`dwm_`、`ads_`，以及 `di` / `hi` 等时间粒度后缀
- 常用 `config()` 模式：`schema`、`tags`、`materialized`、分区或增量策略
- YAML 组织方式：模型说明、列描述、tests 的写法
- 上游引用方式：已有模型通常如何 `ref()`，外部表通常如何 `source()`，以及对应 `dbt_athena/models/sources.yml` 如何组织

如果目标目录附近样例不足，可以补看临近业务模型来参考写法；但最终仍要以目标目录的命名、语义和约定收敛，不要直接复用别的业务表达。

其中要特别确认：
- `tags` 通常用于声明调度方式，给外部调度系统识别
- `materialized` 用于声明物化方式，是生成 `view`、全量 `table` 还是增量 `incremental`
- 模型 SQL 里不能直接写物理表名；已有模型用 `ref()`，外部表用 `source()`

如果层级、目录或命名拿不准，必须先问用户，不要硬猜。

### Rule 3 - 创建或修改 SQL 模型

目标文件路径：
- `dbt_athena/models/<layer>/<domain>/<model_name>.sql`

SQL 规则：
- 优先复用现有 `source()` / `ref()` 关系，不要绕开现有数据链路
- 禁止直接写物理表名；已有模型一律使用 `ref()`，外部表一律使用 `source()`，避免绕过环境隔离与权限控制
- 如果使用 `source()`，先确认对应外部表已在 `dbt_athena/models/sources.yml` 中定义；如果没有定义，先补齐再引用
- `config()` 必须逐项按 `references/dbt-model-template.md` 里的参数规范判断；不要整段照抄样例
- `config()` 尽量贴近同目录现有模型；不要默认把所有模型都写成 `incremental`
- 如果选择 `incremental`，必须先确认唯一键、分区字段和重跑策略
- 如果选择 `view` 或 `table`，要主动删掉不适用的增量参数，不要保留“以后可能会用”的配置
- 优先把业务转换逻辑放进 SQL 主体；不要把字段说明堆在 SQL 里替代 YAML

如果目标文件已存在，先读取现状并征求用户确认，再执行修改。

### Rule 4 - 补齐 YAML 描述与基础 tests

配套文件路径：
- `dbt_athena/models/<layer>/<domain>/<model_name>.yml`

YAML 规则：
- 必须包含 `version: 2`
- 必须为模型补齐描述
- 必须为核心字段补齐描述
- 仅在语义明确时增加 `not_null`、`unique` 等强约束 tests
- 如果用户只要求先搭骨架，也至少提供最小可读的模型说明与核心字段说明

### Rule 5 - 回传建模结果并转交执行 Skill

结果回传时必须说明：
- 新增或修改了哪些模型文件
- 哪些业务口径、字段含义或物化条件仍待用户确认
- 如果用户下一步要解析、编译、run 或 build 模型，转交 `dagster-dbt-model-execute`

## Examples

### Good

```text
帮我在 dagster 项目里新增一个 dbt 模型，把 finance 的原始账单整理成按天可分析的数据表，先帮我判断应该落在哪一层和哪个目录。
```

```text
在现有 DATA/dbt 里补一个 ads 模型，输入是两个已有的 dw 模型，输出给运营看周维度结果，SQL 和 yml 一起补齐。
```

```text
帮我修改 customer_care 下面一个现有 dbt model，先读同目录模型再改，不要直接覆盖；改完如果需要执行，我再让你转到执行 skill。
```

### Bad

```text
不用看现有项目结构，直接随便新建一个 dbt 模型，名字和目录你自己定。
```

```text
只写 SQL 就行，yml、字段说明和验证都先不要了。
```

```text
不需要先准备本地 DATA/dbt，直接在任意目录创建这个模型。
```

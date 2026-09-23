---
name: dagster-project-prepare
description: 为 Dagster 项目准备本地基础环境或更新项目代码：拉取或更新项目代码、设置本地代码目录变量 DBT_REPO_DIR、在 a4x_dagster 子目录安装依赖并做基础验证。底层依赖 DATA/dbt 仓库。当用户提到“帮我搭建 dagster 项目环境”“帮我更新 dagster 项目代码”“配置 DBT_REPO_DIR”或“先把 dagster 环境装起来”时使用。
---

# dagster-project-prepare

准备 Dagster 项目的基础本地环境。底层代码来自 `DATA/dbt` 仓库，这个 Skill 只负责第一步：

- 准备本地 Dagster 项目代码目录
- 设置 `DBT_REPO_DIR`
- 在 `a4x_dagster` 子目录安装依赖
- 在需要本地运行 `a4x_dagster` 时初始化 `.env` 凭证
- 验证 `a4x-dagster` 包可导入

不要在这个 Skill 中引入额外的项目级变量或运行时配置。后续如果用户要启动本地 Dagster、执行具体资产、接入业务配置，再进入后续流程。

## Description

适用场景：

- 新人第一次在本机准备 Dagster 基础环境
- 本地已经有 Dagster 项目代码，但需要更新代码并重新同步依赖
- 需要一个统一可复用的本地代码目录变量 `DBT_REPO_DIR`
- 用户还没进入具体资产开发，只想先把基础环境装好
- 用户明确要求让 `a4x_dagster` 本地跑起来

关键概念：

- `DBT_REPO_DIR`：本地 Dagster 项目代码目录，底层对应 `DATA/dbt`
- `a4x_dagster`：仓库内负责 Dagster 相关 Python 依赖和代码的子目录

范围限制：

- 仅支持底层来自 `DATA/dbt` 的 Dagster 项目
- 仅处理基础准备，不处理后续业务配置或运行时配置
- 不要把执行结果表述为“Dagster 已完全可运行”

关键输出：

- 可用的本地 Dagster 项目目录
- 已设置的 `DBT_REPO_DIR`
- 已同步的 `a4x_dagster` 依赖环境
- 如果目标是本地可运行，已初始化 `a4x_dagster/.env.local`
- 明确说明这只是基础环境准备完成

## 输入契约

执行前确认以下输入；缺失则先询问用户：

- 目标路径（可选）：用于 clone 或复用 Dagster 项目代码，未提供时优先复用当前 `DBT_REPO_DIR`
- 仓库地址（可选）：默认 `git@gitlab.addx.ai:DATA/dbt.git`
- 更新策略（必填）：首次 clone 或已有仓库 `git pull --ff-only`

## Rules

### 核心原则

1. 仅在底层为 `DATA/dbt` 的 Dagster 项目范围内执行
2. 先校验后改动，失败即停止
3. 验证阶段统一使用 `uv run python`
4. 只做基础环境准备，不扩展到后续运行配置
5. 只有在目标是让 `a4x_dagster` 本地可运行时，才初始化 `.env` 凭证

### 红线

1. 禁止在非目标 Dagster 项目继续执行
2. 禁止跳过 `DBT_REPO_DIR` 结构校验
3. 禁止在验证阶段直接使用系统 `python`
4. 禁止在未确认影响范围前执行破坏性 Git 命令
5. 禁止把基础准备结果说成“本地 Dagster 已完全可运行”
6. 禁止提交 `a4x_dagster/.env.local`

### 实施流程（Phase 1-6）

- Phase 1：前置检查与目录确认（Rule 1）
- Phase 2：拉取或更新仓库（Rule 2）
- Phase 3：设置本地代码目录变量（Rule 3）
- Phase 4：安装依赖（Rule 4）
- Phase 5：初始化本地运行凭证（Rule 5，可选）
- Phase 6：基础可用性验证与结果回传（Rule 6）

### Rule 1 - 确认目标目录

先检查 `DBT_REPO_DIR` 是否已设置且可用；可用则直接复用，不可用再向用户确认目标路径。

有效目录必须包含：

- `a4x_dagster/pyproject.toml`

```powershell
$env:DBT_REPO_DIR
Test-Path $env:DBT_REPO_DIR
Test-Path "$env:DBT_REPO_DIR/a4x_dagster/pyproject.toml"
```

```bash
echo "$DBT_REPO_DIR"
test -d "$DBT_REPO_DIR"
test -f "$DBT_REPO_DIR/a4x_dagster/pyproject.toml"
```

同时校验仓库 `origin` 指向 `DATA/dbt`：

```powershell
$origin = git -C $env:DBT_REPO_DIR remote get-url origin
$origin -match "DATA/dbt(\.git)?$"
```

```bash
origin=$(git -C "$DBT_REPO_DIR" remote get-url origin)
echo "$origin" | grep -E "DATA/dbt(\.git)?$"
```

### Rule 2 - 拉取或更新项目代码

目标代码仓库：`https://gitlab.addx.ai/DATA/dbt.git`（或 SSH 地址）。

```powershell
# 目录不存在
git clone https://gitlab.addx.ai/DATA/dbt.git <target_dir>/dbt

# 目录已存在
git -C <target_dir>/dbt pull --ff-only origin master
```

```bash
# 目录不存在
git clone https://gitlab.addx.ai/DATA/dbt.git <target_dir>/dbt

# 目录已存在
git -C <target_dir>/dbt pull --ff-only origin master
```

若本地仓库有未提交改动（`git status --porcelain` 非空），先停止并让用户确认是否继续，避免污染用户工作区。

更新规则：

- 仓库已存在时，一律执行 `git pull --ff-only origin master`
- 不要在这个 Skill 中自动切换分支

### Rule 3 - 设置本地代码目录变量

设置基础环境变量：

```powershell
$env:DBT_REPO_DIR = "<target_dir>/dbt"
```

```bash
export DBT_REPO_DIR=<target_dir>/dbt
```

只设置 `DBT_REPO_DIR`。不要在这个 Skill 中继续扩展其他变量。

### Rule 4 - 安装依赖

进入 `a4x_dagster` 子目录后执行 `uv sync --extra dev`。如果本机没有 `uv`，先用 `pip` 安装 `uv`。

```powershell
cd "$env:DBT_REPO_DIR/a4x_dagster"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    python -m pip install -U uv
}
uv sync --extra dev
```

```bash
cd "$DBT_REPO_DIR/a4x_dagster"
if ! command -v uv >/dev/null 2>&1; then
  python -m pip install -U uv
fi
uv sync --extra dev
```

### Rule 5 - 初始化本地运行凭证

如果用户目标是“让 `a4x_dagster` 本地跑起来”，在完成依赖安装后继续执行本规则；如果只是更新代码或准备基础环境，可以跳过。

初始化方式：

- 如果 `a4x_dagster/.env.local` 不存在，则从 `a4x_dagster/.env.template` 复制生成
- 如果 `a4x_dagster/.env.local` 已存在，则不要覆盖，改为人工检查和更新
- 除固定规则外，其余模板默认值保持不动
- 如果 `VAULT_TOKEN=unknown`，提示用户前往 [应用配置中心](https://dapp.addx.live/app_configuration_center) 申请调试令牌，并把值返回给你；拿到后再写入 `.env.local`
- 将 `DBT_PROJECT_DIR` 固定设置为 `$DBT_REPO_DIR/dbt_athena`
- 将 `DAGSTER_HOME` 固定设置为 `$DBT_REPO_DIR/a4x_dagster/dagster_home`

```powershell
$envFile = "$env:DBT_REPO_DIR/a4x_dagster/.env.local"
$templateFile = "$env:DBT_REPO_DIR/a4x_dagster/.env.template"
if (-not (Test-Path $envFile)) {
    Copy-Item $templateFile $envFile
}
$content = Get-Content $envFile
$content = $content -replace '^DBT_PROJECT_DIR=.*$', ('DBT_PROJECT_DIR=' + "$env:DBT_REPO_DIR/dbt_athena")
$content = $content -replace '^DAGSTER_HOME=.*$', ('DAGSTER_HOME=' + "$env:DBT_REPO_DIR/a4x_dagster/dagster_home")
Set-Content -Path $envFile -Value $content
```

```bash
env_file="$DBT_REPO_DIR/a4x_dagster/.env.local"
template_file="$DBT_REPO_DIR/a4x_dagster/.env.template"
if [ ! -f "$env_file" ]; then
  cp "$template_file" "$env_file"
fi
python - <<'PY'
from pathlib import Path
import os
env_file = Path(os.environ["DBT_REPO_DIR"]) / "a4x_dagster" / ".env.local"
lines = env_file.read_text(encoding="utf-8").splitlines()
updated = []
for line in lines:
    if line.startswith("DBT_PROJECT_DIR="):
        updated.append(f'DBT_PROJECT_DIR={os.environ["DBT_REPO_DIR"]}/dbt_athena')
    elif line.startswith("DAGSTER_HOME="):
        updated.append(f'DAGSTER_HOME={os.environ["DBT_REPO_DIR"]}/a4x_dagster/dagster_home')
    else:
        updated.append(line)
env_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
```

### Rule 6 - 基础可用性验证

如果只是准备基础环境，可以停在 Rule 4 或 Rule 5。

如果用户目标是让 `a4x_dagster` 本地可运行，Rule 6 的验证方式改为真实执行 `a4x_dagster/start.py`。

执行顺序固定为：

1. 加载 `a4x_dagster/.env.local`
2. 执行 `a4x_dagster/start.py`

```powershell
$projectDir = "$env:DBT_REPO_DIR/a4x_dagster"
$envFile = "$projectDir/.env.local"
Test-Path $envFile
uv run python -c "from dotenv import load_dotenv; import os, runpy; load_dotenv(r'$envFile'); os.chdir(r'$projectDir'); runpy.run_path('start.py', run_name='__main__')"
```

```bash
project_dir="$DBT_REPO_DIR/a4x_dagster"
env_file="$project_dir/.env.local"
test -f "$env_file"
uv run python -c "from dotenv import load_dotenv; import os, runpy; load_dotenv(r'$env_file'); os.chdir(r'$project_dir'); runpy.run_path('start.py', run_name='__main__')"
```

结果回传时使用下面的措辞：

- 已完成：Dagster 项目代码已拉取或更新，`DBT_REPO_DIR` 已设置，`a4x_dagster` 依赖已同步
- 如果目标是本地可运行：已加载 `a4x_dagster/.env.local` 并成功执行 `a4x_dagster/start.py`
- 未完成：后续运行所需的其他配置与具体业务接入步骤

若任一项失败，必须明确返回失败原因与修复建议，不可继续进入后续流程。

## Examples

### Good

```text
帮我先把 dagster 项目环境搭起来：更新代码，设置 DBT_REPO_DIR，并在 a4x_dagster 下执行 uv sync --extra dev。
```

```text
我是新人，先帮我把 dagster 项目代码和依赖准备好，后面的运行配置先不做。
```

```text
帮我把 a4x_dagster 项目跑起来：更新代码、安装依赖，并把 .env.template 初始化成 .env.local；如果 VAULT_TOKEN 还是 unknown，就提醒我去申请。
```

### Bad

```text
帮我在任意普通 Python 项目里执行这个流程，不需要 dagster 项目代码，也不用设置 DBT_REPO_DIR。
```

```text
不用确认仓库来源，直接覆盖我现有 dbt 目录。
```

# Board Setup — 按需配置

Board 是工作项数据的视图，不是另一套分类模型。配置前先读 [Label Governance executable SSOT](label-system.md)。不要按每个 label 族机械创建 board。

## 推荐清单

| Board | 是否默认创建 | list 定义（从左到右） |
|---|---|---|
| Workflow | 推荐 | 原生模式使用 Work Item Status lists；Label 兼容模式按 SSOT 的 canonical workflow 顺序 |
| Priority | 有持续排期需求时 | Open → SSOT 的 canonical priority 顺序 → Closed |
| Domain | Business Group 有跨项目规划需求时 | Open → 各 `domain::*` → Closed |
| Area | 默认不创建 | 优先通过 `area/*` filter 查询 |
| 项目模块 | 项目持续按领域模块协调时 | 经治理的 `module::*`，一个模块一栏；见下文 |

`flag/blocked` 用作过滤条件或卡片标记，不创建 blocked 状态列。这样 issue 被阻塞时仍保留真实工作状态。

- **原生模式**：按 namespace 已配置的 Work Item Status 建立 status lists；拖动卡片时由 GitLab 更新原生 Status。
- **Label 兼容模式**：按 `label-system.md` 当前定义的 canonical workflow 顺序建立 label lists，
  不在本文件复制枚举。

同一 namespace 只采用一种模式，不在同一 Workflow board 混用 Status lists 和 `status::*` lists。

## 创建 Label 兼容模式的 Project Workflow Board（幂等）

以下脚本只适用于 Label 兼容模式。Project board 可以使用祖先 Group 继承的 labels，不需要复制为 Project labels。`GET /projects/:id/labels` 默认包含祖先 Group labels。

```bash
#!/usr/bin/env bash
set -euo pipefail

label_id() {
  glab api "projects/:id/labels?include_ancestor_groups=true&search=$1&per_page=100" \
    | jq --arg n "$1" -r '.[] | select(.name == $n) | .id' \
    | head -n 1
}

create_board() {
  local name="$1"
  local existing
  existing=$(glab api "projects/:id/boards" \
    | jq --arg n "$name" -r '.[] | select(.name == $n) | .id' \
    | head -n 1)
  if [ -n "$existing" ]; then
    echo "$existing"
    return
  fi
  glab api -X POST "projects/:id/boards" -f name="$name" | jq -r '.id'
}

add_list() {
  local board_id="$1" label_name="$2" lid existing
  lid=$(label_id "$label_name")
  if [ -z "$lid" ]; then
    echo "WARN: inherited label '$label_name' not found" >&2
    return 1
  fi
  existing=$(glab api "projects/:id/boards/$board_id/lists" \
    | jq --arg n "$label_name" '[.[] | select(.label.name == $n)] | length')
  if [ "$existing" -gt 0 ]; then
    echo "exists: $label_name (skip)"
    return
  fi
  glab api -X POST "projects/:id/boards/$board_id/lists" \
    -f label_id="$lid" >/dev/null
  echo "added:  $label_name"
}

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <canonical-status-label> [...]" >&2
  exit 2
fi

BID=$(create_board "Workflow")
for status_label in "$@"; do
  add_list "$BID" "$status_label"
done
```

如果目标是多个项目的统一视图，应在 Business Group 使用 Group board，并让 board 与 `domain::*` 创建在相同或更低层级；不要在各项目复制 Domain board。

## 配置检查

- 原生模式的 Workflow list 只使用 Work Item Status；Label 兼容模式只使用 `status::*`，不混入 priority、area 或 flag。
- `flag/blocked` 通过 board filter 查看。
- Closed 使用 GitLab 自带 list，不创建 `status::done`。
- 单个 board 超过 10 个业务 list 时，先检查是否把筛选维度误当成了工作流。
- 删除 board 不会删除 labels，但删除或改名 label 前要先检查 board 引用。

## 项目模块 Board

需要“一个模块一栏”时，先把现有 Issue 映射到 [Label SSOT](label-system.md#project-domain-module-module-and-submodule-project-scoped) 批准的两级互斥模块。`type::research` 是工作类型；`module::*` 是项目 Board 的一级列，`submodule::*` 是二级筛选标签。先读回项目/祖先 labels，按照 SSOT 的创建层级补齐缺失值，再创建/复用项目 Board 和按批准顺序的一级 label lists。研究和开发 Issue 共用模块分类；GET 回读全部 Issue 与 lists，核对没有漏列/重复列/不匹配的组合。跨模块依赖用关联 Issue 表示。

GitLab Free/CE 的 Board 不能持久按 `type::research` 过滤；共享模块标签也会让后续开发 Issue 出现在这个 Board。预研范围应结合类型筛选或单独查询核对，不把模块列当作预研专属集合。可按使用需要隐藏默认 Open/Closed 列，保留未分类 Issue 的可追溯查询；不要把隐藏 Open 列误认为未分类项不存在。SQ 的四个一级项目标签为 AI、视觉、定位、设备系统；二级标签只作过滤。现有 Board 名称为 `SQ · 模块`。新模块先更新 SSOT 并经项目 owner 核准，再改标签/Board，不随每次选型临时造列。

优先级使用既有 `priority::p0`–`p3` 做筛选，未排过序的 Issue 不推断 P2。开放 Issue 的状态使用 `status::*`；“搁置”用 `status::on-hold` 并记录复查日期；整单“放弃”用 `resolution::abandoned` 加原生 Closed，不能仅把卡片拖出 Board 当作放弃。模块列不因状态或优先级改变。

## 反模式

| 反模式 | 正确做法 |
|---|---|
| 初始化 5 个维度就创建 5 个 boards | 先从 Workflow 开始，其他 board 必须有持续使用场景 |
| 为 `area/*` 每个值建列 | 用 filter；只有稳定专项流程才建独立 board |
| 把 blocked 当成 status 列 | 保留 status，并用 `flag/blocked` 过滤 |
| 在项目复制 Group labels 只为建 board | 直接使用继承的 Group label |

## SIG Board

用户明确要求“每个 SIG 一个 Board”时，按 [SIG workflow](sig-workflow.md) 建立或复用。
`sig::*` 定义 Board 范围。默认工作流视图按 canonical workflow 建列；用户明确要求按方向分栏时，SIG Board 以已治理的 `topic/*` 建方向列，`status::*` 保留在 Issue 上用于进度筛选。方向是多选，同一 Issue 可出现在多列，不得按卡片数统计专项或把方向当互斥工作流。当前五个 SIG 使用方向视图。
Free/CE 没有持久的 configurable Board scope，每个对外链接必须保留 SIG 的
`label_name[]` 筛选，并验收筛选后的 Issue 集合。不能复制每 SIG 一套 status labels。

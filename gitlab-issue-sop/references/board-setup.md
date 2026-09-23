# Board Setup — 按需配置

Board 是工作项数据的视图，不是另一套分类模型。配置前先读 [Label Governance executable SSOT](label-system.md)。不要按每个 label 族机械创建 board。

## 推荐清单

| Board | 是否默认创建 | list 定义（从左到右） |
|---|---|---|
| Workflow | 推荐 | 原生模式使用 Work Item Status lists；Label 兼容模式按 SSOT 的 canonical workflow 顺序 |
| Priority | 有持续排期需求时 | Open → SSOT 的 canonical priority 顺序 → Closed |
| Domain | Business Group 有跨项目规划需求时 | Open → 各 `domain::*` → Closed |
| Area | 默认不创建 | 优先通过 `area/*` filter 查询 |

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

## 反模式

| 反模式 | 正确做法 |
|---|---|
| 初始化 5 个维度就创建 5 个 boards | 先从 Workflow 开始，其他 board 必须有持续使用场景 |
| 为 `area/*` 每个值建列 | 用 filter；只有稳定专项流程才建独立 board |
| 把 blocked 当成 status 列 | 保留 status，并用 `flag/blocked` 过滤 |
| 在项目复制 Group labels 只为建 board | 直接使用继承的 Group label |

# Batch Operations — glab CLI 批量脚本

完整的批量操作参考脚本。全部基于 `glab` CLI（`curl` + `PRIVATE-TOKEN` 的老写法已废弃）。所有脚本都是幂等的（重复执行不报错）。

## 前置

```bash
# 1. 安装 glab
brew install glab   # macOS
# Linux / Windows: https://gitlab.com/gitlab-org/cli/-/releases

# 2. 登录（每个 GitLab host 只登一次）
glab auth login --hostname gitlab.addx.ai

# 3. 验证
glab auth status
glab api projects/:id | jq .name   # 当前仓库名
```

所有脚本默认在目标仓库的 git worktree 执行，`glab` 通过 git remote 自动识别 project。路径里的 `:id` 会被替换为当前仓库 id。跨项目操作用 `-R <owner>/<repo>` 覆盖（例：`glab api -R engineering/skills projects/:id/issues`）。

**token 来源**：`glab auth` 存在 `~/.config/glab-cli/config.yml`，不需要手动管理 `GITLAB_TOKEN` 环境变量。老脚本从 `.env` 读 token 或设 `GITLAB_HOST` 的写法不再需要。

## 核心命令速查

| 操作 | glab 原生命令（推荐） | glab api 兜底（复杂参数或原生没有时） |
|------|----------------------|-----------------------------------|
| 创 issue | `glab issue create --title ... --label ... --assignee ...` | `glab api -X POST projects/:id/issues --input -` |
| 改 issue label | `glab issue update <iid> --label A --unlabel B` | `glab api -X PUT projects/:id/issues/<iid> -f add_labels=... -f remove_labels=...` |
| 加 comment | `glab issue note <iid> --message "..."` | `glab api -X POST projects/:id/issues/<iid>/notes -f body=...` |
| 关联 issue | *(无原生)* | `glab api -X POST projects/:id/issues/<iid>/links -f ...` |
| 创 label | `glab label create --name ... --color ...` | `glab api -X POST projects/:id/labels -f ...` |
| 删 label | `glab label delete <name>` | `glab api -X DELETE projects/:id/labels/<name>` |
| 创 board | *(无原生)* | `glab api -X POST projects/:id/boards -f name=...` |
| 加 board list | *(无原生)* | `glab api -X POST projects/:id/boards/<bid>/lists -f label_id=...` |

完整 endpoint 参考见 [api-reference.md](api-reference.md)。

## 创建缺失的 canonical Group Label

公共集合以 [Label Governance executable SSOT](label-system.md) 为准，只由 Group owner 在适用
项目的最低共同祖先 Group 创建。这里不复制完整名称、语义、颜色或初始化清单；否则脚本会成为
第二套可维护 taxonomy。

只有在 SSOT 的 missing-label gate 已产出 Ops Todo、外部写入已明确授权后，才可用下面的单项
模板。`NAME` 和 `DESCRIPTION` 必须逐项取自 `label-system.md`；`COLOR` 只是展示属性，由 Group
owner 在授权时确认。不要用此模板批量“初始化所有 labels”。

```bash
#!/usr/bin/env bash
set -euo pipefail

GROUP_ID="${1:?usage: $0 <group-id> <canonical-name> <color> <canonical-description>}"
NAME="${2:?canonical label name from label-system.md}"
COLOR="${3:?approved display color}"
DESCRIPTION="${4:?canonical meaning from label-system.md}"

create_label() {
  local name="$1" color="$2" desc="$3"
  local existing
  existing=$(glab api "groups/$GROUP_ID/labels?search=$name&per_page=100" \
    | jq --arg n "$name" '[.[] | select(.name == $n)] | length')
  if [ "$existing" -gt 0 ]; then
    echo "  exists:  $name (skip)"
    return
  fi
  glab api -X POST "groups/$GROUP_ID/labels" \
       -f name="$name" \
       -f color="$color" \
       -f description="$desc" >/dev/null
  echo "  created: $name"
}

create_label "$NAME" "$COLOR" "$DESCRIPTION"
```

## 批量创建 Issue

从 JSON 输入批量创建：

```bash
#!/usr/bin/env bash
set -euo pipefail

# issues.json 格式:
# [ {"title":"...", "description":"...", "labels":"type::maintenance,priority::p1,status::backlog,area/data", "assignee_ids":[123]}, ... ]

jq -c '.[]' issues.json | while read -r item; do
  # --input - 让 glab 从 stdin 读 JSON body
  echo "$item" | glab api -X POST projects/:id/issues --input - \
    | jq -r '"created #\(.iid): \(.title)"'
done
```

简单场景直接用原生命令：

```bash
glab issue create \
  --title "R-01: 数据分层未设计" \
  --label "type::maintenance,priority::p1,status::backlog,area/data" \
  --assignee @username \
  --description "$(cat issue-body.md)"
```

## 批量改 Label（**关键：避免覆盖丢数据**）

`PUT /issues/:iid` 的 `labels` 字段是覆盖语义。优先使用 `add_labels` / `remove_labels` 做增量更新，不 PUT 全集。

推荐优先用 `glab issue update --label/--unlabel`（自动合并）：

```bash
# 增加一个 label
glab issue update 19 --label "status::ready"

# 删除一个 label
glab issue update 19 --unlabel "status::backlog"

# 已明确知道旧值时同时增删
glab issue update 19 --label "status::ready" --unlabel "status::backlog"
```

但 `glab issue update` **不会自动 strip 同前缀 scoped label**（CE 无服务端互斥），所以批量脚本要先找出同 scope 旧值，再通过同一个增量请求移除旧值、添加新值：

```bash
#!/usr/bin/env bash
# 用法: ./relabel.sh <iid> <add-or-remove> <label>
# 例:   ./relabel.sh 19 add status::ready
set -euo pipefail

IID="$1"; OP="$2"; TARGET_LABEL="$3"

case "$OP" in
  add)
    old_scope_labels=""
    if [[ "$TARGET_LABEL" == *"::"* ]]; then
      prefix="${TARGET_LABEL%%::*}::"
      old_scope_labels=$(glab api "projects/:id/issues/$IID" \
        | jq --arg p "$prefix" --arg n "$TARGET_LABEL" -r \
          '[.labels[] | select(startswith($p) and . != $n)] | join(",")')
    fi
    args=(-f add_labels="$TARGET_LABEL")
    if [ -n "$old_scope_labels" ]; then
      args+=(-f remove_labels="$old_scope_labels")
    fi
    glab api -X PUT "projects/:id/issues/$IID" "${args[@]}"
    ;;
  remove)
    glab api -X PUT "projects/:id/issues/$IID" -f remove_labels="$TARGET_LABEL"
    ;;
  *)
    echo "OP must be add|remove" >&2
    exit 1
    ;;
esac \
  | jq -r '"#\(.iid) labels -> \(.labels | join(","))"'
```

增量字段不会覆盖并发写入的无关 labels。添加 scoped label 后仍应回读，确认同 scope 只剩一个值；若出现并发冲突，重新执行或交由人工处理。

## 批量加 Linked Items (relates_to)

```bash
#!/usr/bin/env bash
# 把 iid_a 和 iid_b_list 中的每个 iid 建立 relates_to 关联
set -euo pipefail

SRC_IID="$1"; shift
# 获取当前仓库 project id
PID=$(glab api projects/:id | jq -r '.id')

for TGT_IID in "$@"; do
  glab api -X POST "projects/:id/issues/$SRC_IID/links" \
    -f target_project_id="$PID" \
    -f target_issue_iid="$TGT_IID" \
    -f link_type="relates_to" \
    | jq -r '"linked #\(.source_issue.iid) <-> #\(.target_issue.iid)"' \
    || echo "WARN: link $SRC_IID <-> $TGT_IID failed (maybe duplicate)"
done
```

注意：CE 不支持 `link_type: blocks`，会返回 400。只用 `relates_to`。

## Label Migration（旧 → 新 + 归档）

场景：确认语义后，把项目里的旧 `area::data` 改成多选族 `area/data`，然后归档旧 label。Priority 迁移必须逐项重新 triage，不能套用这个批量改名脚本。

```bash
#!/usr/bin/env bash
set -euo pipefail

OLD_LABEL="area::data"
NEW_LABEL="area/data"

# 1. 搜索有 OLD_LABEL 的 issue
issues=$(glab api --paginate \
  "projects/:id/issues?labels=$OLD_LABEL&state=opened&per_page=100" \
  | jq -r '.[].iid')

# 2. 对每个 issue：移除旧，加新
for iid in $issues; do
  glab api -X PUT "projects/:id/issues/$iid" \
    -f remove_labels="$OLD_LABEL" \
    -f add_labels="$NEW_LABEL" >/dev/null
  echo "  migrated #$iid"
done

# 3. 回读确认没有遗漏
remaining=$(glab api --paginate \
  "projects/:id/issues?labels=$OLD_LABEL&state=opened&per_page=100" \
  | jq -s 'map(length) | add')
if [ "$remaining" -ne 0 ]; then
  echo "ERROR: $remaining open issues still use $OLD_LABEL" >&2
  exit 1
fi

# 4. 归档旧 label（GitLab 18.10+；更早版本保留 deprecated label）
old_label_path=$(printf '%s' "$OLD_LABEL" | jq -sRr @uri)
glab api -X PUT "projects/:id/labels/$old_label_path" -f archived=true >/dev/null
echo "archived old label: $OLD_LABEL"
```

## 加 Progress Comment

```bash
# 推荐：glab issue note（原生命令）
glab issue note 19 --message "$(cat <<'EOF'
## 进展更新 (2026-04-12)

埋点 schema 已提审，等 data team 审核。

**相关 MR**:
- customer-care/!18

**Follow-up**:
- 审核通过后在测试环境验证事件落表
EOF
)"

# 批量场景（多个 iid 贴同一条进展）：用 glab api
add_comment() {
  local iid="$1" body="$2"
  glab api -X POST "projects/:id/issues/$iid/notes" -f body="$body" \
    | jq -r '"commented on #\(.noteable_iid)"'
}

for iid in 19 20 21; do
  add_comment "$iid" "已 unblock，MR 见 !18"
done
```

## 错误处理速查

| HTTP / 症状 | 原因 | 处理 |
|------|------|------|
| 401 | `glab auth` 未登录或 token 过期 | `glab auth login --hostname gitlab.addx.ai` 重新登录 |
| 403 | 无项目权限 | 联系 project owner 加权限 |
| 404 | project / iid / label 不存在 | 核对参数，确认 `glab api projects/:id` 能读到 |
| 409 | label / link 已存在 | 幂等脚本捕错继续 |
| 400 (link) | CE 用了 `blocks` 类型 | 改成 `relates_to` |
| 5xx | GitLab 服务端问题 | 重试，必要时联系 SRE |
| `could not determine project from git remote` | 当前目录不是 GitLab 仓库 | cd 到仓库根目录，或加 `-R <owner>/<repo>` 指定 |

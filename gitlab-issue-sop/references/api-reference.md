# GitLab API Reference — 本 skill 用到的 endpoint 速查

**调用方式**：本 skill 统一使用 `glab api <PATH>` 调用 GitLab REST API（`glab auth` 自动管理 token，无需手动传 `PRIVATE-TOKEN`）。`<PATH>` 里的 `:id` 会被替换为当前仓库 id，跨项目用 `-R owner/repo` 指定。

下表列出的 Path 都是 `glab api` 的参数（省略 `/api/v4` 前缀）。传 body 用 `-f key=value` 或 `--input -` 从 stdin 喂 JSON。

## Issue 管理

| 用途 | Method | Path | 关键 body 字段 |
|------|--------|------|----------------|
| 列 issues | GET | `/projects/:id/issues` | query: `labels`, `state`, `per_page`, `page` |
| 创 issue | POST | `/projects/:id/issues` | `title`, `description`, `labels` (逗号串), `assignee_id`, `due_date` |
| 读 issue | GET | `/projects/:id/issues/:iid` | — |
| 改 issue | PUT | `/projects/:id/issues/:iid` | `title`, `description`, `add_labels`, `remove_labels`, `labels` (**覆盖语义**), `state_event`=close/reopen |
| 关闭 issue | PUT | `/projects/:id/issues/:iid` | `state_event=close` |

## Comment (Notes)

| 用途 | Method | Path | 关键 body 字段 |
|------|--------|------|----------------|
| 列 comment | GET | `/projects/:id/issues/:iid/notes` | — |
| 加 comment | POST | `/projects/:id/issues/:iid/notes` | `body` |
| 改 comment | PUT | `/projects/:id/issues/:iid/notes/:note_id` | `body` |
| 删 comment | DELETE | `/projects/:id/issues/:iid/notes/:note_id` | — |

## Linked Items (Issue Links)

| 用途 | Method | Path | 关键 body 字段 |
|------|--------|------|----------------|
| 列 links | GET | `/projects/:id/issues/:iid/links` | — |
| 加 link | POST | `/projects/:id/issues/:iid/links` | `target_project_id`, `target_issue_iid`, `link_type`=**relates_to** |
| 删 link | DELETE | `/projects/:id/issues/:iid/links/:link_id` | — |

**CE 限制：** `link_type` 只能是 `relates_to`。`blocks` / `is_blocked_by` 是 Premium 功能，CE 传会返回 400。

## Labels

| 用途 | Method | Path | 关键 body 字段 |
|------|--------|------|----------------|
| 列项目可见 labels | GET | `/projects/:id/labels` | query: `include_ancestor_groups=true`（默认）、`search`, `per_page` |
| 创 label | POST | `/projects/:id/labels` | `name`, `color` (hex), `priority` (int), `description` |
| 改 label | PUT | `/projects/:id/labels/:name_or_id` | `new_name`, `color`, `priority`, `description` |
| 删 label | DELETE | `/projects/:id/labels/:name_or_id` | — |
| 列 Group labels | GET | `/groups/:id/labels` | query: `include_ancestor_groups`, `search`, `per_page` |
| 创 Group label | POST | `/groups/:id/labels` | `name`, `color`, `description` |

**name URL 编码：** scoped label 含 `::`，URL path 里要 urlencode。示例：`status::ready` → `status%3A%3Aready`。用 jq：`printf %s "$LABEL" | jq -sRr @uri`。

## Issue Boards

| 用途 | Method | Path | 关键 body 字段 |
|------|--------|------|----------------|
| 列 boards | GET | `/projects/:id/boards` | — |
| 创 board | POST | `/projects/:id/boards` | `name` |
| 列 board lists | GET | `/projects/:id/boards/:board_id/lists` | — |
| 加 list | POST | `/projects/:id/boards/:board_id/lists` | 项目可见的 `label_id`（可为 Project 或继承的 Group label） |
| 删 list | DELETE | `/projects/:id/boards/:board_id/lists/:list_id` | — |

## 常用 Query 参数

| 参数 | 含义 |
|------|------|
| `per_page=100` | 最大 100 |
| `page=N` | 分页 |
| `state=opened` / `closed` / `all` | issue 状态过滤 |
| `labels=a,b,c` | 必须全部含这些 label（AND） |
| `labels=None` | 无 label |
| `labels=Any` | 至少一个 label |
| `scope=created_by_me` | 只看自己创建 |
| `assignee_id=N` | 指定 assignee |
| `search=xxx` | 标题/描述模糊匹配 |

## 响应字段速查（issue 对象）

```json
{
  "id": 123456,            // 全局 id (用于 API 跨项目)
  "iid": 19,               // 项目内 id (对外展示的 #19)
  "project_id": 1327,
  "title": "...",
  "description": "...",
  "state": "opened",       // opened | closed
  "labels": ["type::maintenance","priority::p1","status::ready","area/data"],
  "assignees": [{"id": 42, "username": "..."}],
  "web_url": "https://gitlab.addx.ai/services/customer-care/-/issues/19"
}
```

## 认证

由 `glab auth` 管理，不需要手动传 `PRIVATE-TOKEN`。

```bash
glab auth login --hostname gitlab.addx.ai      # 首次登录
glab auth status                               # 检查状态
```

如果要换 token（如切 Personal Access Token、Project Access Token）：

- **Personal Access Token**：Settings → Access Tokens → 勾 `api` scope → `glab auth login` 时粘贴
- **Project Access Token**：Project Settings → Access Tokens → 勾 `api` + 最低 Developer role（适合 CI/CD）
- **OAuth (Group SSO)**：不在本 skill 范围

## 分页

列表 endpoint 默认 per_page=20，最大 100。glab 支持 `--paginate` 自动翻页，或通过 `-F per_page=100 -F page=N` 手动控制。

```bash
# 一次拿最多 100 条
glab api "projects/:id/issues?per_page=100&state=opened" | jq 'length'

# 自动翻页（glab 会拼接所有页）
glab api --paginate "projects/:id/issues?per_page=100" | jq 'length'
```

## 速率限制

GitLab 默认 300 req/min/user。批量脚本建议循环中加 `sleep 0.1`。429 响应时 `glab api` 返回非零退出码，脚本应捕错并按 header `Retry-After` 给出的秒数 sleep 后重试。

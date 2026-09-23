---
name: gitlab-bulk-members
description: 批量添加 GitLab 仓库或 Group 成员权限。当用户需要给多个 gitlab.addx.ai 仓库、Group，或项目默认分支里的 git submodule 仓库按人员邮箱批量授权时使用。
---

# gitlab-bulk-members

批量给 `gitlab.addx.ai` 上的项目或 Group 添加成员权限。项目目标可从默认分支递归发现同实例 Git submodule 后一并授权；Group 目标只添加 Group 成员，不展开子项目。

## Description

适用场景：

- 给一个或多个 GitLab 项目批量添加成员。
- 给一个 GitLab Group 添加成员，不遍历 Group 子项目。
- 目标项目默认分支包含 `.gitmodules`，需要自动遍历 submodule 仓库并补齐权限。
- 用户提供目标仓库或 Group、目标人员邮箱、目标权限，希望 AI 先规划再批量执行。

## Rules

### 输入要求

最少需要用户提供：

| 输入 | 支持格式 | 说明 |
|------|----------|------|
| 目标 | 项目 full path、项目 URL、Group full path、Group URL、数字 ID | 示例：`services/foo`、`https://gitlab.addx.ai/services/foo` |
| 人员 | 邮箱 | 公开输入契约只支持邮箱；user_id 仅作为邮箱唯一匹配现有用户后的内部优化 |
| 权限 | 角色名或数字 | `guest=10`、`reporter=20`、`developer=30`、`maintainer=40`、`owner=50` |

可选项：

- `recurse_submodules`：默认 `true`。
- `max_depth`：默认 `5`，递归 submodule 最大深度。
- `expires_at`：可选到期日期，用户输入统一接受 `YYYY-MM-DD`；调用 API 时按目标端点转换，见 Step 6。

### 认证策略

优先使用 `glab`：

```bash
glab auth status --hostname gitlab.addx.ai
glab api "user"
```

如果 `glab` 不可用或未登录，使用环境变量兜底：

| 变量 | 说明 |
|------|------|
| `GITLAB_URL` | 默认 `https://gitlab.addx.ai` |
| `GITLAB_TOKEN` | GitLab Personal Access Token，必须有 `api` scope |

安全规则：

- 禁止把 token 写入 SKILL.md、脚本、命令参数记录、仓库文件或临时报告。
- 禁止要求用户在聊天里粘贴 token 原文；只允许让用户设置环境变量或完成 `glab auth login`。
- 不要使用 Project Access Token 处理跨项目授权，除非用户确认它对所有目标项目都有成员管理权限。

### 执行原则

1. **默认只做 dry-run**：任何写操作前必须先输出计划表，并等待用户明确确认。
2. **先解析所有对象**：目标项目/Group、项目目标的 submodule 项目、人员、权限都解析完，再进入写阶段。
3. **幂等处理**：邮箱先查 pending invitation；如邮箱唯一匹配现有用户，再查有效成员。已有同等或更高有效权限/邀请时跳过；已有较低直接权限/邀请时升级。
4. **失败不中断整批**：单个项目、用户或 submodule 失败要记录原因，继续处理剩余项。
5. **只处理同一 GitLab 实例**：submodule URL 不属于 `gitlab.addx.ai` 时标记为 `external_submodule`，不尝试授权。
6. **不降权、不删权限**：本 skill 只添加或升级成员，不删除、不降低、不移动项目或 Group。
7. **Owner 权限谨慎**：目标权限为 `owner=50` 时必须二次确认，并说明 Group Owner 与 Project Owner 的风险。
8. **Group 不展开**：目标是 Group 时，只调用 Group members API；不要列 Group 项目、不要遍历子项目、不要扫描子项目 submodule。

### 推荐流程

#### Step 1：确定认证方式

按优先级检查：

```bash
glab auth status --hostname gitlab.addx.ai
```

如果不可用，再检查：

```bash
test -n "$GITLAB_TOKEN" && echo "GITLAB_TOKEN is set"
```

#### Step 2：标准化目标

- URL 转 full path：去掉 `https://gitlab.addx.ai/`、`/-/tree/...`、`/-/merge_requests/...` 等页面后缀。
- 先尝试解析为项目：`GET /projects/:url_encoded_full_path`。
- 项目不存在时尝试解析为 Group：`GET /groups/:url_encoded_full_path`。
- 数字 ID 需要结合用户语义判断是 project 还是 group；无法判断时先分别查询并让用户确认。

Group 目标只添加 Group 成员，因为 GitLab 权限会继承到子项目。不要展开 Group 下的项目；如果用户想给某些项目或 submodule 做 direct membership，让用户把这些项目作为项目目标单独传入。

#### Step 3：解析人员输入

公开输入只支持邮箱。不要要求用户提供 username 或 user_id，也不要在 dry-run 表里把邮箱改写成猜测出来的 username。

| 输入类型 | 路径 | 幂等检查 | 写操作 |
|----------|------|----------|--------|
| 邮箱 | 邀请路径 | `GET /:scope/:id/invitations?query=<email>`；如能唯一解析成现有用户，再额外查 members/all | invitations API 传 `email`；已有直接成员较低时改用 members API 升级 |

邮箱处理规则：

- 邮箱不保证能解析为 GitLab user_id。不要因为 `/users?search=<email>` 查不到就失败；改走 invitation 路径。
- 如果 `/users?search=<email>` 返回唯一且 email 或 public_email 完全匹配的用户，可以把它视为现有用户，同时检查 members/all。
- 如果 `/users?search=<email>` 返回多个候选，不要猜；仍按 email invitation 处理。
- 写操作前必须先查 pending invitations。已有同等或更高邀请时 `skip: already invited`；已有较低邀请时用 `PUT /:scope/:id/invitations/:email` 升级。
- 邮箱路径的优先级：有效成员权限足够 → `skip`；已有直接成员较低 → `upgrade`；已有 pending invitation 权限足够 → `already_invited`；已有 pending invitation 较低 → `upgrade_invitation`；不能解析成现有用户且没有 pending invitation → `invite_or_existing_member_unverified`。

#### Step 4：发现 submodule

仅对项目目标发现 submodule。Group 目标在添加 Group 成员后结束，不进入本步骤。

对每个项目目标：

1. 读取项目详情拿默认分支；没有默认分支时用 `HEAD`。
2. 读取默认分支 `.gitmodules`。404 表示没有 submodule，不算失败。
3. 解析每个 `url = ...`，支持绝对 URL 和相对 URL：
   - `git@gitlab.addx.ai:group/repo.git` → `group/repo`
   - `ssh://git@gitlab.addx.ai/group/repo.git` → `group/repo`
   - `https://gitlab.addx.ai/group/repo.git` → `group/repo`
   - `../lib.git`、`../../platform/foo.git` → 按父项目 remote full path 解析后归一化
4. 去掉结尾 `.git`，解析成项目。
5. 相对路径解析规则：
   - 以父项目 remote full path 作为 base。例如父项目 `apps/mobile/app` 的 base 是 `apps/mobile/app`。
   - 将相对 URL 与 base 拼接后做路径归一化：`apps/mobile/app` + `../lib.git` → `apps/mobile/lib`；`apps/mobile/app` + `../../platform/foo.git` → `apps/platform/foo`。
   - 归一化后路径不能越过 GitLab namespace 根；否则标记 `invalid_relative_submodule`。
   - 归一化后仍必须能解析为 `gitlab.addx.ai` 上的项目；否则标记 `not_found_or_no_visibility`。
6. 递归处理 nested submodule，使用 `visited_project_ids` 去重，超过 `max_depth` 标记为 `max_depth_reached`。

不要通过本地 clone 发现 submodule；优先用 GitLab API 读取默认分支，避免依赖本机 SSH key 和工作区状态。

#### Step 5：生成 dry-run 计划

必须先输出 Markdown 表格：

| Target | Type | User | Desired | Current | Action | Reason |
|--------|------|------|---------|---------|--------|--------|
| `services/foo` | project | `alice@example.com` | Developer | none / unverified | invite_or_existing_member_unverified | no pending invitation; user_id not resolvable |
| `services/bar` | project | `alice@example.com` | Developer | pending Reporter invitation | upgrade_invitation | pending invitation lower than desired |
| `platform/team` | group | `bob@example.com` | Maintainer | effective Owner | skip | already higher |

Action 值：

- `add`：无有效权限，新增 direct membership。
- `invite_or_existing_member_unverified`：邮箱无法预解析为现有 user_id 且无 pending invitation；执行时发送 invitation，但可能由 GitLab 返回 already member / already in source。
- `upgrade`：已有直接权限低于目标权限，更新 direct membership。
- `skip`：已有同等或更高有效权限。
- `already_invited`：邮箱已有同等或更高 pending invitation。
- `upgrade_invitation`：邮箱已有较低 pending invitation，需要更新邀请权限。
- `unresolved`：项目、Group 或权限无法唯一解析。
- `no_permission`：当前认证身份无成员管理权限。
- `external_submodule`：submodule 不属于 `gitlab.addx.ai`。
- `invalid_relative_submodule`：相对 submodule URL 归一化后越过 namespace 根。

表格后必须给出汇总：目标数、submodule 数、用户数、将新增数、将升级数、跳过数、失败/待确认数。

#### Step 6：确认后执行

只有用户明确确认“执行 / apply / 确认添加”后才能写。

写操作：

- 项目新增：`POST /projects/:id/members`
- 项目升级：`PUT /projects/:id/members/:user_id`
- 项目邮箱邀请：`POST /projects/:id/invitations`
- 项目邀请升级：`PUT /projects/:id/invitations/:email`
- Group 新增：`POST /groups/:id/members`
- Group 升级：`PUT /groups/:id/members/:user_id`
- Group 邮箱邀请：`POST /groups/:id/invitations`
- Group 邀请升级：`PUT /groups/:id/invitations/:email`

`expires_at` 格式：

- members API 新增/更新：传 `YYYY-MM-DD`。
- invitations API 新增：传 `YYYY-MM-DD`。
- invitations API 更新：若需要更新过期时间，将用户输入的 `YYYY-MM-DD` 转成 `YYYY-MM-DDT00:00:00Z`；如果只是升级权限且用户没有明确要求同步过期时间，不传 `expires_at`。

优先一次处理一个目标和一个邮箱，方便报告精确失败原因。即使 GitLab invitations API 支持逗号分隔邮箱，也不要合并请求；逐邮箱处理更容易保持幂等报告准确。

常见响应处理：

| 状态 | 处理 |
|------|------|
| 201 / 200 | 成功 |
| 202 或 message / queued_users 含 queued | 标记 `queued_for_admin_approval` |
| 400 且提示 already exists / already invited / already in source | 重新读取成员或邀请，按幂等规则改成 skip、already_invited、upgrade 或 upgrade_invitation |
| 403 | 标记 `no_permission` |
| 404 | 标记 `not_found_or_no_visibility` |
| 409 | 重新读取当前权限后决定 skip 或失败 |
| 429 | 按 `Retry-After` 等待后重试一次 |

### API 速查

完整 API 细节按 GitLab 官方文档动态查询；本节只列本 skill 会用到的路径。

| 用途 | Method | Path |
|------|--------|------|
| 当前用户 | GET | `/user` |
| 按邮箱尝试匹配用户 | GET | `/users?search=:email` |
| 项目详情 | GET | `/projects/:id_or_path` |
| Group 详情 | GET | `/groups/:id_or_path` |
| 读 `.gitmodules` | GET | `/projects/:id/repository/files/.gitmodules/raw?ref=HEAD` |
| 项目有效成员 | GET | `/projects/:id/members/all/:user_id` |
| Group 有效成员 | GET | `/groups/:id/members/all/:user_id` |
| 项目 pending invitation | GET | `/projects/:id/invitations?query=:email` |
| Group pending invitation | GET | `/groups/:id/invitations?query=:email` |
| 新增项目成员 | POST | `/projects/:id/members` |
| 更新项目成员 | PUT | `/projects/:id/members/:user_id` |
| 新增项目邀请 | POST | `/projects/:id/invitations` |
| 更新项目邀请 | PUT | `/projects/:id/invitations/:email` |
| 新增 Group 成员 | POST | `/groups/:id/members` |
| 更新 Group 成员 | PUT | `/groups/:id/members/:user_id` |
| 新增 Group 邀请 | POST | `/groups/:id/invitations` |
| 更新 Group 邀请 | PUT | `/groups/:id/invitations/:email` |

`glab api` 调用时省略 `/api/v4` 前缀。所有放进 URL 的动态值都必须 URL encode：full path、`:email` path 参数、`query=:email` 查询参数都要编码。例如 `services/foo` → `services%2Ffoo`，`first.last+team@example.com` 中的 `+` 必须编码为 `%2B`，不能让 query string 把它当空格。

## Examples

### Bad

用户：给 Group `platform/team` 加 `alice@example.com` Developer。

错误做法：

```bash
glab api --paginate "groups/platform%2Fteam/projects?include_subgroups=true&archived=false"
glab api -X POST "projects/<project_id>/members" -f user_id=123 -f access_level=30
```

问题：

- Group 目标不应展开子项目。
- 给每个项目加 direct membership 会绕过 Group 继承模型，增加后续维护成本。
- 假设邮箱对应的 `user_id=123`，可能加错人。
- 没有 dry-run 和用户确认。

### Good

用户：给 `services/foo` 和 submodule 加 `alice@example.com` Developer。

正确做法：

1. 检查 `glab auth status --hostname gitlab.addx.ai`。
2. 解析 `services/foo` 为项目，读取默认分支 `.gitmodules`。
3. 只保留 `gitlab.addx.ai` 下的 submodule 项目，递归去重。
4. 邮箱先查 pending invitations；若能唯一匹配现有用户，再查 members/all。
5. 输出 dry-run 表：哪些项目会 invite_or_existing_member_unverified、already_invited、upgrade_invitation、upgrade、skip、失败。
6. 用户确认后逐项调用 invitations 或 members API。
7. 输出执行报告和失败项。

## References

- GitLab 官方 API：Project members、Group members、Repository files、Projects、Groups、Users。
- 本仓库 GitLab 认证约定见根目录 `AGENTS.md`：`GITLAB_URL` / `GITLAB_TOKEN`。

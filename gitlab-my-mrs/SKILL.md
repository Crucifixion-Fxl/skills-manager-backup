---
name: gitlab-my-mrs
description: 扫描 gitlab.addx.ai 上当前用户创建的所有 open 状态 MR，汇总合并状态、CI、unresolved discussions、reviewer、年龄等信号，按紧迫度分组输出清单。当用户说"看下我的 MR"、"我还有哪些 open MR"、"扫下我的未合并 MR"、"列一下我提的 MR"、"MR 清单"时触发。
---

# gitlab-my-mrs

扫描 `gitlab.addx.ai` 上当前用户（glab 已登录账号）创建的全部 open 状态 Merge Request，按紧迫度分组汇总。**不扫任何别人的 MR**，`scope=created_by_me` 已经在服务端过滤。

前提：当前机器已通过 `glab auth status` 登录 `gitlab.addx.ai`。

---

## 执行流程

### Step 1：一次 API 调用拿到全部 open MR

GitLab 提供全局 `/merge_requests` 端点，配 `scope=created_by_me&state=opened` 即可跨仓库返回当前用户的 open MR，无需逐仓库扫。注意翻页（默认 20 条）。

```bash
# 全量拉取（自动翻页）
glab api --paginate "merge_requests?state=opened&scope=created_by_me&per_page=100&order_by=updated_at&sort=desc" > /tmp/my-open-mrs.json
```

返回是 JSON 数组。每条已包含扫描所需的全部信号，**不要**再逐条去 list 端点重查：

| 字段 | 用途 |
|------|------|
| `references.full` | 形如 `DEV/k8s!337`，报告用的稳定标识 |
| `web_url` | 浏览器直达链接 |
| `title` | MR 标题 |
| `source_branch` / `target_branch` | 分支 |
| `created_at` / `updated_at` | 算年龄和 stale 判定 |
| `draft` | true = 草稿，不等 review |
| `has_conflicts` | true = 有冲突 |
| `detailed_merge_status` | `mergeable` / `conflict` / `checking` / `ci_must_pass` / `not_approved` / `unchecked` 等 |
| `blocking_discussions_resolved` | false = 有未解决的阻塞 discussion |
| `user_notes_count` | discussion 评论总数（只能作参考） |
| `reviewers` / `assignees` | 数组，空数组 = 没人接单 |
| `project_id` / `iid` | Step 2 按需查 pipeline 用 |

### Step 2（可选）：按需查 CI pipeline 状态

list 端点**不带** `head_pipeline`，如果用户明确要看 CI，**并行** fan-out 查每条 MR 详情：

```bash
# 仅在需要 pipeline 状态时执行
jq -r '.[] | "\(.project_id) \(.iid)"' /tmp/my-open-mrs.json | \
  while read PID IID; do
    (glab api "projects/${PID}/merge_requests/${IID}" | \
       jq -r --arg key "${PID}/${IID}" \
         '"\($key)\t\(.head_pipeline.status // "none")\t\(.head_pipeline.web_url // "-")"') &
  done
wait
```

**默认不查**，因为：扫描目的是快速盘点，CI 状态对 draft / conflict / stale 都不影响结论。只有用户明确问"哪些 MR 的 CI 挂了"时才跑 Step 2。

### Step 3：分类打标

每条 MR 根据信号落到以下桶，**一条只进最严重的一个桶**（自上而下优先）：

| 优先级 | 桶 | 判定 | 行动建议 |
|-------|----|------|----------|
| P0 | **冲突** | `has_conflicts == true` 或 `detailed_merge_status == "conflict"` | rebase 或 merge target 分支 |
| P1 | **CI 失败** | （仅当 Step 2 已查）`head_pipeline.status in {failed, canceled}` | 看 pipeline 日志修复 |
| P1 | **未解决阻塞 discussion** | `blocking_discussions_resolved == false` | 逐条 reply / resolve |
| P2 | **无 reviewer** | `reviewers == []` 且 `draft == false` | 指派 reviewer |
| P2 | **Stale（> 7 天未更新）** | `updated_at` 距今 > 7 天 且不 draft | 决定继续推进还是关闭 |
| P3 | **Draft** | `draft == true` | 用户自己持有，提醒转正或关闭 |
| P4 | **待合入** | 以上都不命中 | 通常等 reviewer 点 approve / 等时机合并 |

年龄计算：`age_days = (now - created_at) / 86400`，用 `created_at` 看"攒了多久"，用 `updated_at` 看"最近有无动静"。

### Step 4：输出报告

默认用 Markdown 表格按桶分组输出：

```markdown
# My Open MRs (gitlab.addx.ai)

**扫描时间**: {now}  |  **用户**: {glab username}  |  **总数**: {N}

## P0 冲突 ({n})

| MR | 标题 | 分支 | 年龄 | 最近更新 | 说明 |
|----|------|------|------|----------|------|
| [DEV/k8s!337](https://gitlab.addx.ai/DEV/k8s/-/merge_requests/337) | docs: ... | docs/... → master | 1d | 1d 前 | 需要 rebase master |

## P1 未解决阻塞 discussion ({n})

| MR | 标题 | 未 resolve | Reviewer | 年龄 |
|----|------|-----------|----------|------|
| ... |

## P2 无 Reviewer ({n})
...

## P2 Stale > 7 天 ({n})
...

## P3 Draft ({n})
...

## P4 待合入 ({n})
...
```

- MR 标识用 `[references.full](web_url)` 做成 markdown 链接
- 每桶按 `updated_at desc` 排序，用户一眼能看到最近动过的
- **年龄和最近更新都用人类可读相对时间**（"3h"、"2d"、"3w"），不要裸露 ISO 时间戳
- 如果某桶为空，**整个章节省略**，不要留 "(0)" 的空壳
- 总数 = 所有桶之和 = `jq length /tmp/my-open-mrs.json`，用这个交叉校验分类没漏条

### Step 5（可选）：追问收口

报告输出后，主动问一句用户想怎么推进：
- "要把 P0 冲突的 MR 逐个 rebase 吗？" → 进入 gitlab-mr skill 的 Driver 流程
- "要把 Stale > 30 天的直接 close 吗？" → 逐条确认后 `glab mr close`
- "要看某条的 CI 日志吗？" → 跑 Step 2 拿 pipeline URL

**不要自动关闭、不要自动 rebase**。只是盘点，动作都要用户确认。

---

## Rules

1. **永远只扫当前用户**（`scope=created_by_me`）。不要扩大到 `assigned_to_me` 或 `all`，会把别人的 MR 混进来。
2. **不要分仓库调用 `/projects/:id/merge_requests`**。全局端点一次返回跨仓库结果，分仓库调用等于浪费 API 配额。
3. **Draft 不进"无 Reviewer"桶**。Draft 本来就是"还没准备好 review"，提示未指派 reviewer 是噪音。
4. **判断冲突以 `has_conflicts` 为准**，`detailed_merge_status` 有时会是 `unchecked`（GitLab 还没跑过检查），这种情况不算 P0 冲突。
5. **不要把 MR 描述 / discussion 内容贴进报告**。报告是清单不是正文，用户需要细节自己点链接进去。
6. **一个 MR 只进一个桶**。优先级自上而下，中了 P0 就不再看 P1/P2。

---

## Examples

### Good — 按桶分组，突出紧迫项

```markdown
## P0 冲突 (1)
| MR | 标题 | 分支 | 年龄 |
|----|------|------|------|
| [DEV/vaultwarden-deploy!6](...) | fix(staging-cn): exclude address scope | fix/sso-scopes-remove-address → main | 11d |

## P1 未解决阻塞 discussion (2)
...

## P4 待合入 (3)
...
```

### Bad — 一张大表塞所有 MR，看不出重点

```markdown
| MR | 标题 | 状态 | ... |
| !337 | docs: ... | opened | ... |
| !318 | docs(cicd): ... | opened | ... |
| !184 | docs: Fluent Bit | opened | ... |
...
```

没有优先级信号，用户还得自己过一遍，白扫。

### Bad — 替用户做决策

```
# 扫完自动：
glab mr close 184  # 3 周没动了，关了
```

违反 Rule 5 精神：扫描是盘点，不是清理。动作必须用户逐条确认。

### Bad — 分仓库扫

```bash
for repo in DEV/k8s DEV/argocd-apps ...; do
  glab api "projects/$(url-encode $repo)/merge_requests?state=opened"
done
```

浪费配额，还容易漏仓库。用全局 `/merge_requests?scope=created_by_me`。

---

## 注意事项

- **翻页**：当前用户 open MR 通常 < 50 条，`per_page=100` 一页够用；超过时 `glab api --paginate` 自动处理。
- **时区**：API 返回 UTC，计算年龄时和本地 `date -u` 对齐。报告里写相对时间用户更容易读。
- **Project 命名**：`references.full` 形如 `DEV/k8s!337` 是跨仓库稳定标识，比 `project_id` 对用户友好，报告里用这个。
- **用户自 glab 推断**：`glab auth status` 的 "Logged in as XXX" 就是要扫的用户，不需要再问。如果 `glab` 未登录，直接报错让用户 `glab auth login`。

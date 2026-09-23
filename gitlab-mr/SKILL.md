---
name: gitlab-mr
description: 在 gitlab.addx.ai 仓库创建和管理 GitLab Merge Request，驱动到真正可合并为止，并在用户明确要求“MR 已合并后清理本地 worktree/分支”或“清理 session”时安全收尾。自动生成 User Story 文档、push 前对目标 staging/main/master/release 的 MR 强制完整跑一遍 `/code-review`，并通过 `face-review-repair` 判断和安全修复真实 Review 意见；生产晋级 MR 额外执行 release parity check，证明 staging 已验证代码、后续 bugfix、Review 修复、测试、可观测性和必要配置没有遗漏。构造 MR（必须指定且只能指定一位 reviewer，并回读验证；回读通过后按 feishu-channel-rules 飞书通知 reviewer）、推送后由后台 Driver 持续读取 discussion 和 CI：只自主处理语义低风险问题，高风险或人工评论返回用户确认。Supervisor 做存活检查和完工独立审计，直到 CI 全绿、discussion 闭环、无冲突且生产晋级一致性通过。当用户说"提交 MR"、"创建 MR"、"push and create MR"、"合并到 main/master/release"、"从 staging 晋级生产"、"MR 合并后清理本地分支"、"清理 worktree"或"清理 session"时触发。
---

# GitLab MR

创建 MR 并驱动到**真正可合并**（CI 全绿 + 无 merge conflict）。核心原则：**先写文档，再提 MR，用 API 验证，循环修复直到 MR 可合并。**

前提：当前仓库的 remote 指向 `gitlab.addx.ai`，且已安装 `glab` CLI。

---

## Rules

1. **blob 链接必须指向已推送的文件** — 链接中的分支名和文件路径必须在 remote 上存在
2. **用 API 验证，不要猜** — 每一步都通过 `glab` 命令确认结果
3. **push 之前必须先过 code-review gate** — 目标分支是 `staging` / `main` / `master` / `release/*` 时，**强制**在本地完整跑一遍 `/code-review`（多角色多轮），把红线问题修掉再 push（见 Step 2.9）。其他目标分支建议跑、可被用户显式跳过。这样 CI 里的 code-review gate 能一次通过，而不是 push 后被打回。
4. **Review 修复必须走 `face-review-repair`** — 先读取真实 finding、判断是否正确，再按语义风险保护行为并修复。禁止用“AI 评论 + 少于 N 行”判定低风险。
5. **生产晋级必须证明 parity** — `release-parity-check` 只用于目标分支为
   `main` / `master` / `release/*` 的生产晋级 MR。目标分支为 `staging` 时不执行
   parity check，也不受 production promotion lineage 限制，但仍正常执行 code review、
   测试和 CI。已在 staging 验证的功能晋级生产时必须执行 Step 2.8；所有后续 bugfix
   和 Review 修复先回到 canonical feature branch，再晋级，未分类差异阻塞 MR。
6. **生产目标不存在普通 MR 模式** — 目标为 `main` / `master` / `release/*` 时，
   必须显式判定 parity 适用性，并归类为 `production-promotion`、
   `production-non-promotion`、确定性证明的 `staging-writer-cleanup` 或明确批准的 `emergency-hotfix`。
   `production-non-promotion` 仅用于仓库对该类变更没有 staging 晋级链路的场景，必须
   记录工作流证据和可核查原因；禁止用“本次不是 staging 投影”或默认普通 MR 绕过 parity。

7. **提 MR 必须指定 reviewer** — 创建前按 Step 1.5 确定且只能指定一位 reviewer；创建命令显式传 `--reviewer`，创建/更新后按 Step 4 回读验证。
8. **MR 完工后主动询问，本地清理仍须明确授权并机械校验** — Step 7 完工时，若原始请求 / 当前 thread 无明确选择，且 state history 无同一 MR / 当前 session 的完整匹配 `pending|approved|declined` 记录，必须主动询问一次“合并完成后是否清理当前 session 的本地 worktree/分支”，避免用户遗漏；已有选择或 `pending` 提醒记录时不重复询问。询问本身不构成删除授权。MR 创建、CI 全绿或可合并都不授权删除，只有用户明确同意后才按 Step 8 处理；不批量扫描、不删除远端分支。

---

## 执行流程

### Step 1：检查前置条件

```bash
# 确认 remote 指向 gitlab.addx.ai
git remote get-url origin

# 确认当前分支不是 main/master
git branch --show-current

# 明确记录目标分支；未提供时必须先向用户确认
TARGET_BRANCH="<target-branch>"
git fetch origin "$TARGET_BRANCH"

# 确认没有未提交的变更
git status
```

如果有未提交变更，先提交或 stash。

#### 1a：识别 MR 模式

读取目标分支，并将 MR 分为：

| 模式 | 判定 |
|:--|:--|
| `ordinary` | 目标为 `staging` 或其他非生产开发分支 |
| `production-promotion` | 功能已在 staging 验证，当前目标是 `main` / `master` / `release/*` |
| `production-non-promotion` | 目标是生产分支，且仓库对该类变更确实不存在 staging 晋级链路；必须记录仓库工作流证据和不适用原因 |
| `staging-writer-cleanup` | protected staging 已接管并验收 artifact writer，生产分支 MR 只退休同一 staging writer；必须通过结构化 cleanup contract 的确定性检查 |
| `emergency-hotfix` | 未经过 staging 的生产紧急修复；必须有用户明确确认、owner、原因、验证和回补计划 |

目标分支触发矩阵：

| Target branch | `release-parity-check` | 说明 |
|:--|:--|:--|
| `staging` | 不执行 | staging 是实现、修复和集成验证阶段，不要求与生产分支 parity |
| `main` / `master` | 生产晋级时强制 | 必须有 canonical base/head 和 staging verified SHA |
| `release/*` | 生产晋级时强制 | 必须有 canonical base/head 和 staging verified SHA |
| 其他开发分支 | 不执行 | 走普通 MR 流程 |
| 紧急直修到生产分支 | 例外 | 显式声明 hotfix、owner、原因、验证和回补计划 |

生产目标不存在普通 MR 模式。目标为 `main` / `master` / `release/*` 时，按以下互斥
顺序判定，不能任选一个较宽松的模式：

1. 先检查仓库文档、远端分支和近期同类 MR，判定该类变更是否存在 staging 晋级链路。
2. 存在 staging 晋级链路：完整 verified SHA 用 `production-promotion`；只退休已验收 writer 可按 [`cleanup contract`](reference/staging-writer-cleanup.md) 校验；否则停止或批准 hotfix。
3. 确实不存在 staging 晋级链路：只能用 `production-non-promotion`，并记录分支查询、
   工作流文档或同类 MR 作为 `staging_flow_evidence`。
4. 无法判断是否存在 staging 链路：停止确认，不能默认 parity 不适用。

“本次改动不是 staging 投影”本身不是 `production-non-promotion` 的合法理由。只要仓库
对该类变更存在 staging 链路，就不能用该模式绕过 parity。
自动化执行器应复用 `lib/release_workflow_policy.py` 的分类结果，不得另写更宽松的
生产模式分支；cleanup 只有绑定 contract/GitLab/Git tree 证据后才分类，caller prose 无效。

生产晋级模式必须确定：

```text
主功能分支（canonical feature branch）
最后一个绑定 staging 验证结果的 canonical MR IID
candidate MR IID
candidate/release target
```

优先从 Git 历史、已有 staging MR、技术/测试文档和用户给出的 MR 链接发现。
无法确认验证 MR 时停止生产晋级；ref 和 SHA 由 Step 4 脚本从 GitLab API 读取。

### Step 1.5：确定 reviewer（创建 MR 的必填项）

- 每个 MR 必须且只能指定一位 reviewer。优先使用用户在本次任务已指定的唯一 reviewer；否则沿用已有 MR 的唯一 reviewer，或仓库明确配置的唯一默认 reviewer。已有明确人选无需重复确认。
- 用户、已有 MR 或默认配置提供多位人选且无法按上述优先级确定唯一人选时，询问用户选择一位；不取列表第一位、不传多人列表或重复 `--reviewer`。
- 仍无人选时，先询问用户指定 GitLab username；可以继续准备文档和本地审查，但不得创建无 reviewer 的 MR（含 Draft）。CODEOWNERS 或历史 MR 可提供候选，不能据此任意选人。
- 将姓名或别名解析为唯一的 GitLab username / user ID，并确认账号有效且能访问目标项目；结果有歧义、不可用或无法核验时，先补齐信息，不猜测、不降级为空 reviewer。不得以多账号分配绕过身份歧义。
- 用 `glab api --method GET users -f username='<username>'` 精确查询账号；记录唯一预期 username / ID，供 Step 4 回读比对。项目访问权限结合项目成员（含继承成员）和可见性核验。
- assignee、描述中的 @mention 和本地 code-review agent 均不能代替 GitLab 的 reviewer 字段；指定 reviewer 也不代表已获得 approval。

### Step 2：确保文档存在

MR 描述建议包含与变更相关的文档链接（User Story / 设计文档），方便 reviewer 理解上下文。**优先使用 GitLab blob 链接**，指向仓库内的文档文件。

#### 2a：查找或创建文档

检查仓库中是否已有相关文档：

```bash
# 查找可能相关的文档
ls docs/plans/ docs/04-user-stories/ 2>/dev/null
```

如果没有相关文档，为本次 MR 创建一个：

- 文档放在 `docs/requirements/<iid>/plan.md`（**本次实现计划——路径必须带 issue iid，否则反查不到 issue**）、
  `docs/design/<模块>/`（模块设计）或 `docs/04-user-stories/<feature-name>.md`（User Story）
  <br>旧路径 `docs/plans/YYYY-MM-DD-<feature-name>.md` 是 `superpowers:writing-plans` 的默认值，**本组织已覆盖为按 iid 组织**（见 `addx:architect`）；存量文件不迁移。
- 文档内容应**覆盖本次 MR 的核心变更**，方便 reviewer 理解
- 文档不需要很长，但要说清：做了什么、为什么做

#### 2b：提交并推送文档

```bash
git add docs/plans/<doc-file>.md
git commit -m "docs: add <feature> design document"
```

### Step 2.8：生产晋级一致性检查

仅当目标分支是 `main` / `master` / `release/*` 且属于生产晋级模式时执行。
目标为 `staging` 时无条件跳过本步骤。先读
[`reference/release-parity-check.md`](reference/release-parity-check.md)。
此阶段先确认 canonical 分支最后一个绑定 staging 验证结果的已合并 MR，并创建
`release-contracts/<feature>.yaml`。contract 必须提交到 candidate 分支，不能从工作区
临时文件读取。确定性脚本会从 GitLab API 自动追溯该 canonical 分支最早 staging MR 的
`diff_refs.base_sha`，并在 Step 4 绑定 candidate MR 当前 HEAD；通过前不得启动 Driver。

没有环境差异或外部 gate 时可省略 `--contract`；存在任一差异/gate 时必须提交 contract。

同时执行 `git cherry` 和 `git range-diff` 检查 commit/冲突处理语义。检查范围必须覆盖：

- 初始功能代码。
- staging 后的 bugfix 和 Review 修复。
- 测试、可观测性、migration 和部署配置。
- 需要在外部平台完成的 gate 及其真实证据。

环境配置允许不同，但必须在 per-feature release contract 中声明 path、reason、owner，
并为每个候选差异路径绑定精确的 `required_content`；临时差异还要有 expires。任一缺失、
额外或精确变更不同都阻塞。脚本只验证代码和 contract，不接受 contract 自报
`status: ready`；外部 gate 必须由 Auditor 从证据 URL 实时读取。报告中记录脚本解析出的
四个完整 SHA 和 contract blob OID/SHA-256；目标分支更新后必须基于新 target SHA 重跑。

### Step 2.9：提交前完整 code-review + 安全修复（push 前的 gate）

**触发条件**：目标分支是 `staging` / `main` / `master` / `release/*` → 强制执行；其他目标分支 → 建议执行，用户可显式说"跳过 review"才略过。

> 目的：CI 里的 `code-review` gate 迟早要跑，与其 push 后被打回再来一轮，不如在本地先跑一遍、把不达标的地方就地修掉，让 CI 一次过。

#### 2.9a：完整跑 `/code-review`

在当前分支（已 commit 的状态）上 invoke `code-review` skill，走它的「本地已提交」场景，diff 基线用 **MR 的目标分支**（`git diff <target-branch>..HEAD`，目标是 `release/*` 时就是那条 release 分支，不是写死的 `main`），**完整执行多角色多轮审查**，不走任何精简模式。拿到它的 5 段输出（结论 / 评分 / 红线问题 / 建议改进）。

#### 2.9b：按 finding 处理

把 code-review 的 finding 分三类：

| 类别 | 例子 | 处理方式 |
|:-----|:-----|:---------|
| **机械可自动修** | 格式、import、明确的断链、局部常量提取 | Invoke `face-review-repair` 确认为 `mechanical` 后修复、验证、commit |
| **红线问题（必须修，不可接受）** | 明文 secret、缺关键测试/设计/可观测性、鉴权/事务/数据一致性风险等 | **必须修复后才能 push，没有"本次接受"选项**。Invoke `face-review-repair` 判断真实问题和语义风险，再调对应专项 skill。用户拒绝修则停止 |
| **非红线优化项（P1/P2）** | 测试边界、ADR/架构图改进等 | 先用 `face-review-repair` 判断 valid/false-positive/pre-existing；valid 项再给用户 ①现在补 ②本次接受并写 `## 已知 gap` ③不适用 |

Review 建议不是实现规范。`face-review-repair` 必须先区分“意见是否成立”和“建议实现
是否安全”，并用 characterization/reproducer test 保护行为。每补完一批后 commit。

#### 2.9c：复跑直到达标

修完后**重新跑 2.9a**。生产晋级模式中，只要产生新 commit，还必须重新跑
Step 2.8；如果新 commit 晚于 staging-verified SHA，先完成受影响的 staging 回归并更新
verified SHA。放行条件：code-review 结论是「通过」（≥8.0），或「有条件通过」
（6.0-7.9）。**只要还命中任一红线，无论用户什么态度都不准进 Step 3**。

> 如果用户一开始就说"先 push 再说 / 跳过 review"，且目标分支不是 staging/main/master/release/* → 跳过 2.9，但在 MR 描述里标注"未过本地 code-review"。目标分支是这些生产向分支 → 不允许跳过，向用户说明这是 Rule 3；CI 里的 code-review gate 也会拦。

### Step 3：推送分支

```bash
git push -u origin <branch-name>
```

### Step 4：创建或更新 MR

#### 查看是否已有 MR

```bash
glab mr list --source-branch <branch-name>
```

#### 如果没有 MR，创建一个

分析 `git log "origin/$TARGET_BRANCH"..HEAD` 中的所有 commit 来撰写 MR 标题和描述。

blob 链接格式：`https://gitlab.addx.ai/<group>/<project>/-/blob/<branch>/<filepath>`

若本次任务来自已启用 GitLab→Buzz 同步的 Buzz 频道话题（你是在**当前话题**里被要求提这个 MR），**先做 origin 写入前检查，通过了才把下面两行追加到描述末尾**（Channel UUID 与话题根取当前任务，禁止手填其它频道或其它 Thread）。话题根是人类消息也一样写：同步会把这个 MR 的事实回复到讨论它的那个话题里，不再另开门牌线程（ADR-0014）：

```
buzz://message?channel=<channel-uuid>&id=<64-hex-root>&thread=<64-hex-root>
<!-- gitlab-buzz-origin:v1 {"channel_id":"<channel-uuid>","root_event_id":"<64-hex-root>"} -->
```

第一行给人从 GitLab 连回 Buzz；第二行给同步脚本。`<64-hex-root>` 写话题的**顶层根**，不要写话题中间某条回帖的 id。脚本认描述和人类评论里的 origin；多条有效 origin 都回复。尚未有 binding、且 MR 未关联 GitLab Issue 时，第一条 origin 作为 binding；目标可以是本频道 Desk 已发过的顶层门牌／事实，也可以是人发的顶层消息。格式见 `buzz-agent-setup` 的 GitLab→Buzz 协议「origin 绑定」。

- `<group>/<project>` 从 `git remote get-url origin` 解析
- `<branch>` 是当前分支名
- `<filepath>` 是文档的仓库内路径

#### origin 写入前检查

- 根事件同时满足：kind 9、`h` 标签恰好一个且是本频道、没有 `e` 标签（顶层，不是回帖）、`buzz messages thread --channel <channel-uuid> --event <root>` 读得到（在返回里找 `id` 等于该根的事件逐条看）——**两行都写**，根是人类消息（最常见）也行。根若是本频道 Desk 发的（`pubkey` 等于同步配置的 `publisher_pubkey`），内容必须是门牌（末行 GitLab URL）或 `[gitlab-notify:v1]` 事实；人、Feishu 镜像身份和别的 Agent 发的顶层消息不看内容。
- 读不到、是回帖、是别的 Agent 的无关发言、不是在当前话题里被要求提的，或任何一条核对不了：**两行都省略**，让同步自己给这个 MR 建门牌。
- **裸的 `buzz://message?…` 链接只是提示**：完整且指向本频道 Desk 门牌／事实的会被当作 origin（反引号、括号里也一样），指向人类消息或占位符写法的会被忽略（不会绑定到那个话题，要绑定只有 HTML 注释可以）。仍不要在 MR 描述或评论里为了「引用一下」贴指向人类消息的 `buzz://message` 链接；要引用就写频道名加一句话。
- 写错的后果：**格式**非法的 HTML 注释会让**该 MR 停摆**（2026-09-19 事故是整个频道停摆，现在只停该对象）；格式合法但根不可用的注释不会停摆，MR 自开门牌，同步报告里记一条 `origin_fallbacks`。
- 完整规则与错误修复见 `buzz-agent-setup/references/gitlab-buzz-sync.md`「origin 写入前检查」。

```bash
glab mr create \
  --title "<简洁标题，<70字符>" \
  --reviewer "<已核验的唯一username>" \
  --description "$(cat <<'EOF'
## 变更说明
<基于所有 commit 的变更总结，2-3 条>

## 相关文档
- User Story 文档：https://gitlab.addx.ai/<group>/<project>/-/blob/<branch>/docs/plans/<doc>.md
- Tech Design 文档：<如有则填，否则删除此行>

## 变更类型
- [x] <对应类型>
EOF
)" \
  --target-branch "$TARGET_BRANCH" \
  --push
```

#### 如果已有 MR 但描述缺少文档链接

```bash
glab mr update <mr-id> --target-branch "$TARGET_BRANCH" --description "$(cat <<'EOF'
<包含 blob 链接的完整描述>
EOF
)"
```

#### 已有 MR 的 reviewer

无论是否需要修改描述，都检查 reviewer。现有 reviewer 恰好一位且符合本次人选时不重复写；缺失、用户已指定不同人选，或已有多人而唯一人选已明确时，用单个 username 设置完整 reviewer 分配：

```bash
glab mr update <mr-id> --reviewer '<已核验的唯一username>'
```

不使用 `+` 追加，不传逗号列表或重复 flag。已有多人且尚未确定保留谁时，先询问人选；不得静默删减。仅更新描述时保留有效的唯一 reviewer。

#### 创建/更新后必须回读 reviewer

```bash
glab api "projects/:id/merge_requests/<mr-id>"
```

核验响应是目标 MR，并确认 `reviewers` 是恰好含一个元素的数组，且该元素的 username / ID 与 Step 1.5 的唯一人选一致。空响应、字段缺失、人数不是一位、身份不符或请求失败均不算通过，不能进入 Step 5 或声称提交完成。写请求结果不明时先回读已有 MR，避免重复创建；服务端拒绝指定人选或权限不足时报告具体原因，不擅自换人。

最终交付时同时报告 MR URL 和实际回读的 reviewer。命令语义见 [GitLab CLI create](https://docs.gitlab.com/cli/mr/create/) / [update](https://docs.gitlab.com/cli/mr/update/)，回读字段见 [Merge requests API](https://docs.gitlab.com/api/merge_requests/)。

#### 回读通过后的 reviewer 飞书通知

回读验证通过后，若本次会话首次指定或变更了 reviewer，按 `feishu-channel-rules`
通知该 reviewer：MR 链接、一句话事由、指定的 reviewer（即回读验证到的那位）。
渠道、身份、登录与收件人解析由 `feishu-channel-rules` 定义，本 skill 不复制；
幂等键按（MR × reviewer × 指派变化，含前任）构造，Driver 后续轮次重复回读
不重复通知。回读未通过不得通知；通知失败不改动 MR 状态，如实报告
「reviewer 已设置、未通知 + 原因」。

#### 创建或更新后统一验证

无论 MR 是新建还是已存在，都必须先回读真实 target。`production-promotion` 还必须让
脚本直接读取 GitLab MR/branch API 并生成绑定当前 candidate HEAD 的报告；通过前不得
启动 Driver：

```bash
MR_JSON=$(glab api "projects/:id/merge_requests/${MR_IID}")
ACTUAL_TARGET=$(printf '%s' "$MR_JSON" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['target_branch'])")
HEAD_SHA=$(printf '%s' "$MR_JSON" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])")
test "$ACTUAL_TARGET" = "$TARGET_BRANCH"

if [ "$MR_MODE" = "production-promotion" ]; then
  PARITY_REPORT="/tmp/gitlab-mr-parity-${MR_IID}.json"
  PARITY_ARGS=()
  test -z "$CONTRACT_PATH" || PARITY_ARGS=(--contract "$CONTRACT_PATH")
  uv run <skill-path>/scripts/release_parity_check.py \
    --project-path "$PROJECT_PATH" \
    --canonical-verification-mr "$CANONICAL_VERIFICATION_MR_IID" \
    --candidate-mr "$MR_IID" \
    "${PARITY_ARGS[@]}" \
    --json > "$PARITY_REPORT"
fi
```

`CANONICAL_VERIFICATION_MR_IID` 必须是主功能分支最后一次通过 staging 验证的已合并 MR。
脚本会从其 source branch 的 staging MR 历史中自动选择最早 MR 的
`diff_refs.base_sha`，并直接 fetch 三个 GitLab MR ref 和当前生产目标分支；调用方不再
手填 canonical/candidate ref 或 SHA。

### Step 5：派后台 Driver agent 接手

MR 创建/更新后，不在前台循环 `glab ci status`。**派一个后台 Driver agent 去驱动 MR 到真正可合并**，父会话只负责接收返回和问用户。
详细的 Driver / Supervisor / Auditor 提示词契约、状态文件规范、恢复协议在 **`reference/background-drive.md`**，派 agent 前先读它。

#### 5a：写初始 state 文件

使用 `scripts/init_drive_state.py --help` 按已确认模式初始化 state。所有值必须来自
Step 1a/4 的真实读取结果，不能留下默认空字段：

```bash
STATE_FILE="/tmp/gitlab-mr-drive-${MR_IID}.json"
uv run <skill-path>/scripts/init_drive_state.py \
  --mr-iid "$MR_IID" \
  --project-path "$PROJECT_PATH" \
  --branch "$BRANCH" \
  --target-branch "$TARGET_BRANCH" \
  --mr-mode "$MR_MODE" \
  --head-sha "$HEAD_SHA" \
  --staging-flow-exists "$STAGING_FLOW_EXISTS" \
  --staging-flow-evidence "$STAGING_FLOW_EVIDENCE" \
  <mode-specific MR-IID/contract/non-promotion/emergency arguments> \
  --output "$STATE_FILE"
```

脚本复用 `release_workflow_policy.py` 校验目标分支和模式。`production-promotion` 必须填入
`--canonical-verification-mr "$CANONICAL_VERIFICATION_MR_IID"`、
`--candidate-mr "$MR_IID"` 及可选 contract；初始化器会自行重跑 GitLab-aware parity，
并校验真实 Git HEAD 与 GitLab candidate MR SHA 一致。`production-non-promotion` 必须填入
`staging_flow_exists=false`、工作流证据和不适用原因；cleanup 按 reference 传入
`staging_flow_exists=true` 和 `--cleanup-contract`；hotfix 填批准/owner/验证/回补。

#### 5b：派 Driver（background Agent）

从 `reference/background-drive.md` 第 2 节读 Driver prompt 模板，填入 `{{MR_IID}}` /
`{{PROJECT_PATH}}` / `{{BRANCH}}` / `{{TARGET_BRANCH}}` / `{{PROMOTION_CONTEXT}}` /
`{{RESUME_DECISIONS}}`（首轮传 `(none)`），然后：

```bash
PROMOTION_CONTEXT=$(python3 - "$STATE_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    state = json.load(source)
print(json.dumps({
    "target_branch": state["target_branch"],
    "mr_mode": state["mr_mode"],
    "promotion": state["promotion"],
    "cleanup": state["cleanup"],
    "emergency": state["emergency"],
}, ensure_ascii=False))
PY
)
```

必须把这段完整 JSON 原样填入 Driver prompt，不能只传
`promotion.enabled`。后续每次恢复 Driver 和派 Auditor 前都从最新 state 重新生成。

```
Agent(
  subagent_type: "general-purpose",
  run_in_background: true,
  description: "Drive MR !<iid> to mergeable",
  prompt: <填充后的 Driver 模板>
)
```

记下返回的 task id，后面 Supervisor 告警或用户中断时要用到。

#### 5c：调度 Supervisor liveness loop

用 `ScheduleWakeup` 每 600 秒醒一次。醒来时派一个前台 Supervisor（liveness mode，见 reference 第 3 节）观察 MR 状态、更新 state 文件。三次无变化告警。

```
ScheduleWakeup(
  delaySeconds: 600,
  prompt: "[gitlab-mr liveness] MR !<iid>: run Supervisor liveness check",
  reason: "poll MR state for Driver liveness"
)
```

### Step 6：处理 Driver 返回

Driver 返回时是一个 JSON（见 reference 第 2 节返回格式）。按 `status` 分支：

| status | 处理 |
|---|---|
| `done` | 进入 Step 7 完工审计 |
| `awaiting_confirmation` | 按 reference 第 5 节 Resume protocol：把 `pending_items` 格式化给用户，收集决议，bundle 到 `{{RESUME_DECISIONS}}`，回到 Step 5b 派新 Driver |
| `stuck` | 把 `stuck_context` 的最近 3 次 pipeline 日志尾部 + 当前 snapshot 展示给用户，问下一步 |
| `timeout` | 展示当前 snapshot，问用户是否继续（重派）还是中止 |

**Supervisor liveness 告警**（`no_change_streak >= 3`）时，父会话：

```bash
# 通过 Agent 工具的 task id 停掉 Driver
# （在 Claude Code 中用 TaskStop <id>）
```

然后把 snapshot 交给用户决定重派还是中止。

每次处理完，在 state 文件的 `history` append 一条记录。

### Step 7：完工审计

Driver 返回 `done` 不代表真完工。**派独立的 Auditor 前台 agent 复核一遍**（见 reference 第 4 节）：

```
Agent(
  subagent_type: "general-purpose",
  run_in_background: false,
  description: "Audit MR !<iid> mergeability",
  prompt: <填充后的 Auditor 模板>
)
```

- Auditor 必须返回 `background-drive.md` 定义的结构化 JSON。
- 父会话先按 Step 4 回读 reviewer，验证通过后再用 `scripts/validate_drive_audit.py` 校验该 JSON 与最新 state；只有
  `AUDIT PASS` 才写 `completed: true`、取消 liveness 并报告 MR URL。
- 校验失败时把准确错误放入新 `{{RESUME_DECISIONS}}`，回到 Step 5b 再派一轮 Driver。

生产目标的 Auditor 还必须验证 `mr_mode` 与目标分支一致；`production-promotion` 在
当前 HEAD 重跑 Step 2.8 并实时核验外部 gate，`production-non-promotion` 核查
staging 流程不存在证据；cleanup 重跑 attestation 并核查 pipeline/branch policy；hotfix 核查批准/回补。
**只有 Driver done + 结构化 Auditor 结果通过机械校验 + deterministic code parity
PASS 及外部 gate 实时核验通过（适用时）同时成立，才向用户报告 MR 可合并。**

报告完工时，先检查原始请求、当前 thread 和 state history。若原始请求 / 当前 thread
没有明确选择，且 history 没有 MR/worktree/branch 完全匹配的
`decision=pending|approved|declined` 记录，在同一条消息末尾**主动询问一次**：“MR 合并
完成后是否清理当前 session 的本地 worktree/分支？”已有选择或完整匹配的 `pending`
提醒记录时不再询问。此时 CI 全绿、
`AUDIT PASS` 或 `mergeable` 只代表 MR 已就绪，**不得执行清理**。若 GitLab 已回读为
`state=merged`，改问“是否现在清理”。

发送询问以及收到同意或拒绝时，分别向现有 drive state 的 `history` 追加一条
`kind=post_merge_local_cleanup` 记录，至少包含 `mr_iid`、当前 session 的绝对
`worktree`、`branch`、`decision=pending|approved|declined` 和 `ts`。恢复时只认
MR/worktree/branch 三者完全匹配的最后一条记录；记录缺失或不完整时不得推断用户选择。
`declined` 停止清理，`pending` 不重复提醒；用户在尚未合并时给出 `approved`，实际执行前
仍必须重新回读 GitLab 并满足 Step 8 的全部门禁。

### Step 8：用户明确同意后的合并后本地清理（条件执行）

Step 7 在尚无选择时的主动询问是必做提醒，不是自动清理。**只有用户明确同意**清理当前 session /
worktree / 本地分支，并且调用方能从当前会话确认目标正是本 session 创建或复用的
worktree 时才执行。用户可以在 MR 尚未合并时同意，但执行前必须实时确认已经合并；
详细门禁与命令见
[`reference/post-merge-local-cleanup.md`](reference/post-merge-local-cleanup.md)。

先运行 helper 的默认预检模式；只有返回 `status=ready`，才在同一明确授权下加
`--execute`。helper 必须验证 GitLab `state=merged`、MR source branch/SHA 与本地完全
一致、MR 目标等于仓库默认主干、worktree 已注册且不含 tracked/untracked/ignored 内容、
目标不是主 checkout，并保留另一个 worktree 管理仓库。
任一条件失败都报告 `status=blocked` 的准确原因，不改用 `--force`、不换目标、不删除
远端分支。该 source SHA 绑定同时覆盖 squash merge；禁止只用
`git branch --merged <target>` 作为合并证明。

---

## 示例

### 完整流程示例

```
# Step 1-4: 创建 MR
glab mr create --title "feat: add retry logic" --description "..." \
  --reviewer "<已核验的username>" --target-branch "$TARGET_BRANCH" --push

# Step 5: 派后台 Driver + Supervisor liveness loop
# Step 6: 收到 Driver 返回 awaiting_confirmation → 问用户 → 派下一轮
#        收到 Driver 返回 done → 进入 Step 7
# Step 7: Auditor 独立验证 → validate_drive_audit AUDIT PASS → 报告 MR URL
```

---

## Bad/Good 示例

### Bad — Driver done 就声称完成

```
Driver → status: done
# → "MR !42 可合并！"  ← 没派 Auditor，可能是 Driver 误判
```

### Bad — 按评论行数判断风险

```text
AI bot 建议改 1 行事务/数据源代码
→ 因为少于 20 行直接自动修复
```

问题：语义风险与行数无关。必须 invoke `face-review-repair`。

### Bad — 手工从记忆拼生产分支

```text
从 staging 相关 MR 中挑几个 commit cherry-pick 到 release
→ 忘记后续 Review 修复和一个生产 profile 配置
```

问题：生产晋级必须以 canonical branch + verified SHA 为输入执行 parity check。

### Bad — 替用户 resolve 人工 reviewer 的 discussion

```
Driver 改完代码就自动 PUT discussions/<id>?resolved=true  ← 违反规则，人工 discussion 只能 reply
```

### Bad — 贴无关文档链接

```
glab mr create --description "User Story: .../README.md"  # 与变更无关，对 reviewer 无用
```

### Bad — 目标 staging 却跳过本地 code-review，靠 CI 打回

```
git push → glab mr create --target-branch staging
# CI 的 code-review gate 报红线：已实现 handler 无 L2/L3 test
# → 又得改一轮、再 push、再等 CI  ← 应该在 Step 2.9 就修掉
```

### Good — push 前先过 code-review，CI 一次过

```
Step 2.9: 本地跑 /code-review → 红线：handler 无 E2E + TODO.md 未同步
→ TODO.md 自动补上并 commit
→ E2E 缺失：问用户 → 用户同意 → invoke testing-strategy 补 L3 用例 → commit
→ 复跑 /code-review → 通过 8.5/10
→ Step 3 push → CI code-review gate 绿
```

### Good — 全链路驱动到真正可合并

```
创建 MR → 派后台 Driver + liveness → Driver 自主修低风险 →
→ 高风险攒清单返回 → 用户逐条决议 → 重派 Driver →
→ Driver done → Auditor 独立验证 VERIFIED → 报告 MR URL
```

---

## 已知限制

- **父会话关闭 = Driver 终止**。`run_in_background: true` 的 Agent 不跨 session 持久。需要跨会话持久的场景，请改用 `schedule` skill 另起轨道。
- **跨仓库 side effect** 一律进待确认清单：DB migration、sibling 仓库修改、外部系统调用，Driver 不自行处理。
- **人工 reviewer 的 discussion 不由 Driver resolve**。Driver 即使已按 reviewer 意见改了代码，也只 `reply "Fixed in <sha>"`，让 reviewer 自己点 resolve。AI bot 的 discussion 才由 Driver 调 resolve API。
- **Review 修复不按行数自动分级**。Driver 必须使用 `face-review-repair` 的语义风险分类。
- **生产晋级中的新修复不能只留在 release 分支**。先回 canonical branch 并更新验证证据。
- **"Request changes" 无具体 discussion** 的情况超出 Driver 处理范围，会升级给用户跟。
- Driver 的 **60 min 超时** 和 **stuck 3 轮无进展** 不是失败，是设计上的"喘口气"，都会返回完整上下文让你判断。

---

## 注意事项

- blob 链接中的分支名如果含 `/`（如 `feat/my-branch`），GitLab 能正确解析
- 如果 pipeline 长时间不触发，可以手动运行：`glab ci run`
- 派 Driver 前一定要读 `reference/background-drive.md`，prompt 模板的占位符必须全部填充

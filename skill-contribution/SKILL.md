---
name: skill-contribution
description: 引导用户向 engineering/skills 仓库贡献 Skill（创建新 Skill 或更新现有 Skill）。覆盖环境搭建、Git 操作、Skill 编写、格式验证到提交 MR 的全流程，面向非研发用户零 Git 操作。当用户说"我想贡献一个 Skill"、"帮我创建一个新 Skill"、"把 XX 做成 Skill 加到仓库"、"这个 Skill 的规则不对"、"帮我更新这个 Skill"，或在使用 Skill 过程中发现问题想修改时触发。
---

# skill-contribution

引导用户向 `engineering/skills` 仓库贡献 Skill 的全流程管家。用户不需要会 Git — AI 代执行所有 Git 操作。

## Description

本 Skill 是一个路由器 + 环境搭建器，覆盖三个场景：

| 场景 | 路由目标 |
|------|---------|
| 创建新 Skill（工具类：把内部工具做成 Skill） | `tool-skill-creator` |
| 创建新 Skill（通用类：审查规则、流程指南等） | `skill-creator`（Anthropic 社区） |
| 使用中更新现有 Skill | 直接修改，无需走完整创建流程 |

本 Skill 专注于环境搭建和 Git 流程管控，Skill 内容的编写委托给对应的 creator skill。

## Rules

### Rule 1 — Phase 0: 环境检查

收到贡献请求后，先确保用户环境就绪。以下检查全部由 AI 执行，用户只需在必要时做一个操作（粘贴 SSH 公钥到 GitLab）。

```bash
# 1. 检查 git
git --version
# 没有 → 引导安装：macOS 执行 xcode-select --install

# 2. 检查 SSH key
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ls ~/.ssh/id_rsa.pub 2>/dev/null
# 没有 → 生成：
ssh-keygen -t ed25519 -C "用户邮箱" -f ~/.ssh/id_ed25519 -N ""
# 输出公钥，告诉用户：
# "请把下面这段公钥粘贴到 GitLab：
#  打开 https://gitlab.addx.ai/-/user_settings/ssh_keys
#  点 'Add new key'，粘贴后点 'Add key'"

# 3. 测试连通性
ssh -T git@gitlab.addx.ai -o StrictHostKeyChecking=accept-new
# 失败 → 排查：key 未添加？网络问题？

# 4. 检查仓库是否已 clone
# 在用户常用目录（~/workspace、~/projects、~）搜索 skills 仓库
find ~/workspace ~/projects ~ -maxdepth 3 -name "skills" -type d 2>/dev/null | head -5
# 没有 → clone：
git clone git@gitlab.addx.ai:engineering/skills.git ~/workspace/skills
```

环境就绪后告诉用户："环境准备好了，接下来帮你创建/修改 Skill。"

### Rule 2 — Phase 1: 创建分支

在 skills 仓库目录下操作：

```bash
cd <skills-repo-path>
git checkout main
git pull origin main
git checkout -b feat/<skill-name-or-change-description>
```

分支命名规则：
- 新建 Skill：`feat/<skill-name>`（如 `feat/zendesk`）
- 更新 Skill：`fix/<skill-name>-<change>`（如 `fix/grafana-add-alert-rule`）

### Rule 3 — Phase 2: 路由决策

根据用户意图路由到正确的流程：

**判断逻辑：**

```
用户想做什么？
  ├─ 更新现有 Skill → 直接进入 Rule 4（快速修改流程）
  └─ 创建新 Skill
       ├─ 是内部工具类？（SaaS/管理后台/监控平台等）
       │    └─ 是 → 委托 tool-skill-creator skill
       └─ 否（审查规则/流程指南/编码规范等）
            └─ 委托 skill-creator skill（Anthropic 社区版）
```

**工具类 vs 通用类的判断标准：**

| 信号 | 类型 |
|------|------|
| 用户提到具体工具名（Grafana、Sentry、XX 后台） | 工具类 → `tool-skill-creator` |
| 用户提到 API、登录、查询数据 | 工具类 → `tool-skill-creator` |
| 用户提到代码审查、测试策略、文档规范 | 通用类 → `skill-creator` |
| 用户说"这个 Skill 有问题"或在使用中反馈 | 更新 → Rule 4 |

**委托时的上下文传递：**

调用对应 skill 时，把已完成的环境信息传递过去：
- 仓库路径：`<skills-repo-path>`
- 分支名：`feat/<skill-name>`
- Skill 目标目录：`skills/<skill-name>/SKILL.md`

### Rule 4 — 快速修改流程（使用中更新现有 Skill）

用户在使用 Skill 过程中发现问题想修改，不需要走完整的 creator 流程。

1. **确认修改内容** — 问用户具体要改什么（加规则？改流程？修正错误？）
2. **定位文件** — 找到对应的 `skills/<skill-name>/SKILL.md`
3. **直接修改** — 按用户要求编辑 SKILL.md
4. **继续到 Phase 3**（验证 + 提交）

这个流程的价值在于低摩擦 — 用户正在用 Skill，发现问题马上能改，不用切换上下文走一遍完整创建流程。

### Rule 5 — Phase 3: 验证 + 提交

无论是新建还是更新，最后都走这个流程：

**格式验证：**

```bash
uv run python scripts/validate.py --skill skills/<skill-name>
```

如果报错，自动修复后重新验证，直到 0 errors。

**提交变更：**

```bash
git add skills/<skill-name>/
git commit -m "feat(skills): add <skill-name>"  # 新建
# 或
git commit -m "fix(skills): update <skill-name> - <变更摘要>"  # 更新
```

**推送并创建 MR：**

委托 `gitlab-mr` skill 完成 MR 创建。MR 描述中建议附上与变更相关的文档链接（User Story 或设计文档），方便 reviewer 理解上下文。

如果用户没有现成的 User Story 文档，帮用户在仓库 `docs/04-user-stories/` 下创建一个简单的文档，然后用 GitLab blob 链接引用。

## Examples

### Bad

```
用户："帮我把 Zendesk 做成 Skill 加到仓库里"
AI：直接开始写 SKILL.md，没检查环境，没创建分支，写完后告诉用户"请自己 git push"
→ 用户根本不会 git，卡住了
```

```
用户（使用 grafana skill 时）："这个 Skill 里的 Dashboard 命名规则不对，应该是 svc-<name> 不是 <name>-dashboard"
AI：调用 tool-skill-creator 走完整 5 个 Phase
→ 杀鸡用牛刀，用户只想改一行规则
```

### Good

```
用户："帮我把 Zendesk 做成 Skill 加到仓库里"
AI：Phase 0 检查环境 → 发现没有 SSH key → ssh-keygen 生成 → 输出公钥让用户粘贴到 GitLab
→ clone 仓库 → 创建 feat/zendesk 分支 → 判断是工具类 → 委托 tool-skill-creator
→ 完成后跑 validate.py → git commit + push → 委托 gitlab-mr 创建 MR → 返回 MR 链接给用户
```

```
用户（使用 grafana skill 时）："这个 Skill 里的 Dashboard 命名规则不对，应该是 svc-<name>"
AI：Phase 0 检查环境（已就绪）→ 创建 fix/grafana-dashboard-naming 分支
→ 直接修改 grafana/SKILL.md 中的命名规则 → validate.py 通过
→ git commit + push → 创建 MR → 返回链接
```

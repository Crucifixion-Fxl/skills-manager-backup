---
name: software-development-guide
description: 公司软件开发全貌指南 — 新人 / 跨部门同事 / AI agent 的入口。覆盖业务大类（增值/观测/B 端/基础能力）、仓库地图、技术栈、环境配置、研发流程、上线规范、分支规范、问题排查、咨询联系人。从 GitLab/本地仓现读现答，不依赖人不依赖看文档。Triggers on '我是新人怎么开始'、'公司业务有哪些'、'X 仓库干嘛的'、'X 模块谁负责'、'新需求怎么开发'、'上线流程'、'/onboarding'、'/software-development-guide'。
---

# Software Development Guide — 公司软件开发全貌指南

> 形态：A 静态密集型（业务/仓库/技术栈速查）+ B 路由探索（按问题跳子 skill 现读现答）。
> 范围：客户端 + 后端 + 前端 + 数据 + 配套（覆盖 11 namespace、100+ 活跃仓）。

## Description

公司软件开发全貌指南，覆盖业务大类（增值/观测/B 端/基础能力）、仓库地图、技术栈、环境配置、研发流程、上线规范、分支规范、问题排查、咨询联系人。

**目标用户**：

- 新入职 dev：第一周内通读 SKILL.md + 8 个 resources（特别推荐 `end-to-end-example.md` 照做一遍），跑通环境，并完成一个 feature 开发（开分支 → 写代码 + test → 提 MR → 通过 review → 合入；哪怕只是修一个 typo，也要走完整流程）
- 跨部门 dev（数据 / 算法 / QA / SRE）：找哪个仓做什么、谁负责
- 远程同事 / AI agent：不依赖飞书 IM 也能拿到公司全貌

## 9 大新人核心问题 → 路由表

按问题查表，每条问题给 (1) 一句话答案（在本文件末尾），(2) 详情 resource 文件，(3) 路由的子 skill。

| # | 新人问题 | 详情 resource | 路由 skill（仅本仓 engineering/skills 真实存在的）|
|---|----------|--------------|------------------------------|
| 1 | 公司业务有哪些主线？我会参与哪条？ | `resources/business-map.md` | — |
| 2 | 我们有哪些仓库？哪些是核心仓？ | `resources/repo-map.md` | — |
| 3 | 每条主线用什么技术栈？ | `resources/tech-stack.md` | — |
| 4 | 电脑环境怎么配？ | `resources/dev-flow.md#环境配置` | `android-dev-setup` / `iot-service-dev-setup` / `dev-infra` |
| 5 | 新需求怎么开发？ | `resources/dev-flow.md#新需求-sop` | `dev-workflow`（标准研发流程）|
| 6 | 问题怎么排查 / 上线规范 / 分支规范？ | `resources/dev-flow.md#上线-cd-规范` | `troubleshooting` / `gitlab-mr` / `code-review` / `code-submit` / `root-cause-analysis` |
| 7 | X 业务 / 系统 / 职能找谁咨询？ | `resources/ownership.md`（按业务域 / 系统 / 职能，非按仓 / 非按需求） | — |
| 8 | 飞书 wiki 在哪 / 上线规范 / 物模型 / IoT 灰度发布等专题文档？ | `resources/wiki-index.md` | — |
| 9 | 端到端怎么做（从需求到上线全流程示例）？ | `resources/end-to-end-example.md` | 详见文件内每步骤路由的本仓 skill |

> ⚠️ **路由 skill 说明**：本表只列 `engineering/skills` 仓**真实存在**的 skill。
> 其它 plugin / superpowers 提供的 skill（如 `tdd-workflow` / `using-git-worktrees` / `lattice-*` / `kotlin-patterns` 等）会出现在 resource 文件正文中作为**说明性引用**（"参考 lattice-* skill"），但 agent 不应当作 Skill 工具直接调用 — 找不到就当作普通文档说明。

## Rules

> 探索流程（路由型）— Agent 强制行为规范

新人提任意问题，agent 走这个流程：

```
1. 问题映射到 9 大问题之一（不在表里 → 让用户改述）
2. 立即 Read 对应 resource（resources/*.md），不要只用速答兜底
   - 速答只在 resource 都跑完仍然兜不住时给
   - 第一次回答必须有 resource 引用（"详见 resources/X.md#section"）
3. 如有路由子 skill 标注（仅本仓 engineering/skills 真实存在的），**主动用 Skill 工具进入子 skill**
   - 例如问到"环境配置" → 先读 dev-flow.md 共通 4 步 → 再 Skill 调 android-dev-setup
   - resource 正文里出现的非本仓 skill 名（如 `tdd-workflow` / `lattice-*` 等）当作"说明性引用"，不要尝试 Skill 调用
4. 仍需现读现答的（如"X 仓最近活跃度"），用 glab 现拉
5. owner 类问题：先答 resources/ownership.md 的推测值
   - **必须显式说"推测，待 SPM review"**，不要一带而过
   - 表里标"高/中"信度的，照答；标"待补"的，明说"未知，建议查 GitLab MR reviewer 反推"
   - 最后给三条路径（看 reviewer / 飞书 @SPM / CODEOWNERS）
```

**禁止**：背静态答案、引用过期文档、不读现仓就给技术栈结论、跳过 resource 直接给速答。

## 一句话速答（仅作兜底，第一次回答必须先读 resource）

> ⚠️ Agent 注意：不要把这一节当成首选答案。每次提问，先 Read 对应的 resource 文件，把详细内容 / section 链接给用户。速答只在 resource 已读完仍兜不住的极简场景用（如"公司业务有几条主线"这种 1 行能答完的）。

- **业务大类**：① 增值业务（订阅/支付/权益 + CMS 触点 + IoT 安防 + IoT 平台建设）② 观测产品（自然观测 Naturehood：徽章 / KBTV / 鸟类故事 / Postcard）③ B 端业务（独立团队：CRM/收入分享/客服）④ 基础能力（待补完整范围）
- **全栈期望**：产品 dev 全栈（Android + iOS + Flutter + Java + Go + 前端 + 数据 都要会）；归属 1-2 条主线深耕，主线内跨栈
- **独立团队**：QA / SRE / 数据 / 算法 — 不要求全栈业务开发，但必须有 CI/CD 能力 + 数据驱动思维（详见 tech-stack.md）
- **测试左移**：开发对质量负责，PR 必须含 unit/integration test；QA 兜底 E2E + 探索性测试
- **核心客户端仓**：`SWCLIEN/g0-android` `SWCLIEN/g0-ios` `platforms/lattice`（Flutter 框架）`SWCLIEN/g0-flutter-module`（旧）
- **核心后端仓**：`CLOUD/iot-service-unified`（Java/Spring Boot）`applications/naturehood`（Go）`CLOUD/marketing-service`（Java）
- **核心前端仓**：`CLOUD/marketing-cms`（Next.js + Payload）`frontend/web`(React + qiankun 微前端)
- **核心 SPM**：tiancailaxi（lattice scope 三仓 + iot-service 集成相关）
- **跨仓 scope**：lattice scope 含 iot-service / naturehood-server，可直接修复
- **必读 skill 路径**（按顺序）：
  - 可调用（本仓）：`dev-workflow` → `dev-infra` → `android-dev-setup` 或 `iot-service-dev-setup` → `testing-strategy`
  - 仅参考（外部）：`tdd-workflow`（superpowers 内）

## 不在范围内的问题

- 公司战略 / OKR / 商业机密 → 找 PM/SPM
- HR / 工资 / 福利 → 飞书 HR 群
- 跨公司客户业务 → 找产品

## 资源清单

```
SKILL.md                  ← 本文件，路由 + 速答
resources/
├── business-map.md       ← 4 大业务（增值/观测/B 端/基础能力）+ 每条主线的仓 + 技术栈
├── repo-map.md           ← 11 namespace × 100 活跃仓索引（按 scope 分组）
├── tech-stack.md         ← 客户端/后端/前端/数据栈速查 + 全栈期望
├── ownership.md          ← 业务 / 系统 / 职能 三层 owner 总结（业务域级别，非按仓 / 非按需求）
├── dev-flow.md           ← 环境/新需求 SOP/上线/分支/排查 + 周会 / weekly-report 节奏
├── wiki-index.md         ← 飞书 A4x Wiki 索引（上线规范 / 物模型 / IoT 灰度等专题文档）
├── end-to-end-example.md ← 端到端完整指引（11 步 case study：从 issue 录入到上线观察）
└── glossary.md           ← 业务术语 + 高频缩写 + 性能/数据指标（P95/P99/SLA/SLO/DAU 等）
scripts/
└── （预留：未来加自动化探索脚本，如 `glab-active-repos.sh`）
```

## Examples

### ❌ Bad Example：跳过 resource 直接背速答

> 用户："Library 入口谁负责？"
>
> Agent："Library 是 g0-android 仓的，主负责人是林智。"
>
> **问题**：
> 1. 没读 `resources/ownership.md` 现状
> 2. 用了"X 仓的主负责人是 Y"句式，公司没有"仓 owner"概念
> 3. 没识别"Library 入口"对应的业务域（视频回放 / 设置模块）
> 4. 没标"按 monthly plan 推测"

### ✅ Good Example：路由 resource + 按业务域定 owner

> 用户："Library 入口谁负责？"
>
> Agent：
>
> 我先 Read `resources/ownership.md` 查"Library 入口"对应业务域。
>
> 按 5 月 monthly plan，可能是这 2 个业务域之一（你确认下哪个）：
>
> 1. **设置模块动态化**（P2，李凯负责）— 如果说的是个人中心 Library 入口的设置/路由
> 2. **feeds 流优化**（P0，李凯负责）— 如果说的是视频回放 feeds
>
> 两个都是李凯。建议直接飞书 @李凯，附上 GitLab issue 链接。
>
> **数据来源**：weekly-reports/software/2026-05-monthly-plan.md（每月更新）

### ❌ Bad Example：装环境跳过共通 4 步

> 用户："我新人，怎么装 Android 环境？"
>
> Agent：直接调 `Skill android-dev-setup`
>
> **问题**：android-dev-setup 假设 GitLab + glab + Vault + 飞书已就绪；新人 Day 1 都没做，跑到一半会卡 token / clone fail。

### ✅ Good Example：先共通 4 步再栈级 setup

> 用户："我新人，怎么装 Android 环境？"
>
> Agent：
>
> 我读 `resources/dev-flow.md#环境配置`。
>
> 装 Android 之前，先确认共通 4 步：
> 1. GitLab 账号 + SSH key — 测 `ssh -T git@gitlab.addx.ai`
> 2. glab CLI — 测 `glab repo view CLOUD/iot-service-unified`
> 3. Vault token — 测 `vault kv list secret/`
> 4. 飞书账号 + 部门群
>
> 这 4 步都做完了吗？做完我调 `android-dev-setup` skill 给你装 Android 那套。

### ❌ Bad Example：仓 → owner 句式

> 用户："iot-service 仓谁负责？"
>
> Agent："iot-service 主负责人是林智。"
>
> **问题**：iot-service 服务多个业务域（library/timeline/VIP/bird filter/setting-override...），每个域不同人负责。

### ✅ Good Example：反问业务域再答

> 用户："iot-service 仓谁负责？"
>
> Agent：
>
> iot-service 服务多个业务域，没有"仓 owner"。你问的是哪个具体业务？我读 `ownership.md` 速查：
>
> - **Library / Timeline / NewEvent**：lattice 集成相关，飞书 @tiancailaxi（SPM）
> - **VIP / 权益**：汪强（P0 权益中心）
> - **Bird filter / 设备绑定**：金威（P1 绑定重构）
> - **OTA / 物模型**：武严严、马奥杰（P0 IoT 平台重构）
> - **iOT 平台事件服务**：马奥杰（P0）
>
> 告诉我具体业务，我精确给联系人。

---

## 维护者注

本 skill 由 lattice scope 试点（`lattice/.aide/skills/onboarding/`，2026-05 起）→ 沉淀至 `engineering/skills`。

**反馈方式**：
- 内容错误（如 ownership / 业务划分）：直接 PR 改本 skill
- 添加缺失主线 / 仓 / 术语：PR 编辑对应 resources 文件
- 业务地图需更新：先读飞书原文档（链接在 `resources/business-map.md` 末尾）再改

**数据时效性**：resources 内容来自 2026-05 调研快照（GitLab API + 本地仓 CLAUDE.md + 飞书业务规划文档）。建议每月手动 refresh 一次：
- `repo-map.md` / `tech-stack.md`：用 `glab` 重拉 + 本地仓配置 re-scan
- `ownership.md`：跑文件末尾的 refresh 命令（60d MR author + reviewer 频率）
- `business-map.md`：读飞书业务规划文档对照
- `glossary.md`：新出现的缩写持续累加

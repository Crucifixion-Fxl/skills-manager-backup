# 开发流程：环境 / 新需求 SOP / 上线 / 分支 / 排查

> 本文件汇总跨仓共用的标准流程。仓特定的流程在各仓 CLAUDE.md / `docs/DEVELOPMENT_WORKFLOW.md`。
> 所有 → 子 skill 的引用，都是 Claude Code 里 `Skill` 工具直接调用的入口。

---

## 全栈基础原则

公司全员（产品 dev + 独立团队）必须掌握的 4 项共通能力：

| 能力 | 可调用 skill（本仓 engineering/skills）| 仅参考 skill（外部 plugin / superpowers，非本仓）|
|------|------------------------------------|-------------------------------------------|
| **CI/CD** | `gitlab-ci` / `argocd` / `argocd-deploy` / `cicd-developer` / `k8s-ops` | — |
| **测试左移** | `testing-strategy` | `tdd-workflow` / `e2e-testing` |
| **数据驱动思维** | `data-driven-investigation` / `tracking-lifecycle` / `growthbook` / `superset` | — |
| **AI 工程** | — | `claude-api` / `agentic-engineering` / `ai-first-engineering` |

> "测试左移"是硬要求：开发不能写完功能甩给 QA，QA 只兜底 E2E + 探索性。
> ⚠️ **可调用** = 本仓 engineering/skills 真实存在，agent 应当主动用 Skill 工具进入。
> **仅参考** = 外部 plugin / superpowers / lattice 内独立 skill，agent 应当作"说明性引用"，不要尝试 Skill 工具调用。

---

## 环境配置

按你参与的主线选对应的 setup 路径：

### 全员先做（共通 4 步，不可跳过）

⚠️ **Agent 注意**：用户问"装环境"时，先确认共通 4 步是否完成。**不要直接调 `android-dev-setup` 这类栈级 skill**，因为它们假设共通基础已就绪。

1. **GitLab 账号 + SSH key**：注册 https://gitlab.addx.ai/，加 SSH 公钥；测试 `ssh -T git@gitlab.addx.ai`
2. **glab CLI**：`brew install glab` + `glab auth login --hostname gitlab.addx.ai`；测试 `glab repo view CLOUD/iot-service-unified`
3. **Vault 访问**（密钥管理）：找 SRE/DevOps 申请 Casdoor 飞书 SSO + Vault token；测试 `vault kv list secret/`
4. **飞书账号 + 部门群**：找 HR / 直属 leader 拉群

> Agent 应主动问："你这 4 步做完了吗？我陪你逐步过。" 用户确认完成后，**再**进入下面的栈级 setup 路径。

### 客户端栈 setup

| 栈 | 路径 / 子 skill |
|----|---------------|
| Android（g0-android） | → 子 skill `android-dev-setup`（含 SDK / NDK / Gradle / Flutter module 联调） |
| iOS（g0-ios） | 装 Xcode 16+、CocoaPods、`pod install` 走 `_Pods.xcodeproj`；smartdevicecoresdk-ios 是 framework 依赖 |
| Flutter（lattice） | 装 Flutter 3.5.x SDK；进 `apps/shell_app` 跑 `flutter run -d chrome` 验环境；melos bootstrap |

### 后端栈 setup

| 栈 | 路径 / 子 skill |
|----|---------------|
| iot-service（Java） | → 子 skill `iot-service-dev-setup`（含 Maven / IDEA 配置 / 本地 Redis + MySQL） |
| naturehood server（Go） | 装 Go 1.21+；进 `applications/naturehood/server/`，看 README 起 docker-compose |
| marketing-cms（Next.js） | 装 Node 20+ / pnpm；连 MongoDB；走 Payload CMS 文档 |

### 客户端 + 后端联调

→ 子 skill `local-fullstack-debug`（在本地把 g0-android 模拟器 / g0-ios 真机 接到本地起的 iot-service-unified）

### 多 worktree 并行开发

→ 子 skill `dev-infra` / `multi-worktree-dev`（同仓多分支并行的标准做法）

### 工具链

- **AI agent harness**：Claude Code（你正在用），配置在 `~/.claude/`
- **代码审查**：GitLab MR review
- **任务跟踪**：飞书项目 / GitLab Issue（按团队选）

---

## 新需求 SOP（标准研发流程）

→ **子 skill `dev-workflow`**（10 阶段编排器）— 强烈推荐先跑一次走完整流程，再独立做需求

简化流程（lattice scope）：

```
1. 需求分析       → 写 docs/requirements/{feature}.md / 飞书 PRD link
2. 技术方案设计   → 写 implementation_plan.md，找 SPM/owner review
3. 测试用例设计   → 写 docs/test-cases/{feature}.md
4. Code Review (设计) → 找仓主负责人确认方案
5. 单元测试       → 先写测试（TDD，用 `tdd-workflow` skill）
6. 代码实现       → 测试驱动写实现
7. 单元测试通过   → 覆盖率 ≥ 80%
8. 编译调试       → BUILD SUCCESSFUL，无 warning
9. 模拟器/集成测试 → adb logcat / 真机 / E2E
10. Code Review (代码) → 提 MR，按本仓 review checklist 走完
11. 总结报告      → 写 docs/reports/{feature}-{date}.md（lattice / g0-android 强制）
```

**仓特定差异**：

- **g0-android**：12 阶段（在第 4 步前加"测试用例 review"，第 9 步加"模拟器测试 + Manual Verification Checklist"）
- **lattice**：必须 `dep_checker` + `dart format` + `dart analyze` + `quality_gate` 全过；每个 feature 包必须有 `USER_STORY.md`
- **iot-service**：DDD 分层（domain-interface / domain-service / site-controller）；密钥变更必须更新 `docs/product/secret-rotation-plan.md`
- **naturehood**：所有 user story 必须有 E2E 覆盖；handler 层只解析参数，业务逻辑放 logic 层

---

## 分支规范

| 分支类型 | 命名 | 用途 |
|----------|------|------|
| 主分支 | `main` / `master` | 受保护，只接 MR 合入 |
| Release | `release/*` / `release/KB_2.15.0` | 发版分支 |
| Feature | `feat/<scope>-<name>` 或 `feat/issue-<n>-<slug>` | 功能开发 |
| Bugfix | `bugfix/<scope>-<name>` 或 `fix/<slug>` | bug 修复 |
| Hotfix | `hotfix/<name>` | 线上紧急 |
| Stage | `<base>_for_stage` | 预发布 |

**关键规则**：
- **永不直接 push main**：所有改动走 MR
- **永不 force push 共享分支**（main / release / 别人的 feature）
- **不跳过 hooks**：`git commit --no-verify` 仅在 hook 本身坏掉时使用
- **新 feature 用 worktree 隔离**（lattice 强制 / g0-android 推荐）

**Commit 规范**（统一）：

```
<type>(scope): <subject> [tag]

可选正文（说 why，不说 what）

可选 footer（关联 issue / breaking change）
```

- type：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `ci`
- tag：`[human]`（人写的）/ `[AI-Generated]`（AI 生成的，工具不限）
- 跨端改动：commit 末尾标 `需同步到 iOS/Android`

→ 子 skill：`gitlab-mr`（自动起 MR）/ `code-submit`（提交全流程）/ `using-git-worktrees`

---

## MR / Code Review 规范

→ 子 skill：`code-review` / `requesting-code-review` / `receiving-code-review`

**起 MR 前必须**：
- 自动化检查（CI/CD）全过
- 解决合并冲突
- 分支基于最新 base 分支

**MR 模板**（推荐）：
```
## 现象 / 需求
- ...

## 根因分析（bug fix）/ 设计要点（feature）
- ...

## 影响范围
- ...

## 验收 / 测试
- [ ] 单元测试通过（覆盖率 X%）
- [ ] 编译无 warning
- [ ] 模拟器 / 真机 / E2E 验证 OK
- [ ] 文档同步更新

## 关联
- Issue: #...
- 双端：需同步到 iOS / Android（如适用）
```

**Review 严重级别**：
- **CRITICAL**（安全 / 数据丢失风险） → block merge
- **HIGH**（bug / 重大质量问题） → 应该修
- **MEDIUM**（可维护性） → 考虑修
- **LOW**（风格） → 可选

---

## 上线 / CD 规范

> 完整规范：https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1（A4x Wiki / 上线规范）
> 上线日志模版：A4x Wiki → 上线规范 → 上线日志模版

### 常规上线 SOP（飞书 wiki 12 步 — 必读）

1. 产品需求 / 技术优化 / bug 产生**同时**通知测试，了解背景，明确测试范围/策略；上线前 review 测试方案
2. 任何跟上线文档步骤有不同的改动，必须有 checklist，重新 review，更新到上线文档
3. **提前一天**确定 QA 资源，预估测试时长。上线单当日 pre 和线上必须验证完毕（紧急 bug 当天处理）
4. 创建 PR → 通过 SonarQube 检查 + review → merge 到 master → 打 tag（命名：`tag_<飞书姓名>_<YYYYMMDD>_<功能简述>`，如 `tag_gshen_20240611_firebase`）
5. 上线功能负责人按"上线日志模版"创建本次上线日志，填上线日志链接。测试范围明确列出每个功能点（注册 / 登录 / 直播 / 视频列表 / 一键呼叫等）
6. 指定**观察人**（本周 oncall；如自己 oncall，指定下一个 oncall 人）
7. 上线清单详细列出测试范围。code review 时观察人必须检查是否有额外改动
8. 后端上线群发部署通知，给出上线单链接，上线每个环境时 @ QA 和观察人。例：`http://cicd.addx.live/#/task/351/detail 开始部署中国 prod @叶梦瑶 @沈xx`
9. **pre 验证完毕后部署**：先部署 cn；**eu / us 禁止晚 6 点以后高峰期部署**；**禁止周五**及办公室无人值守情况下上线。特殊情况需"牧云 + 江领"共同审批
10. 每个线上环境观察监控图表 + 项目日志 **20 分钟**，记录观察日志，确认无错误和不良影响
11. 完成上线日志中所有项。出问题且未按清单流程走，根据故障级别采取处理措施。**观察人和功能负责人责任 3:7**
12. 发现严重问题，**及时通报并回滚**

### 客户端 App 提审值班

详见 `ownership.md` "APP 版本值班 / 提审流程" 段。

| App | iOS / Android 提审 |
|-----|------|
| KB / VH | 陈志超 |
| VN | iOS 谷磊 / Android 李凯 |
| F | 林智 |
| Golf | 朱孟强 |

iOS 自动上传应用商店后**手动提审**；Android 打 apk/aab 后**手动提审**。

### 后端 / K8s GitOps

- **ArgoCD GitOps 流**：MR 合 → CI 镜像 push → ArgoCD sync 到 stage → 验证 → release MR → 同步 prod
- **stage→release 双 MR 模式**（iot-service 等老仓）：先 stage MR 验证，再 release MR 同步 prod
- **灰度白名单**：用 `cicd-gray-whitelist` skill 给指定 service 在指定地区灰度
- **IoT 服务灰度发布完整方案**：A4x Wiki → IoT 服务灰度发布完整技术方案

→ 子 skill：`argocd` / `argocd-deploy` / `gitlab-ci` / `jenkins` / `cicd-gray-whitelist`

### 关键约束

- **禁止晚 6 点后**（eu / us 高峰期）部署
- **禁止周五**部署
- **禁止无人值守**部署
- 紧急例外需"牧云 + 江领"双人审批

---

## 问题排查

### 客户端

| 现象 | 路径 |
|------|------|
| Android 构建失败 | → `kotlin-build` skill |
| iOS 构建失败 | 看 Xcode log；Pod 冲突重 `pod deintegrate && pod install` |
| Flutter 编译失败 | → `flutter-build` skill |
| Lattice 运行时空数据 / 路由错误 | → `lattice-debugging` skill |
| Lattice quality gate 失败 | → `lattice-quality-gate` skill |
| 接口调用失败 | adb logcat 抓请求；troubleshooting 平台查后端日志 |

### 后端

| 现象 | 路径 |
|------|------|
| Spring Boot 启动失败 | 看 stack trace；常见 bean 重名 / cglib proxy / DataSource 连不上 |
| 接口偶发异常 | troubleshooting 平台 ES 日志 + traceId 回溯 |
| 性能问题 | Prometheus + Grafana 看指标；JVM dump 分析 |
| 告警 firing | → `sla-alert-analysis` skill |

### 跨栈方法论

→ 子 skill：`troubleshooting`（troubleshooting 平台 API 入口）/ `systematic-debugging`（方法论）/ `root-cause-analysis`（必跑步骤）/ `silent-failure-hunter`（隐藏错误识别）

---

## 文档同步

凡修改代码逻辑 → 必须同步文档：

- 改配置字段 → 更新 integration doc 配置表
- 改业务流程 → 更新流程图（Mermaid）
- 新功能 → 补测试用例 + USER_STORY.md（lattice / g0-android 强制）
- 修 bug → 如 bug 涉及文档描述，更新 doc

→ 子 skill：`update-docs` / `update-codemaps`

---

## 常用命令速查

```bash
# 看本仓最近 MR
glab mr list -P 20

# 拉某个 MR diff
glab mr diff <number>

# 起 MR
glab mr create --title "feat(scope): xxx" --description-from-file .mr-template.md

# 看仓的活跃 commit
git log --oneline -20

# 多仓 worktree（lattice 强制）
git worktree add ../lattice-wt-myfeature -b feat/myfeature

# Android 调试
adb logcat -c && adb logcat | grep -iE "<your-app-id>|crash"
adb -s emulator-5554 install -r app/build/outputs/apk/.../release.apk

# Flutter 跑测试
cd packages/<pkg> && dart test          # 纯 Dart 包
cd packages/<pkg> && flutter test       # Flutter 包
```

---

## 周会 / Weekly Report 节奏

公司有固定的周报 / 月报 / 周会节奏，新人入职第一周就要进入：

### Weekly Report

- **公司主仓**：`weekly-reports/software`（GitLab）— 每月一份 monthly plan + 每周一份周报
- **路径**：https://gitlab.addx.ai/weekly-reports/software
- **当前月**：https://gitlab.addx.ai/weekly-reports/software/-/blob/main/2026-05-monthly-plan.md
- **本周周报**：飞书 wiki 当周链接（如 https://a4x-paas.feishu.cn/wiki/II6ywUqggiWU5DkGilzcpUapn3f — 2026.05 第 2 周）
- **周报内容**：上周完成 / 本周计划 / 风险点 / 待协调

### 周会

- 形式：飞书会议或线下
- 节奏：每周固定时间（按团队定，新人入职第一天问直属 leader）
- 新人参与：第一周开始旁听，第二周开始同步个人计划 / 进展
- 周会前：填好本周 weekly-report（上周完成 / 本周计划），周会上简短同步关键变化

### 工时填报

- 周五填报上周工时（部分团队）
- → 子 skill `worktime-filing`（周五工时填报自动生成）

### 月度 Monthly Plan

- 月初由 SPM / 架构师 整合到 `weekly-reports/software/{YYYY-MM}-monthly-plan.md`
- 内容：P0+ / P0 / P1 / P2 / P3 + 各责任人任务 + Size + 预计上线
- 来源：飞书"需求和资源"文档（https://a4x-paas.feishu.cn/wiki/F25ywb6ajiQEoDkelkUc2MrvnSf）

### Agent 用法

- 用户问"本月有什么需求 / 谁在做什么" → 让用户看 monthly plan 链接
- 用户问"上周做了什么" → 让用户看 weekly-reports 仓最新周报
- 用户问"我要交周报怎么写" → 套 monthly plan 同人最新模板

---

## 高频新人 FAQ

**Q：第一周做什么？**
A：跑通环境 + 走完一次 dev-workflow（哪怕是修个 typo），熟悉 MR / review / CI 流程。

**Q：找不到对接人怎么办？**
A：先看 `resources/ownership.md` 推测表，再看 GitLab 仓最近 5 个 MR 的 reviewer，最后飞书 @SPM。

**Q：要不要自己折腾环境？**
A：先按对应 dev-setup skill 走，**不要自创流程**。setup skill 是积累过的最优路径。

**Q：写代码必须用 AI 工具吗？**
A：本公司鼓励 AI-first 工程方法（→ skill `ai-first-engineering`），Claude Code 是默认工具，但不强制每行代码都 AI 生成。commit message 必须标 `[AI-Generated]` / `[human]`。

**Q：lattice scope 和 gen3 scope 怎么区分？**
A：看 lattice/CLAUDE.md。lattice scope 包含客户端三仓 + 客户端集成中暴露的后端问题（iot-service / naturehood-server）；与客户端集成无关的纯后端问题转交 gen3 SPM。

---
name: story-craftsman
description: 专业用户故事(User Story)与文档体系构建。协助编写高质量 US 文档，通过引导式访谈挖掘需求背景与验收标准，并将文档精准放置到规范的 docs 目录结构中。在用户请求编写 US、描述功能需求、或需要初始化项目文档结构时触发。
---

# Story Craftsman

此技能践行「需求即代码」(Everything is Code) 的工程理念，帮助你在提交 MR 时同步产出高质量的用户故事及其配套文档。

详细示例模板参见 [examples/TEMPLATE-UserStory.html](examples/TEMPLATE-UserStory.html)。

---

## Rules

1. **严禁凭空脑补**：信息不足时，必须通过引导式访谈获取背景，而不是自行填充
2. **架构感知放置**：分析文件路径或 `git status` 确定所属模块，将文档放入对应的 `docs/product/user-stories/` 目录
3. **验收标准可测**：每条 AC 必须是可检验的可勾选条件（Given/When/Then 或明确的 checkbox）
4. **一次性提问**：访谈时将所有缺失维度合并为一次提问，避免多轮碎问造成用户疲劳
5. **路径优先推断**：先尝试从上下文推断目标子模块，推断不确定时才询问用户
6. **本 skill 是 US 文档结构的 SSOT**：覆盖访谈协议、Epic-first 结构纪律、优先级表达规则、推荐骨架与反例。`architect` skill 在 Step 1 委派到这里，本节是规范的唯一来源——避免规范分裂。详见下方 [Step 3](#step-3生成用户故事文件) 完整 Epic-first 纪律
7. **Requirements review-only 模式**：被 `requirements-analysis-agent` 调用时，只返回 Why/Who/What、scope/non-goals、AC/NFR 的 findings 与合并问题；不得写文件、不得自行向用户逐轮访谈、不得宣称 PO 已接受、不得启动设计或编码。文档生成模式只在上游提供 accepted REQUIREMENTS Artifact 后使用。
8. **Direct raw intake guard**：direct raw requirement（新想法、功能描述、PRD 草稿或行为变更）必须先转交 `requirements-analysis-agent`，不得直接执行访谈或 Step 3 写文件。An explicit document-write request is not PO acceptance；只有 exact Artifact ID/hash 已被 PO ACCEPT，或本 Skill 明确处于 Requirements `review_only=true` pass，才可继续对应模式。

## Review-only mode

当调用方声明 `review_only=true` 或来源是 `requirements-analysis-agent`：

- 输入必须带 canonical source ref/digest；缺失时返回 `BLOCKED` finding，不从 cwd 猜来源。
- 一次性返回 `verdict: PASS|CONDITIONAL|BLOCKED`、稳定 finding ID、证据引用和一个合并问题集。
- 不执行下方 Step 3 文件写入，不更新索引，不创建 MR；输出交回 Requirements Agent 统一形成 Artifact 或 `ATTEMPT_NEEDS_INPUT`。
- 本 Skill 只拥有 US/PRD 结构质量；Requirements envelope、Attempt、lineage 与 PO Gate 由 `requirements-analysis-agent` 拥有。

---

## 执行流程

### 功能 A：编写用户故事 (Generate User Story)

### 触发条件

用户请求"帮我写 US"、"描述这个功能"、"生成用户故事"等。

### Step 1：判断信息充分性

先执行入口门禁：若输入是 direct raw requirement 且没有 accepted REQUIREMENTS Artifact，停止本流程并调用 `requirements-analysis-agent`。只有 accepted Artifact 的派生文档生成，或 Requirements review-only pass，才继续下面的信息充分性检查。

收到请求后，检查是否具备以下三要素：

| 要素 | 含义 | 是否充分的判断标准 |
|------|------|--------------------|
| **背景 (Why)** | 现状痛点、影响范围 | 能说出"谁在什么场景下受影响" |
| **目标 (What)** | 可验证的成功指标 | 能说出"完成后能量化什么" |
| **验收标准 (AC)** | 关键边界场景 | 至少列出 2 个可勾选条件 |

**若信息不足，执行 Step 2（访谈）；若信息充分，直接执行 Step 3（生成）。**

### Step 2：引导式访谈协议

以「侦探」和「诗人」的双重角色，一次性提出所有缺失维度的问题（避免多轮碎问）：

```
为了生成一份专业的用户故事，我需要确认以下信息：

1. **背景/痛点**：[具体缺失的背景问题]
2. **目标**：[具体缺失的目标问题]
3. **验收标准**：[具体缺失的 AC 问题]

您可以简要描述，我会负责将其转化为标准的专业文档格式。
```

访谈前先做 **source_issue 硬门禁**：无 GitLab issue 链接时先停下——请用户提供链接，或先走 `gitlab-issue-sop` 查重 + 创建 issue；拿到链接前不开始写 US。生成的 US 文档 header 必须带必填字段 `source_issue`（issue URL）。（#61 G-1，详见 docs/architecture/skill-artifact-delivery-implementation.html §10）

### Step 3：生成用户故事文件

严格遵循 [TEMPLATE-UserStory.html](examples/TEMPLATE-UserStory.html) 的结构生成 HTML 文件。

#### 文档基本规则

- 按**服务或模块**拆分文件（如 `gateway.html`、`sandbox.html`），一个文件对应一个服务域
- ID 格式：`US-{模块缩写}-{序号}`（如 `US-FI-01`）
- 每条 US：背景(Why) + 用户故事(Who/What/Goal) + AC（Given/When/Then 格式）
- NFR 单独列节，写可量化指标（如"响应 p95 < 2s"），不写实现方案
- 进展状态标记：`✅ 已实现` / `⚙️ 进行中` / `待开始`
- 新增 US 后同步更新 `user-stories/index.html` 的索引表

#### Epic-first 结构纪律

**跨多个 stakeholder 视角**（作者 / 阅读者 / 运营 / AI 审核 等）的 feature，必须按 Epic 顶级组织：

- **每个文件内按 Epic 组织，Epic 使用顶级 `<h2>` section，US 是 Epic 下的 `<h3>` 子节**
- 每个 Epic 对应一个明确的 **stakeholder 视角**，Epic 标题就显式标注视角
- Epic 的引言段（紧跟 `<h2>Epic N：xxx</h2>` 之后的一句话）说明"这个 Epic 服务的是谁、解决什么问题"
- 文件开头放一张 **Epic 概览表**（Epic / 视角 / 包含 US / 优先级），让 reader 一眼看清整体结构

**单一视角的简单 feature** 可继续用 [TEMPLATE-UserStory.html](examples/TEMPLATE-UserStory.html) 的扁平 US 列表（不强制造 Epic），但跨视角时必须切 Epic。

#### 优先级表达规则（关键）

- **不要把 P0/P1/P2/MVP/增强 等优先级作为顶级 `<h2>` section，把 Epic 挤到二级标题。** 优先级是 Epic 的**属性**，不是组织维度
- 优先级的正确表达方式：
  1. Epic 概览表里加一列「优先级」标注每个 Epic 的优先级
  2. 当前不做但已识别的 Epic 放在独立的 `<h2>Future Epic：xxx</h2>` section，说明触发条件
  3. 当前不做的零散 US 放在 `<h2>本期明确不做</h2>` section
- 这样做的理由：Epic 是按"谁来用、解决什么问题"切分的稳定结构；优先级是会随业务节奏变化的标签。让稳定结构主导文档骨架，让易变标签做属性

#### 推荐结构骨架

```html
<h1>用户故事：&lt;feature 名称&gt;</h1>
<!-- header 必填字段：source_issue = 需求 GitLab issue URL；缺失不得生成文档 -->
<section id="why"><h2>背景（Why）</h2></section>
<section id="epic-overview"><h2>Epic 概览</h2></section>
<section id="epic-1"><h2>Epic 1：&lt;功能簇&gt;（&lt;stakeholder&gt; 视角）</h2>
  <section id="us-xx-01"><h3>US-XX-01：&lt;US 标题&gt;</h3></section>
  <section id="us-xx-08"><h3>US-XX-08：&lt;US 标题&gt;</h3></section>
</section>
<section id="future-epic"><h2>Future Epic：&lt;尚未排期但已识别的 Epic&gt;</h2></section>
<section id="out-of-scope"><h2>本期明确不做</h2></section>
<section id="nfr"><h2>非功能需求（NFR）</h2></section>
<section id="observability-scope"><h2>Observability Scope</h2></section>
<section id="implementation-status"><h2>实现状态追踪</h2></section>
<section id="l3-coverage"><h2>L3 端到端覆盖</h2></section>
```

#### Epic-first 反例对比

**❌ Bad — 用优先级（P0/P1）做顶级 section，把 Epic 挤到二级**

```html
<h2>P0 — MVP</h2>
<h3>Epic 1：评论发表</h3>
<h4>US-CM-01：评论发表后作者立即看到</h4>
<h4>US-CM-08：AI 审核故障时体验不受影响</h4>
<h3>Epic 2：评论可见性</h3>
<h4>US-CM-02：其他用户只看到通过审核的评论</h4>

<h2>P1 — 增强</h2>
<h3>Epic 3：评论区氛围保护</h3>
<h4>US-CM-03：...</h4>
```

问题：

- Epic 是按"谁来用、解决什么问题"切分的**稳定业务结构**，优先级是会随节奏变化的**易变标签**——让易变标签遮蔽稳定结构，文档骨架很快就要随排期重写
- reader 想找"作者视角的所有 US"必须先猜它在 P0 还是 P1，跨段拼凑
- Future Epic 没地方放（P2？P3？），导致已识别但暂不做的方向被悄悄丢掉

**✅ Good — Epic 顶级、优先级作为属性**

```html
<h2>背景（Why）</h2>
<h2>Epic 概览</h2>
<h2>Epic 1：评论发表（作者视角）</h2>
<h3>US-CM-01：评论发表后作者立即看到</h3>
<h3>US-CM-08：AI 审核故障时体验不受影响</h3>
<h2>Epic 2：评论可见性（阅读者视角）</h2>
<h3>US-CM-02：其他用户只看到通过审核的评论</h3>
<h2>Epic 3：评论区氛围保护（AI 审核）</h2>
<h3>US-CM-03：...</h3>
<h2>Epic 4：灰度与可观测（运营视角）</h2>
<h3>US-CM-06：...</h3>
<h2>Future Epic：移除 Shadow Ban，升级为透明化审核</h2>
<h2>本期明确不做</h2>
<h2>NFR / Observability / 实现状态 / L3 覆盖</h2>
```

收益：

- Epic 骨架按 stakeholder 视角组织，业务结构稳定；优先级通过概览表的「优先级」列表达
- Future Epic 有明确位置，避免"识别到但忘记跟踪"
- reader 按视角直接定位（"作者视角看 Epic 1"），不需要在优先级桶里翻找

**参考实例**：Naturehood `docs/product/user-stories/comment-moderation.html`

#### 向后兼容

已存在的旧 US 文档**不强制翻新**，只对新建或大幅重写的 US 文档强制 Epic-first 结构。

#### 文件命名 & 放置

- **文件命名**：`{feature-name}.html`（小写 kebab-case）
- **放置路径**：`{project_root}/docs/product/user-stories/{feature-name}.html`

若目标目录不存在，先执行功能 B 或提示用户。

---

### 功能 B：初始化标准 Docs 结构 (Initialize Docs Structure)

### 触发条件

用户明确请求"创建文档结构"、"初始化 docs"等。

### 标准目录结构

```
docs/
├── product/
│   └── user-stories/       # 用户故事 HTML
├── architecture/           # 系统设计与架构决策 HTML
├── testing/                # 测试策略与场景 HTML
├── deployment/             # 部署与本地环境 HTML
└── user-guide/             # 用户手册 HTML
```

### 执行步骤

1. 确认目标根目录（询问用户或从上下文推断）
2. 在目标根目录下创建上述 5 个子目录
3. 在每个子目录下创建 `.gitkeep` 文件，确保 Git 能追踪空目录
4. 在 `docs/` 根目录生成 `AGENTS.md`，说明各目录用途

### 生成的 `docs/AGENTS.md` 内容模板

```markdown
# docs/

本目录为项目文档根目录，按研发生命周期分层组织。

## 目录结构

- `product/user-stories/`：用户故事 HTML，命名规范：`{feature-name}.html`
- `architecture/`：系统架构设计、技术选型、ADR（架构决策记录）HTML
- `testing/`：测试策略与测试场景 HTML
- `deployment/`：部署与本地开发环境 HTML
- `user-guide/`：面向最终用户的操作手册与使用说明 HTML

## 维护约定

- 用户故事按功能模块创建独立 `.html` 文件
- 架构/目录级变更后同步更新本文件
```

---

### 子模块识别逻辑

分析代码变动或用户描述，按以下顺序确定目标子模块：

1. **从文件路径推断**：变动文件的顶层目录（如 `device-cloud-server/`、`frontend/`）
2. **从 git status 推断**：`git status` 输出中的路径前缀
3. **无法确定时询问**：若变动跨多个模块或路径不明确，**必须询问用户**

---

## 示例

### ❌ Bad

```html
<h1>用户故事：报警过滤功能</h1>

<p><strong>作为</strong>系统管理员，<strong>我希望</strong>能过滤报警，<strong>以便于</strong>减少干扰。</p>

<h2>验收标准</h2>
<ul>
  <li>系统支持过滤功能</li>
  <li>过滤后不显示被过滤的报警</li>
</ul>
```

问题：背景缺失、AC 过于模糊、无法验证"什么条件触发过滤"。

### ✅ Good

```html
<h1>用户故事：基于规则的报警静默过滤</h1>

<section id="why">
  <h2>背景</h2>
  <p>监控系统每天产生 500+ 条心跳探活报警，导致运维人员出现告警疲劳，真正的故障报警被淹没，平均响应时间从 2 分钟延长到 15 分钟。</p>
</section>

<section id="us-alert-silence">
  <h2>US-ALERT-01：基于规则的报警静默过滤</h2>
  <p><strong>作为</strong>运维工程师，<strong>我希望</strong>能配置报警静默规则（按设备类型+报警类型组合），<strong>以便于</strong>屏蔽已知的非故障性报警，将真实故障报警的响应时间恢复到 2 分钟内。</p>

  <h3>验收标准</h3>
  <ul>
    <li>Given 已配置"设备类型=心跳探针 AND 报警类型=连接超时"静默规则，When 该类报警触发，Then Grafana 面板不显示该报警，审计日志记录"已静默"</li>
    <li>Given 静默规则配置界面，When 管理员保存规则，Then 规则在 30 秒内生效，无需重启服务</li>
    <li>Given 某报警同时匹配静默规则和升级规则，When 报警触发，Then 静默规则优先，但在审计日志中标注冲突</li>
  </ul>
</section>
```

---

## 豁免

| 场景 | 条件 |
|------|------|
| 技术改造/重构 MR | 无明显用户可见功能变化时，可简化为一段技术说明 |
| 纯 Bug 修复 | 直接引用对应 Issue，无需完整 US 格式 |

豁免方式：`/override skill=story-craftsman reason="纯技术重构，无用户可见功能变更"`

---

## References

- [用户故事模板](examples/TEMPLATE-UserStory.html)
- [INVEST 原则](https://en.wikipedia.org/wiki/INVEST_(mnemonic)) — Independent, Negotiable, Valuable, Estimable, Small, Testable
- [Given-When-Then 验收标准写法](https://martinfowler.com/bliki/GivenWhenThen.html)

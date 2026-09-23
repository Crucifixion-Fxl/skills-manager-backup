---
name: architect
description: Use when designing a new feature or system, making architecture decisions, writing technical design docs, or structuring project documentation. Triggers on "design", "architecture", "technical design", "ADR", "system decomposition", "how should I structure this", "设计方案", "架构设计", "技术方案", "文档体系", "写 US", "写文档". Also use when reviewing existing architecture docs or asking "where should I put this doc?"
---

# Architect

架构师 Skill — 通过苏格拉底式追问驱动设计决策，产出完整的技术文档体系。

架构师的产物就是文档体系。写统一 HTML 文档站的过程就是在做系统分解，写 `domain-model.html` 的过程就是在统一领域语言，写 Markdown ADR 的过程就是在固化决策推理。文档不是设计的副产品，文档就是设计本身。

## 核心原则

1. **文档先于代码**：每个步骤的文档产物是下一步的输入，不跳步、不倒序
2. **苏格拉底式追问驱动设计**：不直接给方案，先从 US 中识别架构关注点，通过追问收敛设计决策
3. **User Story 纯用户视角**：US 只写用户体验和验收标准，绝不混入技术细节
4. **架构正文 HTML-first，ADR Markdown-only**：面向人的架构总览、专题页、流程与数据图默认是自包含 HTML，复用同一套 shell、导航、大纲、图语言和 SSOT 链接；新 ADR 固定使用机器友好的 Markdown（YAML front matter + 稳定章节），不为简单决策记录引入 HTML chrome。详细边界见 [`references/html-architecture-doc-writing.md`](references/html-architecture-doc-writing.md) 与 [`references/adr-format.md`](references/adr-format.md)
5. **术语统一**：`domain-model.html` 是 DDD Ubiquitous Language 的落地，业务术语 <-> 技术术语的唯一映射 SSOT
6. **Superpowers 产物必须归并**：会话中生成的方案在开始编码前必须写入 `docs/`
7. **方案先行**：文档可以描述目标态（设计超前于代码是正常节奏）；但代码已实现的功能必须有对应文档
8. **代码一级目录留 CLAUDE.md + AGENTS.md 软链**：仓里只两层 CLAUDE.md——**仓根** + **代码一级子目录**（`server/` / `admin/` / `moments/` / `app/` 等，1 个目录 = 1 个 System）。每次架构变更（拆 System、本目录 skill 选型变更、新增 hard boundary、关键 ADR 推翻）同步更新，**不允许漂移**。详细规约 + 模板 + 反模式（不在 docs/ 树写 / 不在 Component 级写 / 极薄 ≤20 行 / 只两节）见 [§ 代码一级目录 CLAUDE.md 模板](#代码一级目录-claudemd-模板)。
9. **需求驱动设计必须有 accepted Requirements**：对新功能、需求变更或 bug-derived requirement，进入架构 Step 2 前必须读取由 `requirements-analysis-agent` 生成、PO 对精确 `artifact_id + artifact_hash` ACCEPT 的 REQUIREMENTS Artifact。缺失、hash 不匹配、`ATTEMPT_NEEDS_INPUT` 或 Gate pending/rejected 时停止；architect 不得自行补写需求并绕过门禁。豁免只限**只读现状解释或通用技术教育**，且不得形成项目特定目标态、ADR、实现计划或任何持久化设计产物，也不得写入项目 docs/；项目特定设计即使被称为“咨询”也必须经过 Gate。

---

## HTML 默认产物

面向人的架构正文默认使用 HTML artifact：同一套页面 shell，按文档类型装配不同内容模块。ADR 是明确例外：它主要供 Agent、review gate 和治理工具读取，结构固定、视觉复杂度低，因此新 ADR 必须使用 Markdown。

| 文档类型 | 默认承载 |
|---|---|
| 架构总览 / system overview / vertical cockpit | `docs/index.html` 或 `docs/architecture/<system>/index.html` |
| 核心流程 / 状态机 / 数据流 / 发布 / 故障恢复 / 容量测算 | `docs/architecture/<topic>.html` 或 `<component>/<topic>.html` |
| ADR 决策推理 | `docs/architecture/**/adrs/NNNN-*.md` + 同目录 `README.md` |
| User Story / domain model / testing strategy / user guide | `docs/product/**.html`、`docs/architecture/domain-model.html`、`docs/testing/**.html`、`docs/user-guide/**.html` |
| dashboard / timeline / flowchart / API reference | 同一 HTML shell + 对应 visual/documentation 模块 |

HTML-first 不是做展示页，而是让面向人的架构正文具备阅读路径、SVG 图、图例、ADR 链接、SSOT 导航和移动端可读性；它不覆盖 ADR 的 Markdown-only 规则。详细模板、图语言、校验 checklist 见 [`references/html-architecture-doc-writing.md`](references/html-architecture-doc-writing.md)。

### md 还是 html：**两者都可以**，按读者选

`docs/design/` 与 `docs/requirements/` 下 **md 和 html 都是合法产物**，不强制其一。下面是选型参考，不是门禁：

| 情形 | 更适合 | 为什么 |
|---|---|---|
| 会被 **agent 当输入读**，或需要在 MR 里**看 diff** | md | HTML 的 diff 噪音大，agent 解析也更费劲 |
| 主要给人读，且**论点依赖图**（时序、状态机、架构） | html（内联 SVG） | 图是论证的一部分，拆出去就散了 |

仓库页面能直接渲染 md；html 要么本地打开要么发到 Pages——**这只影响阅读便利，不影响合规**。
（`docs/architecture/` 下的架构树仍按上一节 HTML-first 执行。）

### 两棵按「生命周期」切的树

除上面按 system/component 切的架构树外，还有两棵按**粒度与生命周期**切的：

| 树 | 粒度 | 生命周期 | 内容 |
|---|---|---|---|
| `docs/design/<模块>/` | 按**模块** | 长期演进 | `README.md` 或 `README.html`（模块是什么·边界·依赖，入口）· `contract.md`（对外契约：接口·事件·数据形状）· `decisions/`（**一次决策一个文件，只增不改**——新决策推翻旧的就再写一个）· `<专题>.html`（需要图的专题） |
| `docs/requirements/<iid>/plan.md` | 按**需求** | 一次性，做完即归档 | 本次实现计划，见下节 |

两者都**随 feature 分支走完整晋级**，与代码同一条链。

**进仓的文档只写结论。** 争论、澄清、方案比选留在 issue comment 与频道 thread 里——否则 `docs/design/` 会退化成聊天记录的转录，丢掉「可被机器读」这个唯一价值。

---

## 整体工作流程（必须按序执行）

```
0. Requirements Analysis + Gate
                       → requirements-analysis-agent 固定来源、完成公司 Skill review
                       → 命中路由时包含条件式 product-tech-research review
                       → PO exact-hash ACCEPT
1. User Story          → 从 accepted REQUIREMENTS 派生用户体验流程和验收标准（不含技术细节）
2. 架构设计             → 先定 catalog 拓扑（Domain/System/Component, invoke service-catalog-onboarding）
                         → US 模式识别 → 苏格拉底追问 → brainstorming 发散
                         → 参考方法论收敛 → 输出 HTML 架构页 + domain-model.html + Markdown ADR
3. 开发/测试环境设计    → 为 AI Agent TDD 构建反馈闭环
4. 分 Component细节设计       → Component 级模式识别 → 追问 → 输出 Component HTML 细节文档
5. 分层测试方案（L2-L4）→ 基于 US + 技术方案 + 环境设计生成
6. CI/CD 部署文档       → pipeline 质量门禁 + 部署策略
7. 用户手册             → 面向终端用户的操作指南
```

---

## 文档目录结构

> **所有文档必须位于项目根目录的 `docs/` 下。** 下方树形图中 `docs/` 是根，所有子目录（`product/`、`architecture/` 等）都是 `docs/` 的子目录，绝对路径形如 `<project-root>/docs/product/prd.html`。不要在项目根目录下创建独立的 `product/` 或 `architecture/` 目录。

> **catalog 维度（Domain > System > Component）和 docs 维度可以正交**——catalog 描述 IDP / 部署 / 治理边界（SSOT 不变），docs 树则可以按**业务垂直能力（vertical）**重新组织 cross-end 内容。两套视图通过 `catalog-info.yaml` 的 annotation 桥接（`golf.addx.ai/vertical` / `backstage.io/techdocs-entity-path` / `backstage.io/adr-location`，精确格式见 [`service-catalog-onboarding` skill](../service-catalog-onboarding/SKILL.md)）。下方给出两棵候选 docs 树：**树 1（catalog-aligned，baseline）** 和 **树 2（vertical-first，可选）**——同一仓可以同时保留两种入口，但**同一设计主题只能有一个 SSOT**；非 SSOT 目录只放导航 / redirect，不重复正文。决策依据写 ADR，准入规则见 [§2.0.1](#201-vertical-视图准入规则-vs-system-子树视图)。

### 树 1（catalog-aligned，baseline）—— `docs/architecture/<system>/<component>/...`

```
docs/                                # <-- 所有文档的唯一根目录
├── TODO.html                        # 文档 review 跟踪清单（SSOT）
├── product-tech-research/           # docs/product-tech-research/ — 调研层：行业竞品与技术调研
│   ├── index.html                   # docs/product-tech-research/index.html
│   ├── competitors.html             # 竞品分析
│   ├── open-source.html             # 开源方案
│   ├── tech-trends.html             # 技术趋势
│   ├── user-feedback.html           # 用户反馈/VOC
│   └── compliance.html              # 合规标准
├── product/                         # docs/product/ — 产品层：用户视角
│   ├── prd.html                     # docs/product/prd.html
│   └── user-stories/
│       ├── index.html               # docs/product/user-stories/index.html
│       └── {service-or-component}.html # docs/product/user-stories/{service}.html
├── architecture/                    # docs/architecture/ — 架构层：系统设计决策
│   ├── index.html                   # docs/architecture/index.html（HTML-first；跨 System 总览 / 阅读路径）
│   ├── domain-model.html            # docs/architecture/domain-model.html — Ubiquitous Language SSOT
│   ├── tech-stack.html              # docs/architecture/tech-stack.html
│   ├── adrs/                        # docs/architecture/adrs/ — repo-wide / cross-vertical ADR（option C）
│   │   ├── README.md                # 机器可读 ADR 索引
│   │   └── NNNN-*.md                # 跨 Component / 跨 vertical 的决策
│   └── <system>/                    # docs/architecture/<system>/ — System 级目录（System 名 = catalog 的 metadata.name）
│       ├── index.html               # docs/architecture/<system>/index.html — System overview（装哪些 Component / 协作 / 对外 API）
│       └── <component>/             # docs/architecture/<system>/<component>/ — Component 级目录（Component 名 = catalog 的 metadata.name）
│           ├── index.html           # docs/architecture/<system>/<component>/index.html — Component overview
│           ├── {topic}.html         # docs/architecture/<system>/<component>/{topic}.html — 子组件 / 数据模型/状态机等专题
│           └── adrs/                # docs/architecture/<system>/<component>/adrs/ — per-Component Markdown ADR（option C，见 references/adr-format.md）
│               ├── README.md        # 机器可读 ADR 索引
│               └── NNNN-*.md        # 一文件一条 ADR（4 位零填充，单调递增；不删、被推翻标 Superseded）
├── testing/                         # docs/testing/ — 测试层：策略与用例
│   ├── strategy.html                # docs/testing/strategy.html
│   └── services/{service}/          # docs/testing/services/{service}/
├── deployment/                      # docs/deployment/ — 部署层：环境操作手册
│   ├── local-dev.html
│   ├── ci.html
│   └── cd.html
└── user-guide/                      # docs/user-guide/ — 用户手册：面向终端用户的操作指南
    └── {feature}.html               # docs/user-guide/{feature}.html
```

> **`<system>` / `<component>` 必须跟 catalog 实体名一字不差**（kebab-case，对应 `catalog-info.yaml` 的 `metadata.name` / `spec.system`），不是模糊的"服务名"。System 级是新增的层级（dome-Component → dome-System → Domain 三层导航），Step 2 的产出**必须包含 `docs/architecture/<system>/index.html`**。老仓若有 `index.md`，只作为迁移期 redirect / 导航，不再写新正文。
>
> **以 engagement 域为例**：
> ```
> docs/architecture/engagement-backend/engagement-service/    ← System engagement-backend 下的 Component engagement-service
> docs/architecture/engagement-console/engagement-admin/      ← System engagement-console 下的 Component engagement-admin
> docs/architecture/engagement-sdk/engagement-flutter-sdk/    ← System engagement-sdk 下的 Component engagement-flutter-sdk
> docs/architecture/engagement-sdk/engagement-ios-sdk/        ← System engagement-sdk 下的 Component engagement-ios-sdk
> ```
> ADR 的**内容、文件名和模板 SSOT** 只看 [本 skill `references/adr-format.md`](references/adr-format.md)。`service-catalog-onboarding` 仅用于确认 Domain / System / Component 拓扑、ADR 目录归属和 `backstage.io/adr-location` 接入；**不得把它的 ADR authoring / 文件格式段落作为规则来源**。跨 Skill 文本未同步前，本 skill 的调用边界必须阻止旧 HTML 规则进入同一工作流。

### 树 2（vertical-first，可选）—— `docs/architecture/verticals/<vertical>/...`

**适用场景**：项目里存在 **cross-end 业务能力**（同一业务能力既在 Flutter / App 端实现，又在 Go / 后端实现，或两端均已规划），并且这些能力的端云一图、字段对齐、跨端 sequence flow 经常需要在一处闭环。**纯单端 / 横切 infra / B 端 ops console 不进 verticals/**，留在树 1（System 子树）。

```
docs/architecture/verticals/<vertical>/  # <-- 一个 vertical 一个目录（kebab-case，如 sync / share / ai-coach）
├── index.html                           # HTML-first cockpit：业务价值 + 端云职责切分 + 关键 user flow（必填）
├── domain.html                          # 该 vertical 术语 & 数据模型（指回 docs/architecture/domain-model.html，不复写）
├── integration.html                     # 条件必填：当其它 vertical / App shell / package 要集成本 vertical 能力
├── services.html                        # 条件必填：当本 vertical 对外暴露 reusable service / public API / SDK
├── app/                                 # Flutter / App 端实现（合并所有相关 lattice package / lib Component）
│   └── index.html
├── backend/                             # Go / 后端实现（合并所有相关 Modular Monolith Component）
│   └── index.html
├── web/                                 # 可选：Web 端实现（仅 share / moments 这类有 web 实体的 vertical）
│   └── index.html
├── contracts/                           # 端云契约（REST / proto / 字段映射 / 版本演进）—— 跨端字段对齐 SSOT
│   └── api.html
├── data/                                # schema / ownership / migration
│   └── schema.html
├── flows/                               # 跨端 sequence diagram、user flow（横跨端云的核心 flow 归这里）
│   └── *.html
└── adrs/                                # vertical 内部 Markdown ADR（编号独立，从 0001 起；区别于顶层 docs/architecture/adrs/）
    ├── README.md
    └── NNNN-*.md
```

**两棵树共存示意（同一仓内）**：

```
docs/architecture/
├── index.html
├── domain-model.html
├── observability.html                  # 横切 → 顶层（不进 verticals/）
├── local-data-storage.html             # 横切 → 顶层
├── subscription/                       # 横切（公司平台接入）→ 顶层
├── adrs/                               # 跨 vertical 元 ADR（顶层）—— 见 §2d
│   └── NNNN-*.md
├── verticals/                          # 树 2：cross-end 业务能力
│   ├── index.html                      # vertical 索引 + 准入规则提醒
│   ├── sync/
│   ├── share/
│   └── ...
├── <system-A>/                         # 树 1：纯单端 / B 端 / 落地页等留 System 子树
│   └── <component>/...
└── <system-B>/
    └── <component>/...
```

> **vertical 不引入 Backstage 自定义 entity kind**——catalog 模型保持 Domain > System > Component 不动，vertical 是**文档维度**。两套视图通过 Component annotation 桥接，精确格式（`golf.addx.ai/vertical: <name>` / `backstage.io/techdocs-entity-path` 指向 `verticals/<name>/app/` 或 `/backend/` / `backstage.io/adr-location` 指向 `verticals/<name>/adrs/`）见 [`service-catalog-onboarding` skill](../service-catalog-onboarding/SKILL.md)。

### 代码一级目录 CLAUDE.md 模板

在 monorepo 代码一级子目录（`server/` / `admin/` / `moments/` / `app` 等，1 个目录 = 1 个 System）落一份**极薄 CLAUDE.md（≤20 行）**+ 同目录 `AGENTS.md` 相对路径软链。**只写两节**：必读 skill + 抽象规约。栈 / 命令 / Component 列表 / 详细 ADR 一律不写，全部让 AI 跳 `docs/architecture/<system>/index.html` / `go.mod` / `Makefile` 取权威。`CLAUDE.md` / `AGENTS.md` 是 agent 配置文件，不属于 `/architect` 生成的项目文档。

```bash
# 在代码一级子目录下，例如 server/
cat > CLAUDE.md <<'EOF'
# {dir-name} —— System `{system-name}`（叠加根 CLAUDE.md，详见 docs/architecture/<system>/index.html）

## 必读 skill
- `addx:service-catalog-onboarding` —— 改 API / 拉接入规格
- `addx:architect` —— 跨 Component 设计决策（§4c 引用规则）
- `addx:{system-specific}` —— 如 `dev-infra` / `operations-console-design` / `marketing-cms` ...

## 规约（违反会被 review 拦截）
- **做架构 / 接口 / 数据契约变更前，必读 `docs/architecture/<system>/<component>/adrs/` + `docs/architecture/adrs/` 里所有 `Accepted` 状态的 ADR**（不只是看标题，要读 Decision Outcome + Consequences 全文）；老仓若仍有 `docs/adr/` / `docs/adrs/` 也要读，但新 repo-wide ADR 不再创建到旧路径。新决策推翻旧的就写新 ADR；旧 ADR 改为 `status: Superseded`，并在 `superseded-by` 数组写入新 ADR ID，**不删**
- 跨 Component 调用走 ports interface（不准 import 对方 internal；CI: go-arch-lint / import-linter / ArchUnit）
- 不准直连的外部服务：列具体几个（如 FCM / Stripe / GrowthBook 走中台）
- 数据所有权：表 owner 唯一，跨 Component 取数走 ports 或事件
EOF
ln -s CLAUDE.md AGENTS.md
```

**为什么只写这两节**：
- **必读 skill** —— 让 AI `cd` 进来知道要 invoke 谁；这条信息 SSOT 在 CLAUDE.md，不放别处
- **抽象规约** —— 静态分析 CI 之外的"约定"，AI 容易踩；这条也只在 CLAUDE.md 显式写
- **其他都不写**：栈 → `go.mod` / `package.json` 是 SSOT；命令 → `Makefile` 是 SSOT；Component 列表 / 数据所有权矩阵 / API 契约 → `docs/architecture/<system>/index.html` 是 SSOT；当前阶段 / 子 ADR → 各 Component 的 `index.html` 或 topic HTML 是 SSOT。CLAUDE.md 复制 = 漂移源

---

## 规则

### Step 0 — Requirements Analysis + Gate

**做什么**：由 `requirements-analysis-agent` 固定 canonical source/digest，执行必选 `story-craftsman` review，并按其 `references/skill-routing.md` 判断是否需要 UAT、调研或安全合规 review。只有 PO 对精确 `artifact_id + artifact_hash` ACCEPT 后才进入 Step 1。

**调研规则**：产品技术调研不是每个架构请求的固定前置步骤。涉及竞品比较、开源选型、陌生/新兴领域、标准问题或证据依赖的产品定位时，由 `requirements-analysis-agent` 按路由条件调用 `product-tech-research`，形成条件式 product-tech-research review；每条 review 路由都记录 `SELECTED` 或 `SKIPPED` 及理由，不为补齐形式而制造调研文档。

**产出**：不可变 REQUIREMENTS Artifact 或 `ATTEMPT_NEEDS_INPUT`。若执行了研究，可另有 `docs/product-tech-research/` 证据，但它不能替代 Requirements Artifact 或 PO Gate。

---

### Step 1 — User Stories（`docs/product/user-stories/{service}.html`）

**写什么**：用户体验流程、Epic 划分、可验证的验收标准（AC）。可以包含 NFR（非功能需求）。

**绝对禁止**：技术实现细节（不写类名、API 路径、数据库字段、协议细节）。User Story 是用户视角，不是技术规格。

**怎么写**：从 accepted REQUIREMENTS Artifact 派生；需要生成项目 HTML US 时再委派 [`story-craftsman`](../story-craftsman/SKILL.md)。`story-craftsman` 已在 Step 0 作为 review pass 执行，此处不得重新解释或改变已接受需求。它是 US 文档结构的 SSOT，覆盖：

- 引导式访谈协议（信息不足时如何向用户挖掘背景 / 目标 / AC）
- Epic-first 文档结构纪律（Epic 顶级 `##`、US 是 `###`、按 stakeholder 视角切 Epic）
- 优先级表达规则（属性而非组织维度，禁用 `## P0` 顶级 section）
- 推荐结构骨架 + ❌/✅ 反例对比
- Future Epic 单独顶级 section、本期明确不做清单
- ID 命名规范、AC Given/When/Then 格式、进展状态标记
- 文件按服务 / Component拆分 + 索引表同步规则
- 向后兼容（旧文档不强制翻新）

architect 不在本节重复 story-craftsman 的规则，避免规范分裂。Review 一份 US 是否合规，对照 [`story-craftsman/SKILL.md`](../story-craftsman/SKILL.md) 即可。

**配套 Skill**：`story-craftsman`

**PRD 入 Git 约定（需求 SSOT）**：issue 正文是需求唯一正本；PRD 定稿必须提交 Git（`docs/product/prd/{service}.html`）并在 issue 回链。飞书 PRD 仅作讨论输入/迁移期兼容——用 `feishu-auth` 读取后必须落盘到该路径并回链 issue，**禁止飞书链接作为唯一载体**。（#61 X-3，详见 docs/architecture/skill-artifact-delivery-implementation.html §10）

---

### Step 2 — 架构设计（`docs/architecture/`）

Step 2 是架构师的核心工作。**不要读完 US 就直接画架构图** — 先定 catalog 拓扑（Domain / System / Component），再识别 US 暗含的架构关注点，通过追问收敛设计决策，最后落地为文档。

#### 2.0 先定 catalog 拓扑（Domain / System / Component）—— hard prerequisite

**所有后续设计都依附在 catalog 实体上**（`docs/architecture/<system>/<component>/...` 路径、ADR 归属、API 实体、TechDocs 深链都按 catalog 拓扑组织）—— 没有 catalog 拓扑 = 文档放哪儿都靠猜。

进入 §2a 模式识别之前，先从 `catalog-info.yaml` 和现有架构文档核对以下三件事（顺序固定）。**只有拓扑缺失、冲突或需要新增实体时**，才 invoke [`service-catalog-onboarding` skill](../service-catalog-onboarding/SKILL.md)，且调用范围只限其 Domain / System / Component 粒度判定、实体声明位置和 catalog 注解；**ADR authoring、扩展名、模板和索引格式不得委托给该 skill**，统一回到本 skill 的 [`references/adr-format.md`](references/adr-format.md)：

1. **Domain**（业务领域 / DDD 限界上下文）—— 看 [`service-catalog-onboarding §1`](../service-catalog-onboarding/SKILL.md#1-domain--system--component-粒度判定canonical-ssot) 的「限界上下文测试 5 条」+「Domain 实体声明位置 canonical 规则」。判定它属于哪个父域、要不要建子 Domain、`kind: Domain` 实体声明在哪个 repo。
2. **System**（治理边界）—— 看 [`service-catalog-onboarding §1`](../service-catalog-onboarding/SKILL.md#1-domain--system--component-粒度判定canonical-ssot) 的「System ≠ 代码分包、= 治理边界」。判定切几个 System、System 名（业务能力名，不是 API 名）。
3. **Component**（业务高内聚单元，不是「必须独立部署」单元）—— **PRIMARY 判据 = 业务逻辑独立性**（不是「能不能独立部署」）。完整三条判据 + type-specific publish 形态参考矩阵详见 [`service-catalog-onboarding §1 三层定义`](../service-catalog-onboarding/SKILL.md#三层定义)。要点：
   - **三条全 yes 才拆 Component**：① 业务逻辑高内聚（独立变更频率 / owner）② 接口契约清晰（对外只走 ports，详见 [§4c](#4c-跨-component-引用规则hard-rule)）③ 有独立可演进可能。三条都满足但代码暂时共部署 / 共 binary / 共 pubspec / 共 build artifact —— **仍然拆 Component**（catalog 是业务边界 SSOT，不是部署形态 SSOT）
   - `spec.type` 决定 publish artifact 形态参考（不是拆 Component 的 gate）：service → K8s Deployment；library → pub / npm / cargo / Conan / Bazel package；mobile-app → IPA/APK/AAB；firmware → .bin/.uf2/.hex；website → nginx/CDN/Vercel
   - **典型形态**：Flutter / 嵌入式 monorepo —— 业务边界清晰的功能块 = N 个 `library` Component（即使代码还在单体 lib/ 没拆 melos / Conan 也照拆）。**顶层 IPA/APK / .bin 形态按 case 三选**（详见 [`service-catalog-onboarding §1` 三层定义](../service-catalog-onboarding/SKILL.md#三层定义)）：① SDK / 库仓 → 无顶层 Component；② App / 固件 + 极薄 shell（默认推荐）→ 无顶层 Component，mobile-app/firmware config 挂 System annotations；③ App / 固件 + 厚 shell → 1 个 mobile-app / firmware Component 装 shell。**强行为极薄 shell 建 Component 会违反 Component 三条判据的 ②③**（shell 一年不变更 = 没有独立演进可能）。Service 共部署 multi-Component 形态见 [§4.1.1 Composite Service](../service-catalog-onboarding/SKILL.md#411-共部署多-componentcomposite-service形态1-system--n-component共享-kubernetes-id)
   - **方案设计 vs 代码现状可解耦**（hard rule）：catalog 可写 N Component 描述**当前业务边界**，代码暂未物理拆开也合法 —— N 个 Component 的 `source-location` 全部指向**当前代码里对应的子目录**即可。**禁止**指向不存在的 future melos / Conan 路径，否则 Backstage 实体页 "View Source" 全部 404
   
   每个 Component 的 `metadata.name` kebab-case；同 catalog 全局唯一（如 backend 已有 `golf-players` Component，App 端同语义 lib 用 `golf-players-flutter` 后缀避冲突，参考 [engagement-sdk 命名](../service-catalog-onboarding/SKILL.md#实例-a--平台型产品增值业务跨多个产品线复用)）。

**产出**（写进 `docs/architecture/index.html` 的拓扑/阅读路径区；老仓如有 `index.md` 只作迁移期 redirect；同时补仓根 `catalog-info.yaml` 草稿）：

| 拓扑层 | 名字 | 声明位置 | 备注 |
|---|---|---|---|
| Domain | `<domain-name>` | 本仓 `catalog-info.yaml` 顶部（如果本仓 own）/ `engineering/architecture/catalog/domains.yaml`（如果是 BU 父域无 owning repo） | 见 service-catalog-onboarding §1 |
| System | `<system-name>` × N | 本仓 `catalog-info.yaml`（`spec.domain` 指上面 Domain） | 多 System 用 `---` 分割 |
| Component | `<component-name>` × M | 本仓 `catalog-info.yaml`（`spec.system` 指所属 System） | type 决定独立部署判据（service 共部署 → 共享 `kubernetes-id`，§4.1.1；library 是各自可独立 publish 的 package；mobile-app / firmware 是顶层组装入口 dependsOn N 个 lib） |

**两个参考样例**（service-catalog-onboarding §1 实例节）：
- **垂直产品域**（如 [Golf](../service-catalog-onboarding/SKILL.md#golf--垂直产品域示例复杂业务--3-system-共部署多-component)）：1 Domain + 多 System（端 / B 端管理 / 落地页拆开）+ 共部署多 Component
- **平台型产品域**（如 [Engagement](../service-catalog-onboarding/SKILL.md#实例增值业务可当模板)）：1 Domain + 多 System（backend / console / sdk 拆开）+ 各 System 内的 Component

这一步**没做完，不要进 §2a**。`docs/architecture/<system>/<component>/` 没有合法的 `<system>` / `<component>` 就开始写架构文档 = 后面要全部 mv。

#### 2.0.1 vertical 视图准入规则（vs System 子树视图）

catalog 拓扑定完后，**再决定 docs 树怎么组织**——是 baseline 的 System > Component 子树（树 1），还是 vertical-first（树 2），还是两棵共存。判据如下：

**走 vertical 视图（树 2）的信号**——满足任一即推荐：

- 同一业务能力**既有 Flutter / App 端实现，也有 Go / 后端实现**（或两端都已规划），需要端云一图闭环（如 sync / share / play / ai-coach）
- 端云字段对齐需要 SSOT（同一指标 / 同一资源在端有 SQLite schema、云有 MySQL schema、契约有 REST/Proto，三处必须对齐）
- 存在横跨端云的 sequence flow（如"App 启动 → 调 N 个 endpoint → outbox 落库 → 下行 fan-out"），挂任一端都缺另一半上下文

**留 System 子树视图（树 1）的信号**——满足任一即留树 1：

- **纯单端**：纯 Flutter app-only（如 design-system / api-client / bridge / database / telemetry 这类纯端 infra）或纯 Go server-only Component
- **横切关注点**：observability / database / subscription（公司平台接入）等跨多个 vertical 复用的基础设施 → **不进 verticals/**，留在 `docs/architecture/` 顶层（如 `docs/architecture/observability.html` / `docs/architecture/subscription/`）
- **B 端 ops console / 落地页 SPA**：跨多个 vertical 但本身是独立 System（如 admin / moments），按 System 子树描述更自然

**vertical 内部目录结构**（树 2 已示）：基础目录 `index.html` / `domain.html` / `app/` / `backend/` / `web/`（可选）/ `contracts/` / `data/` / `flows/` / `adrs/`，**全部建 stub**（即使内容暂为空），避免后期再改结构。老仓 `index.md` 只在迁移期作 redirect / 导航，不重复 `index.html` 正文。`integration.html` / `services.html` 是条件必填：只要该 vertical 被其它 vertical、App shell、SDK/package 或 backend component 集成，就必须写。

#### 2.0.2 vertical 文档防 drift 规则

详细写法、模板和 review checklist 见 [`references/vertical-doc-writing.md`](references/vertical-doc-writing.md)。本节只保留必须遵守的硬规则。

vertical-first 文档的核心风险是同一机制被 `index.html`、`app/`、`backend/`、`flows/` 重复描述后发生 drift。写 vertical 文档时必须按以下规则组织：

| 规则 | 要求 |
|---|---|
| `index.html` 是设计驾驶舱 | 必须有业务价值、端云职责、架构图、核心流程图、DDD 边界、代码结构、SSOT 导航、关键决策表。核心 cross-end flow 要上浮到 HTML cockpit；细节再链接子文档。 |
| `问题 -> SSOT -> 引用方式` 表 | `index.html` 必须列出关键问题的唯一权威文档，例如字段定义、契约、同步机制、测试策略、当前代码差距分别去哪看。其它文档只引用，不复制。 |
| `domain.html` 是 vertical 统一语言 | 不只写术语，还要写 ID 生成/作用域、数据生产 ownership、不变量、canonical catalog/alias/unit（如指标类字段）。全局概念链接 `docs/architecture/domain-model.html`，不复写。 |
| `flows/` 只放跨边界流程 | 横跨 App / Backend / Embedded / Media / Sync 的 sequence 放 `flows/`；单端内部机制放 `app/`、`backend/`、`embedded/`；通用机制放 owning technical vertical。 |
| integration/service 单独成文 | 当 vertical 是 reusable capability（如 sync）或被其它 vertical 接入，`integration.html` 写宿主如何集成，`services.html` 写对外 public service/API；不要埋在 app/backend 内部实现里。 |
| 边界规则要写 forbidden path | 除了画图，还要列“不允许什么”：例如业务方不得直写内部队列、App 不直调业务 sync endpoint、二进制不进 sync、package 不处理 auth header。 |
| 目标设计可超前代码 | 目标态写在设计 SSOT；若代码尚未完全匹配，在相关设计文档里写“当前代码对齐状态/差距”。不要写完成百分比；进度状态归 `dev-workflow` 的 `PROGRESS.html` 或项目进度页。 |

**vertical ADR 编号规则**：

| ADR 类型 | 位置 | 编号 |
|---|---|---|
| 跨 vertical 元 ADR（如"vertical 准入规则"本身、modular-monolith / shared-db / 跨 vertical 边界）| `docs/architecture/adrs/NNNN-*.md`（顶层）| 全仓单调递增 |
| vertical 内部 ADR（如 sync 的 Phase 1/2 路线、share 的端云签名方案）| `docs/architecture/verticals/<vertical>/adrs/NNNN-*.md` | **本 vertical 内独立从 0001 起**，不与顶层 / 其它 vertical 共享编号空间 |
| Component 级 ADR（仍只影响单个 catalog Component）| `docs/architecture/<system>/<component>/adrs/NNNN-*.md`（树 1）| Component 内独立 |

**与 Backstage 桥接的 3 个 annotation**——catalog Component 段加：

- `golf.addx.ai/vertical: <name>`（或项目自定义 namespace；横切 Component 用 `_horizontal`）—— 把 catalog Component 反向归到 vertical
- `backstage.io/techdocs-entity-path: docs/architecture/verticals/<vertical>/app/`（或 `/backend/`）—— TechDocs 深链指向 vertical 内的端/云子目录
- `backstage.io/adr-location: docs/architecture/verticals/<vertical>/adrs/`（或 Component 自己的 `adrs/`）—— Backstage ADR 插件渲染来源

精确 YAML 例子和 CI 检查在 [`service-catalog-onboarding` skill](../service-catalog-onboarding/SKILL.md)（不在本 skill 重复）。

**决策落地**：选哪套视图（仅树 1 / 仅树 2 / 两棵共存）**必须写 ADR**——一条跨 vertical 元 ADR 放 `docs/architecture/adrs/`，记录准入规则、横切件归属、迁移策略（含老路径兼容期）。

#### 2a. US 模式识别

读完 Step 1 的 User Stories 后，识别其中暗含的架构关注点。以下是常见的 US 模式和对应的架构信号：

| US 中出现... | 架构信号 | 需要追问的方向 |
|-------------|---------|-------------|
| 多个租户 / OEM 客户 / 白标 | 多租户隔离 | 数据隔离级别、配置差异度、故障爆炸半径 |
| 实时 / 推送 / 直播 / 在线状态 | 实时通信 | 延迟容忍度、推送失败策略、并发连接规模 |
| 定时 / 周期 / 自动执行 | 调度系统 | 失败补偿 vs 跳过、并发触发互斥、时区处理 |
| 设备 OTA / 固件升级 / 远程控制 | 离线 + 最终一致 | 设备离线补推、升级中断恢复、灰度 + 回滚 |
| 第三方支付 / 外部 API 对接 | 外部集成 | 故障隔离、重试幂等、对账机制 |
| 审计 / 合规 / 操作记录 | 审计追踪 | 不可变性、保留期限、访问控制 |
| 订单 / 工单 / 设备生命周期 | 状态机 | 状态定义、转换规则、并发转换、非法转换处理 |
| 跨服务协调 / 多步骤流程 | 分布式事务 | 最终一致 vs 强一致、补偿机制、超时处理 |
| 搜索 / 报表 / 数据分析 | 读写分离 | 查询模型 vs 写入模型、数据同步延迟容忍度 |
| 任何新功能 / 用户行为变更 | **可观测性** | 如何衡量成功？需要哪些指标？需要埋点吗？需要 A/B 实验验证吗？ |

**不是所有 US 都会触发所有信号。** 一个简单的 CRUD 功能不需要被追问分布式一致性。只追问与当前 US 相关的方向。

#### 2a+. 查公司现成的服务 & 能力（识别到需要平台能力时，必做）

只要 2a 识别出方案要用到某个平台能力——发推送、灰度发布、权益/订阅校验、用户画像、设备上下行、通知、A/B 实验、支付……——**先 invoke `service-catalog-search` 的 `find-capability`**（自然语言即可，如「我要发推送」「判断用户是否在订阅期」），查清楚：

- 公司有没有现成提供这个能力的服务（哪个 `Component` / 哪个 `API`），它现在能用吗（`lifecycle`）；
- 有没有「不准直连 X」的架构约束（如 `no-direct-fcm`/`no-direct-stripe`——业务方必须走中台服务，不能自己接第三方）；
- 怎么接（`get-integration-spec` 拿接入规格 + API 契约）。

把结论写进 HTML 架构页的 `问题 -> SSOT -> 引用方式` 表（依赖哪个服务/能力、为什么）。**不要凭记忆**——凭记忆容易重复造轮子，或写出违反架构约束的方案（自己接 FCM、自己调 Stripe）。门户离线时该 skill 会自动降级直读 GitLab 的 `catalog-info.yaml`。

#### 2b. 苏格拉底追问

对识别出的每个架构关注点，通过追问而非直接给答案来收敛设计决策。追问的目标是让用户（或自己）显式回答以下问题：

**边界划分**（对应 DDD Bounded Context）：
- 这两块逻辑的变更频率是否不同？
- 它们的故障是否需要隔离？（A 挂了不应该影响 B）
- 它们的数据是否有明确的 ownership？（谁是 source of truth）

**集成模式**：
- 调用方需要立即拿到结果吗？→ 同步 vs 异步
- 下游挂了，上游应该失败还是继续？→ 耦合程度
- 这个操作需要幂等吗？（重复调用会怎样）

**可观测性**（每个功能都必须追问）：
- 这个功能上线后，怎么知道它是成功的？→ 定义核心指标（转化率、留存、延迟 p95 等）
- 需要新增埋点吗？→ 用户行为事件（曝光/点击/转化漏斗）
- 需要 A/B 实验验证吗？→ 方案不确定时，用实验数据代替猜测
- SLA 指标是什么？→ 可用性、响应时间、错误率的量化目标
- 出问题时怎么发现？→ 告警规则、Dashboard、日志关键字

**设计约束**：
- 最关键的非功能需求是什么？（延迟？吞吐？一致性？可用性？）
- 哪些是硬约束（不可妥协），哪些是软约束（可以 trade-off）？
- 团队对哪些技术最熟悉？（技术选型要考虑团队能力）

#### 2c. 架构质量原则

设计方案时必须对照以下原则。这些原则也是 `code-review` 审查设计质量的评判标准。

**结构性原则**：
- **高内聚低耦合**：Component 边界清晰，每个 Component只做一件事，变更不扩散
- **依赖方向**：稳定 Component不依赖不稳定 Component，依赖单向流动
- **垂直切片**：按业务领域组织而非技术栈分层
- **Core Component分离**：核心业务逻辑独立于框架和基础设施，可在不同环境复用
- **Ports & Adapters**：业务逻辑与外部依赖严格隔离，所有 I/O 通过 Port 接口定义，不直接依赖具体实现

**健壮性原则**：
- **故障隔离**：外部依赖挂了有降级/熔断/超时方案
- **幂等性**：写操作重复调用安全
- **数据一致性**：跨表/跨服务操作的事务边界正确
- **输入校验**：系统边界处校验用户输入
- **内置 Stub**：每个外部依赖有对应 Stub/Mock，测试环境无需外部服务在线

**可演进性原则**：
- **扩展点**：为已知的未来变化预留接口
- **接口最小化**：API 只暴露必要信息，不泄露内部实现
- **命名即设计**：变量、函数、接口名必须准确表达意图；命名不只是可读性问题——它决定 AI Agent 能否正确理解语义
- **每条规则写 Why**：文档、注释、commit message 说清楚"为什么"，不只是"是什么"；ADR、AC、配置项都需要 rationale

#### 2d. 方案发散与收敛

追问收集到足够信息后：

1. **调用 `superpowers:brainstorming`** 发散探索可能的方案
2. **参考 `references/` 中的方法论决策卡** 评估方案（见本 skill 的 references/ 目录）
3. **收敛**：选定方案，**按影响范围写成一条 Markdown ADR** —— 单 Component 放 `docs/architecture/<system>/<component>/adrs/`，vertical 内部放 `docs/architecture/verticals/<vertical>/adrs/`，repo-wide / 跨 vertical 放 `docs/architecture/adrs/`；全部使用 [`references/adr-format.md`](references/adr-format.md) 的机器友好 Markdown 模板（什么时候写一条 ADR、命名/编号、front matter、状态生命周期、索引和 Backstage ADR 插件衔接）。

**一条新 ADR 的标准模板**（细节看 adr-format.md）：标题（一句话决策）+ YAML front matter 五字段 + **Context and Problem Statement** + **Considered Options**（≥2，不写选项=没在做决策）+ **Trade-off Analysis**（收益、代价、风险、适用条件）+ **Decision Outcome** + **Consequences**（得到什么 / 接受什么代价 / 后续动作）。`/architect` 新建 ADR 时五段齐全；独立 `code-review` 为兼容历史提升文档可把章节或索引漂移记为非阻断警告，这不改变新文件的默认 authoring 模板。

**模板 SSOT 在 [`references/adr-format.md`](references/adr-format.md)**（本 skill 自带）—— 含 YAML front matter（`status` / `date` / `deciders` / `supersedes` / `superseded-by`）+ 5 sections（Context and Problem Statement / Considered Options / Trade-off Analysis / Decision Outcome / Consequences）+ 文件命名规则（`NNNN-<kebab-slug>.md`，4 位零填充）+ Status 生命周期 + ADR 索引 `README.md` 样板。落地侧（放哪个目录、option C：per-Component vs repo-wide、`backstage.io/adr-location` 注解）仍按 catalog 规则；若其它 Skill 仍写 HTML ADR，视为待同步的旧规则，**不得覆盖本模板的新 ADR Markdown 规范**。

**待定 ADR**：当追问尚未得到回答、信息不足以做出决策时，**必须创建待定 Markdown ADR**（front matter `status: Pending`），而不是跳过不写。待定 ADR 记录已知的 Context 和 Options，**Decision Outcome 节明确写"需要回答以下问题才能决策：1) ... 2) ... 3) ..."**（不要写空 ADR / "TBD" / "待补"）。这样确保不确定的决策点被显式跟踪，不会在后续设计中被遗忘。Pending ADR 仍按影响范围放到正确 ADR 目录，不放临时目录。

**改决策**：绝不删 ADR、绝不改已 Accepted 的 ADR 的决策内容 —— 要改就新写一条 ADR；新 ADR 的 `supersedes` 数组写旧 ADR ID，旧 ADR 改为 `status: Superseded` 且 `superseded-by` 数组写新 ADR ID（双向链；详见 adr-format.md 的 Status 生命周期段）。

**ADR 落地位置（按影响范围三选一）**：① 单 Component 决策 → `docs/architecture/<system>/<component>/adrs/NNNN-*.md`（树 1）；② vertical 内部决策（如 sync 的 Phase 1/2 路线、share 的端云签名方案）→ `docs/architecture/verticals/<vertical>/adrs/NNNN-*.md`（树 2，编号独立从 0001 起，见 [§2.0.1](#201-vertical-视图准入规则-vs-system-子树视图)）；③ 跨 vertical 元决策 / repo-wide 决策（如 modular-monolith、vertical 准入规则本身）→ `docs/architecture/adrs/NNNN-*.md`。

#### 2e. 文档产出

**产出文件**：
- `index.html` / `<topic>.html` — 架构总览 / 设计驾驶舱（设计目标表 → SVG 架构图 → 核心流程图 → DDD 边界 → 组件职责表 → `问题 -> SSOT -> 引用方式` → 子文档导航表）
- `domain-model.html` — 全局 Ubiquitous Language SSOT（业务术语 <-> 技术术语映射，本质是 DDD 统一语言的落地）；vertical 内使用 `domain.html` 维护该 vertical 的 ID、ownership、不变量和 catalog
- `integration.html` / `services.html` — 条件产出；当能力被其它 vertical / App shell / SDK / backend component 集成时必须写接入方式与 public service 边界
- ADR 记录在独立 Markdown 文件（按 §2d ADR 推理链 + [`references/adr-format.md`](references/adr-format.md) 模板写）；HTML 页里**不内嵌 ADR 全文**，保留一个 ADR 索引表（`# / 决策 / 状态 / 日期 / 链接`）指向 `.md` 文件。`catalog-info.yaml` 的 Component 加 `backstage.io/adr-location: <adrs 目录>` 注解 → 门户的 ADR 标签页就能渲染。

#### 2e.1 catalog-aware doc 路径 — `docs/architecture/<system>/<component>/`

`/architect` 在写 `docs/architecture/{X}/...` 之前，**先读仓根 `catalog-info.yaml`**，取出该 Component 的 `metadata.name` 和 `spec.system`，把 `{X}` 解析成真实的 `<system>/<component>`（kebab-case，跟 catalog 实体名一字不差），不是模糊的"服务名"或"Component 名"。

| 文档层级 | 路径 | 内容 |
|---|---|---|
| **Domain 总览** | `docs/index.html`（或既有站点根首页） | 这个 Domain 是干嘛的、有哪些 System、跨域依赖谁 |
| **Domain 架构** | `docs/architecture/{overview,topic}.html` + `domain-model.html` | 跨 System 总览、Ubiquitous Language SSOT |
| **System overview**（**Step 2 必产出**） | `docs/architecture/<system>/index.html` | 这个 System 装哪些 Component、它们怎么协作、对外 API 是什么 |
| **Component overview** | `docs/architecture/<system>/<component>/index.html` | Component 内部架构、Component、依赖 |
| **Component 细节** | `docs/architecture/<system>/<component>/{topic}.html` | 子组件 / 数据模型 / 状态机等专题 |
| **per-Component ADR** | `docs/architecture/<system>/<component>/adrs/NNNN-*.md` | 影响单个 Component 的决策（option C） |
| **repo-wide ADR** | `docs/architecture/adrs/NNNN-*.md` | 跨 Component / 跨 vertical 的决策（option C；`docs/adr/` / `docs/adrs/` 仅作老仓兼容路径，不再新建） |

这与 [service-catalog-onboarding §4.1](../service-catalog-onboarding/SKILL.md#41-docs-镜像-catalog-层级domain--system--component) 完全对齐 —— 那边是接入规范（docs/ 镜像 catalog 是为了 TechDocs Approach B 的 `techdocs-entity-path` 深链稳定），这边是生成约定（`/architect` 写文档时按这一套放）。**共一套层级**。

> **Domain 声明位置规范见 [service-catalog-onboarding §1](../service-catalog-onboarding/SKILL.md#1-domain--system--component-粒度判定canonical-ssot)**（canonical SSOT）：判据是「有没有 owning repo」，不是「是不是叶子」—— 有 owning repo 的 Domain（任何层级，不限叶子）在那个 repo 根 `catalog-info.yaml` 顶部声明（带 `subdomainOf` 指父域如有），同仓还声明域内任何没有独立 owning repo 的 sub-Domain；只有没有 owning repo 的 Domain（BU 父域 / 跨多仓的纯聚合分组）才在 `engineering/architecture/catalog/domains.yaml`。`/architect` 在 `docs/index.html` 写 Domain 总览时，要清楚你这仓有没有 own 一个 Domain —— 决定 `kind: Domain` 实体放哪。

**校验 hook**：写新文档前先 `grep` 仓根 `catalog-info.yaml`，确认目标路径里的 `<system>` / `<component>` 名是 catalog 里真实存在的实体；否则报错（"target system 'foo' not found in catalog-info.yaml; available systems: [bar, baz]"）而不是创建漂移目录。漂移目录会让 TechDocs 的 `techdocs-entity-path` 深链 404 —— 设计阶段省 5 分钟、上线后排查 1 小时。

**文档规范**：
- HTML 不为压行数牺牲清晰度；长页必须有大纲、导航、分区和稳定锚点，必要时拆成多个 HTML 专题页
- 在页面 header metadata 记录文档状态、日期、owner / deciders，以及 **`source_issue` 字段**（需求 GitLab issue URL，必填；缺失视为需求无源，先回 Step 1 补 issue）
- **所有术语必须与 `domain-model.html` 对齐**——写作前先查阅，不得另起概念
- 引用其他文档用相对路径；子系统文档顶部加三列导航表（子 Component | 文档 | 说明）
- 复杂架构图、状态流、数据流优先用 SVG + 语义色；Mermaid 仅用于轻量辅助图，节点换行用 `<br/>`，**禁止用 `\n`**

**配套 Skill**：本 skill 的 HTML 方法 + `visual-documentation-skills` 全部五类（architecture / technical doc / flowchart / dashboard / timeline），以及 `superpowers:brainstorming`（方案发散）。

**G2 Gate（方案 → 实施）**：Step 2 产出的架构方案文档必须走 MR，**获得 ≥1 个 approve 后才可作为实现依据**（个人仓豁免 approve 但 MR 照建留痕）。approve 前架构页仅是提案；approve 后在 issue 追加 comment 回链方案 MR。红线：AI 不得自我批准。Gate 完整定义在 `dev-workflow` 的 G2 Gate 一节。（#61 X-2 / G-4，详见 docs/architecture/skill-artifact-delivery-implementation.html §10）

---

### Step 3 — 开发/测试环境设计（`docs/deployment/local-dev.html`）

**写什么**：L1/L2/L3 分层环境架构、多 worktree 端口隔离方案、共享通道切换机制、热重载配置。目标是为 AI Agent 构建可快速反馈的 TDD 闭环。

**文档规范**：
- `local-dev.html` 分两节：**用户指南**（命令和结果，给开发者看）+ **实现方案**（设计原因，给维护者看）
- 以 `make` target 为操作入口，禁止文档中出现裸命令

**配套 Skill**：
- `dev-infra`（L1/L2/L3 分层设计、端口 hash offset、共享通道排他切换、健康探活）
- `mock-engine`（Mock 基础设施：启动/停止 mock 服务、加载测试数据、创建测试场景）
- `prom-grafana-dev`（本地 Observability 契约回归测试，dashboard/alert rules/PromQL round-trip）

#### Step 3.5 — Monorepo 项目结构推荐（条件触发）

**触发条件**：项目采用 **Monorepo + Everything as Code** 模式（单仓含多栈：前后端 / 测试 / 埋点 / dashboard / A/B / preview 接入 / CI 都在一仓）。多仓或单栈项目**跳过**。

**目标**：按统一布局组织仓库，便于 AI Agent 扫描即懂、新栈加入不改现状、跨栈共享 fixture 不自建副本。

**推荐结构（customer-care 实战验证）**：

```
project-root/
├── Makefile                         # 唯一 dev 入口（禁止裸命令）
├── CLAUDE.md                        # AI Agent 必读约定
├── docker-compose.local-dev.yml     # L1 本地依赖（profiles: l1 / l3）
├── .gitlab-ci.yml                   # 单一 CI pipeline
├── docs/                            # 按本 architect skill 组织
├── scripts/                         # dev-env.sh 等
│
├── # 应用代码（按业务 role，非 tech stack）
├── cmd/ · internal/                 # Go backend
├── admin/                           # Next.js 管理后台
├── app/                             # Flutter SDK / mobile
├── personalization_engine/          # JS 引擎
├── dbt/                             # 数据转换
│
├── # 横切关注点（跨栈复用）
├── e2e/
│   ├── golden-data/                 # 跨栈 scenario fixture SSOT
│   ├── api-e2e/ · playwright/ · maestro/   # 不同客户端 e2e
│   ├── observability-local/         # verify.sh
│   ├── migrations/ · seed/
├── migrations/                      # 生产 DB migrations
├── mock/ · smoke/                   # Mock infra / API synthetic
├── test-fixtures/                   # Mock / stub / cassette
│   ├── wiremock/ · vcr/ · pact/ · seed-sql/
│
├── # Infra as Code（每个都版本化）
├── k8s/                             # k8s manifests
├── apps/preview/                    # preview 接入模板
├── grafana/                         # Dashboard JSON + provisioning
├── prometheus/                      # alert rules YAML
├── growthbook/                      # feature / experiment / metric
└── tracker/                         # 埋点 schema 定义
```

**结构纪律（5 条硬性）**：

1. 应用代码按**业务 role** 命名（`admin` / `app` / `internal`），**不按 tech stack**（不叫 `go/` `ts/` `dart/`）
2. 横切目录固定命名：`e2e` / `mock` / `smoke` / `migrations` / `test-fixtures`
3. Infra as Code 独立目录 + 命名与工具一致：`k8s/` `grafana/` `prometheus/` `growthbook/` `tracker/` `apps/preview/`
4. 根目录只放全局入口：`Makefile` / `CLAUDE.md` / `docker-compose.local-dev.yml` / `.gitlab-ci.yml` / global lockfiles
5. 跨栈共享数据一律在 `e2e/golden-data/` 或 `test-fixtures/`，**禁止各栈自建副本**

**Everything as Code 资产清单**（必须版本化进 git）：

| 类别 | 位置 |
|------|------|
| 应用代码 | `src/` / `app/` / `cmd/` / 各栈目录 |
| 测试 + scenario fixture | `tests/` / `e2e/golden-data/` / `test-fixtures/` |
| 观测 | `grafana/dashboards/*.json` + `prometheus/rules/*.yaml` |
| 埋点 | `tracker/schemas/` + SDK track 调用 |
| A/B | `growthbook.yml` / `growthbook/` |
| 部署 | `.gitlab-ci.yml` / `k8s/` / `apps/preview/` |

**反模式（禁用）**：Grafana UI 手改 dashboard 不同步 JSON；tracker-manager UI 加 schema 不进仓；preview 手 `kubectl apply` 不进 ApplicationSet。

**协作 Skill**：`tdd-infra`（待建，编排 TDD 设施建设）；详细设计见 `docs/architecture/tdd-infra/index.html`（或项目兼容导航）。

---

### Step 4 — 分 Component 细节设计（`docs/architecture/<system>/<component>/index.html`）

**模型**：catalog 中 1 个 System 可装 N 个 Component（兄弟关系），不论物理部署形态（独立部署 / 共部署在同一 binary）。1 个 Component 在 catalog 中是 1 个 `kind: Component` 实体，对应 1 个文档文件夹 + 1 个 `kind: API` 实体（如对外暴露 API）。

System 级 overview 放 `docs/architecture/<system>/index.html`（含 System 级 Component 协作图、对外 API、Component 间边界）；每个 Component 放 `docs/architecture/<system>/<component>/index.html`。老仓如有 `index.md`，只作迁移期 redirect / 导航，不重复正文。路径与 Step 2e.1 的 catalog-aware 规则完全一致，并与 [service-catalog-onboarding §4.1](../service-catalog-onboarding/SKILL.md#41-docs-镜像-catalog-层级domain--system--component) 对齐。

**写新 Component 文档前先 grep `catalog-info.yaml`** 确认 `<system>` / `<component>` 是真实存在的实体名（每个 Component 在 catalog 中独立声明，不论是否共部署）。

**Component 文件夹结构**：

```
docs/architecture/<system>/
├── index.html                     # System overview（Component 列表 / 协作图 / 对外 API 汇总）
├── <component-A>/
│   ├── index.html                 # 主文档（API / 表 / ports / 事件 / 跨 Component 依赖 / 监控 / 测试）
│   └── <subtopic>.html            # 可选：单独拆出的复杂子主题（如复杂状态机 / 内部架构图）
├── <component-B>/
│   └── index.html
└── ...
```

每个 Component 自己一个文件夹（不是单个散落页面）的理由：

- **catalog 一致性**：1 文件夹 = 1 `kind: Component` 实体 = 1 `kind: API` 实体 = 1 OpenAPI spec 文件，四处对齐
- **共部署 / 独立部署切换无摩擦**：物理形态变化（共 binary → 拆独立 Deployment）只改 catalog 的 `a4x.io/cicd-app-name` / `kubernetes-id` 注解，docs 文件夹层级不动
- **子主题扩展空间**：Component 内部状态机、复杂数据流图独立成 `<subtopic>.html`
- **站点导航**：自动按文件夹层级生成，比同级散落文件层次更清晰

**写什么**：单个 Component 的内部设计——接口契约、数据流、状态机、关键算法。

与 Step 2 类似，Step 4 也需要先识别Component 级的设计关注点，再通过追问收敛。

#### 4a. Component 级模式识别

| Component 特征 | 需要追问的方向 |
|---------|-------------|
| 对外暴露 API | 错误分类（哪些可重试 / 哪些不行）、幂等性、版本策略 |
| 有生命周期的实体 | 状态定义、转换 guard condition、并发转换、非法转换处理 |
| 依赖外部服务 | 超时策略、熔断阈值、降级方案、防腐层设计 |
| 高并发读写 | 锁粒度、乐观 vs 悲观、缓存一致性 |
| 复杂业务规则 | 规则是否会变？配置化 vs 硬编码？规则冲突怎么处理？ |
| 用户行为相关功能 | 需要哪些埋点事件？漏斗怎么定义？需要 A/B 实验吗？ |

#### 4b. 追问后输出

追问收敛后，产出Component 设计文档，必须包含：

- **接口契约**：不只是 API path + request/response，还需要错误分类、幂等性说明
- **状态机**（如有）：状态图 + guard condition + 并发处理策略
- **依赖方向**：本 Component依赖谁、被谁依赖，方向是否合理（稳定 Component不应依赖不稳定 Component）
- **数据流**：source → transform → sink，含错误/重试路径
- **可观测性设计**：核心指标定义（SLA）、需要新增的埋点事件（Snowplow）、A/B 实验方案（如有）、告警规则、Dashboard

#### 4c. 跨 Component 引用规则（hard rule）

**同 System 内多 Component 共部署在 1 个 binary（§4.1.1 Composite Service）时，Component 之间也只能走 interface（Port）调用，不能直接 import / package-private access 对方的 internal 实现。** 共部署 ≠ 共享代码内部 —— 这条边界由静态分析 CI 强制保证（go-arch-lint / import-linter / ArchUnit / Spotless），不是靠口头规约。

**为什么必须硬隔离**（即使在同 binary 里）：

- **拆服务零摩擦**：未来某个 Component 拆为独立 Deployment 时，只把进程内 interface 调用换成 RPC client 即可，不需要重写 caller
- **测试单 Component 可 mock**：caller 拿到的是 interface，单测时直接 mock，无需起整个 binary
- **变更影响面可推理**：动 Provider 的 internal 不影响 Consumer 编译（只要 ports 不变）
- **catalog 的 Component 边界 = 代码上的物理边界**：Backstage 上你看到的依赖图与实际 import 图一致，AI agent 推理设计影响时不会被假边界骗

**通用模式**（语言无关）：

| 角色 | 职责 |
|---|---|
| **Provider Component** | 仅暴露 `ports` 包（接口契约 + 数据 DTO），实现细节关在 `internal` 包里 |
| **Consumer Component** | 仅 import `provider/ports`；不 import `provider/internal/*` |
| **依赖装配**（main / wire / DI 容器） | 把 Provider 的 `ports.Adapter` 实现注入到 Consumer 的依赖里 |
| **静态分析 CI** | gate 任何 `consumer/* → provider/internal/*` 的引用 |

##### Go 示例（Golf 后端目前的形态）

```
server/internal/
├── players/                            # Provider Component
│   ├── ports/                          # ✅ 跨 Component 唯一可见的包
│   │   └── players.go                  # type PlayerQuerier interface { GetByID(...) (*Player, error) }
│   └── internal/                       # ❌ 跨 Component 禁止 import
│       ├── service/
│       ├── repo/
│       └── handler/
├── share/                              # Consumer Component
│   └── internal/
│       └── service/
│           └── share_service.go        # 只能 import "<root>/internal/players/ports"
└── ...
```

```go
// players/ports/players.go
package ports
type PlayerQuerier interface {
    GetByID(ctx context.Context, id PlayerID) (*Player, error)
}

// share/internal/service/share_service.go
import "<root>/internal/players/ports"  // ✅
// import "<root>/internal/players/internal/repo" ❌ go-arch-lint 拦截
type ShareService struct { players ports.PlayerQuerier }
```

CI gate：[go-arch-lint](https://github.com/fe3dback/go-arch-lint) 的 `archfile.yml` 声明 `players/internal/**` 只允许被 `players/**` 自身 import；任何外部 `players/internal/**` 引用 → CI red。

##### Python 示例（同模式，FastAPI / Django 服务）

```
src/<service>/
├── players/
│   ├── ports.py                        # class PlayerQuerier(Protocol): def get_by_id(...) -> Player: ...
│   └── _internal/                      # ❌ 跨 Component 禁止 import（下划线前缀 + import-linter 双保险）
│       ├── service.py
│       └── repo.py
├── share/
│   └── service.py                      # from src.<service>.players.ports import PlayerQuerier
└── ...
```

```python
# players/ports.py
from typing import Protocol
class PlayerQuerier(Protocol):
    def get_by_id(self, player_id: PlayerID) -> Player: ...

# share/service.py
from src.<service>.players.ports import PlayerQuerier  # ✅
# from src.<service>.players._internal.repo import PlayerRepo  ❌ import-linter 拦截

class ShareService:
    def __init__(self, players: PlayerQuerier) -> None:
        self._players = players
```

CI gate：[import-linter](https://import-linter.readthedocs.io/) 的 `.importlinter` 配置 `forbidden contract`：`src.<service>.share` 禁止依赖 `src.<service>.players._internal`。也可补 `mypy --strict` + `ruff` 的 `TID252`（禁止相对深 import）。

##### Java 示例（Spring Boot / Maven 多模块）

```
<service>-monorepo/
├── players-api/                        # Provider 的对外接口包（独立 Maven module）
│   └── src/main/java/com/golf/players/PlayerQuerier.java
├── players-impl/                       # Provider 实现（不被任何外部 module 依赖）
│   └── src/main/java/com/golf/players/internal/PlayerService.java
├── share/
│   └── src/main/java/com/golf/share/ShareService.java   // 仅依赖 players-api
└── pom.xml
```

```java
// players-api/.../PlayerQuerier.java
public interface PlayerQuerier {
    Player getById(PlayerId id);
}

// share/pom.xml — 只声明依赖 players-api，不依赖 players-impl
// <dependency><groupId>com.golf</groupId><artifactId>players-api</artifactId></dependency>

// share/.../ShareService.java
import com.golf.players.PlayerQuerier;   // ✅
// import com.golf.players.internal.PlayerService;  ❌ 编译期就 fail（Maven 不传递依赖）+ ArchUnit 兜底
```

CI gate（三层）：
- **Maven enforcer plugin**：声明 `share` 模块禁止依赖 `players-impl`（`bannedDependencies` rule）
- **[ArchUnit](https://www.archunit.org/)** 测试：`noClasses().that().resideInAPackage("..share..").should().dependOnClassesThat().resideInAPackage("..players.internal..")`
- **Spotless / Checkstyle**：禁用通配 import 防绕过

##### 共同要点

1. **Provider 自己也只能从 `ports` 包向外发布类型**（DTO / 错误类型）。如果 `internal/foo.User` 被 caller 用到，必须搬到 `ports/foo.User`，或在 `ports` 里定义独立 DTO 做边界翻译。
2. **数据库表只有 owner Component 能 SQL 直读**。需要别人数据时走 ports（in-process function call）或事件（CDC / Kafka）。详见 `service-catalog-onboarding §4.1.1` 反模式。
3. **CI gate 是必备**，不是 nice-to-have。没有静态分析的 ports 约定撑不过 3 个 sprint。
4. **跨 Component 调用文档化**：在 Consumer 的 `index.html` 列出 "依赖谁的 ports"，Provider 的 `index.html` 列出 "ports.go 暴露什么"。文档与代码 grep 一致是 reviewer 的 checklist。

**文档规范**：
- HTML 不为压行数牺牲清晰度，但必须有大纲、导航、分区和稳定锚点
- 复杂状态机 / 数据流 / 交互流程拆到 `{component}/{topic}.html`
- 层层嵌套：system overview → Component overview → Component 细节，每层只负责本层粒度
- 每个细节文档顶部引用所属 overview，形成可导航的文档树

**配套 Skill**：HTML 文档按 [`references/html-architecture-doc-writing.md`](references/html-architecture-doc-writing.md)；流程图 / dashboard / timeline / technical doc 对应参考 `visual-documentation-skills`。

---

### Step 5 — 分层测试方案（`docs/testing/`）

**写什么**：基于 Step 1（US 验收标准）+ Step 2（技术方案）+ Step 3（环境设计）生成的分层测试策略。

**文档结构**：
```
docs/testing/
├── strategy.html               # 总览：层级表、分层逻辑、Mock 架构、追溯矩阵、CI/CD
└── scenarios/                  # AC 级场景矩阵（strategy.html 不放具体用例）
    ├── ep1-<epic>.html         # 按 Epic（产品/QA 视角）
    ├── tech-<Component>.html   # 按技术 Component（开发者视角）
    └── tech-nfr.html           # NFR 降级容错
```

**strategy.html 必须包含**：
1. **10 列测试层级总览表**（层级 / 用例数 / 测试目标 / 真实依赖 / mock 依赖 / 真实 infra / mock infra / 执行时机 / 耗时 / 代码位置）
2. **分层逻辑表**（每层解决什么问题 + 为什么上层不够）
3. **Mock 基础设施 SSOT**（目录结构 + 各层 mock/真实切换策略）
4. **需求追溯矩阵**（User Story → 各层用例编号）
5. **质量门禁表**（门禁 / 检查点 / 标准）

**场景文件 AC 级追溯表**：8 列固定格式 `AC 场景 | Smoke | L1 | L2-1 | L2-2 | L3-1 | L3-2 | L4`，每格填具体用例编号。

**关键原则**：
- L3/L4 是**黑盒测试**——有 UI 的业务 vertical 通过用户界面交互；无 UI 的 technical vertical 通过 public API / package harness / HTTP route 作为黑盒入口。两者都**禁止在中间层拦截或 mock 内部组件**
- 测试场景数据通过 `make mock-scenario` 一键切换，确保可控可重复
- **跨栈一致性**：多栈项目（后端+引擎+App+数据管道共一套业务规则）应把 scenario data 抽到 `e2e/golden-data/*.json`，各栈 test runner 读同一份 fixture。一份 fixture 多栈共读 = 任一栈行为漂移立即暴露（参考 customer-care 的 `e2e/golden-data/golden_bind_fail.json`，同时被 JS engine 13 tests + Python dagster 17 tests 消费）

**质量标准**（也是 `code-review` 审查测试的评判标准，详细分层策略参见 `testing-strategy` skill）：
- **按行为风险选择最小充分验证**：新增关键用户流程、跨服务契约及安全/数据风险须有相关 L3；低风险局部变化可由 L1/L2/组件测试与必要 smoke 验证，不要求每个边界新增 L3。
- 每条已实现的 US AC 必须追溯到适当测试层级；需要 L3 时，业务 UI 用 UI/app shell，technical vertical 用 public API/harness；**L3 使用真实内部依赖**，不是 stub 内部组件。
- MR/release 的适用范围、执行阶段与旧报告复用以 [L3 分阶段门禁](../code-review/references/l3-release-gate.md) 为准：release 身份本身不触发全端或全量 L3；只验证受影响链路，未扩大风险的历史欠债单列跟踪。
- 每个已实现的 API handler 至少有 L2 集成测试（验证接口契约和数据正确性）
- 状态变更、删除、纠错等核心流程必须有边界场景覆盖
- 测试场景必须覆盖 US 的所有 AC（追溯到适当层级的用例编号，非适用层级注明原因，不要求 L2/L3 重复覆盖所有局部边界）
- **内置 Stub 是测试左移策略**：每个外部依赖有对应 Stub，L1/L2 不依赖外部服务在线；但 L3 黑盒/E2E 必须使用真实内部依赖验证完整链路

**配套 Skill**：
- `testing-strategy`（根据项目类型生成完整分层策略，含 `references/engagement-example.md` 实战范例）
- `mock-engine`（Mock 基础设施管理：L1/L2 的 Stub/Mock 服务启动、测试场景数据加载）
- `dev-infra`（测试并行安全设计 —— 多 worktree 不抢共享通道）

---

### Step 6 — CI/CD 部署文档（`docs/deployment/ci.html` + `cd.html`）

**写什么**：CI pipeline 质量门禁、CD 部署策略、K8s 资源配置、Ingress 路由、密钥管理。

**配套 Skill**：

| 任务 | Skill |
|------|-------|
| 编写/审查 `.gitlab-ci.yml` | `gitlab-ci` |
| 新应用部署全流程（CI + K8s + Ingress + Vault + Crossplane） | `cicd-developer` |
| ArgoCD GitOps 同步/回滚/排查 | `argocd` |
| 新集群搭建 CICD 组件栈 | `argocd-deploy` |
| K8s 集群资源查看/排查 | `k8s-ops` |
| 镜像仓库 Robot Account | `harbor` |
| Vault 密钥管理 | `vault-kv-manager` |
| Runner 选择和排查 | `gitlab-instance-runners` |
| 嵌入式仓库 CI Pipeline | `embed-ci-setup` |
| Jenkins 流水线管理 | `jenkins` |

---

### Step 7 — 用户手册（`docs/user-guide/`）

**写什么**：面向终端用户的操作指南。纯操作视角 — 用户能看到什么、能做什么、怎么做，不含技术实现细节。

**输入来源**：
- Step 1 (US) 的用户流程和验收标准 — 定义了用户能做什么
- Step 4 (Component 设计) 的接口信息 — 定义了操作的入口和参数

**文档结构**：
- 按功能 Component拆分文件（如 `device-setup.html`、`live-view.html`）
- 每个文件包含：功能简介、前置条件、操作步骤（含截图占位）、常见问题
- 使用统一 HTML 基础模板；操作步骤、截图占位和 FAQ 作为用户手册内容模块

**配套 Skill**：`visual-documentation-skills:technical-doc-creator` + 本 skill HTML 模板

---

## 跨文档维护规约

| 操作 | 必须同步更新的位置 |
|------|------------------|
| 新增/删除/重命名文档 | `docs/TODO.html` |
| 新增 User Story | `user-stories/index.html` 索引表 |
| 架构组件职责变更 | 对应 `architecture/**/index.html` 的职责表 / SSOT 表 |
| 新增 ADR / 改 ADR 状态 | 对应 Markdown ADR 文件 + 同目录 `adrs/README.md` + HTML 架构页的 ADR 索引表 + `docs/TODO.html` |
| 新增领域术语 | `architecture/domain-model.html` |
| 文档 + 代码一起变更 | 放在同一个 MR 里（用 `gitlab-mr` skill） |
| 新增调研结论 | `product-tech-research/index.html` 摘要表 |
| 新增 ADR | per-Component → `docs/architecture/<system>/<component>/adrs/NNNN-*.md` + 同目录 `adrs/README.md` 索引；vertical → `docs/architecture/verticals/<vertical>/adrs/NNNN-*.md` + 同目录 `adrs/README.md`；repo-wide → `docs/architecture/adrs/NNNN-*.md` + `docs/architecture/adrs/README.md`（老仓已有 `.html` ADR 或 `index.html` 可保留兼容，但新决策不再写 HTML；不批量迁移历史 ADR）；Component 的 `catalog-info.yaml` 加 `backstage.io/adr-location` 注解 |
| 新增 / 修改 vertical（树 2 视图） | `docs/architecture/verticals/<vertical>/` 下基础目录建 stub（`index.html` / `domain.html` / `app/` / `backend/` / `web/` 可选 / `contracts/` / `data/` / `flows/` / `adrs/`）；若被外部集成则补 `integration.html` / `services.html`；`index.html` 必须有 `问题 -> SSOT -> 引用方式` 表；`docs/architecture/verticals/index.html` 索引；准入决策若新增 / 变更 → 顶层 `docs/architecture/adrs/` 写一条跨 vertical 元 ADR；涉及 Component 的 `catalog-info.yaml` 同步 `golf.addx.ai/vertical` annotation（精确格式见 [`service-catalog-onboarding`](../service-catalog-onboarding/SKILL.md)）|
| 实现推进 / 验证状态变化 | 交给 `dev-workflow` 更新就近 `PROGRESS.html` 或项目进度页；architect 不把当前完成度、验证命令流水账写进设计 SSOT |

### Superpowers 生成文档的归并规则

`superpowers:writing-plans`、`superpowers:brainstorming` 等 skill 会在会话中生成设计文档或方案。这些文档**必须归并进 `docs/` 体系**，不能只存在于会话上下文或临时文件中：

| Superpowers 产物 | 归并目标 |
|----------------|---------|
| 功能方案 / brainstorm 结论 | `docs/product/user-stories/{service}.html` 对应 Epic |
| 整体技术方案 / writing-plans 输出 | `docs/architecture/index.html` 或对应 system/vertical/component overview |
| **本次需求的实现计划**（writing-plans 的主产物） | **`docs/requirements/<iid>/plan.md`** —— 见下方「实现计划」 |
| Component 实现计划 | `docs/architecture/<system>/<component>/{topic}.html` |
| 测试方案 | `docs/testing/strategy.html` 或 `services/{service}/` |
| 开发环境设计 | `docs/deployment/local-dev.html` |
| 调研结论 / product-tech-research 产物 | `docs/product-tech-research/index.html` |

**归并时机**：Superpowers 方案确认后、开始开发前，先归并文档，再开始编码。

### 实现计划 `plan.md` —— 对 `superpowers:writing-plans` 的两处覆盖

计划本身用 `superpowers:writing-plans`（或 plan mode）产出，**其余照搬**，但本规范覆盖两点：

1. **路径改成 `docs/requirements/<iid>/plan.md`**，不用 skill 默认的 `docs/plans/YYYY-MM-DD-<feature>.md`——**路径里没有 iid 就反查不到 issue**，整条自动化链断在这里。
2. **模板多一栏「需要人介入的点」**（skill 原模板没有）。

必填栏：

| 栏 | 内容 |
|---|---|
| 改哪些文件 | MR review 拿它比对 `diff(plan..HEAD)` |
| 工作顺序 · 风险 | 先后依赖、可能破坏什么 |
| proof criteria | 怎么证明做对了——**写不出来说明这活还不能开工** |
| **需要人介入的点** | 哪几处、为什么、需要谁。**非空就必须在开工前 @ 到人**，而不是做到一半卡住 |

两条硬规则：

- **`plan.md` 是 feature 分支的第一个 commit，先于任何代码提交。** commit 时序骗不了人，且在 MR pipeline 上可验证。
- **门禁点在 MR，不在分支创建时刻**（平台拦不住 push）。MR 上检查两件事：① 第一个 commit 是 `plan.md` 且早于所有代码 commit；② `diff(plan..HEAD)` 触及的文件 ⊆ plan 列出的文件，**超出部分要在 MR 描述里逐条说明**。

**计划由 agent 生成，人负责拷问**——拷问到「照着它谁都能实现」为止。

**独立完成自评**：下面任一条成立就是**不能独立完成**，必须开工前把人拉进来——需要产品判断（边界表现、文案、优先级取舍）· 需要跨端/跨团队契约 · 涉及数据迁移等不可逆操作 · 现有测试不足以证明改动正确 · plan 的假设无法在代码里验证。

> 「开发 agent 说我做不了」是**上游信号**——该回头补 spec，而不是让人下场替它手写代码。**独立完成率 ＝ spec 质量的度量。**

---

## 文档 Review（`docs/TODO.html`）

`TODO.html` 用三种状态跟踪所有待 review 条目：待做 / 进行中 / 已完成

Review 时重点检查：
- HTML 页是否有统一 shell、大纲、导航、稳定锚点和分区
- 术语是否与 `domain-model.html` 一致（业务术语 <-> 技术术语映射）
- User Story 是否混入了技术细节
- 跨文档引用路径是否正确
- US 状态是否与代码实现同步
- Product / Architecture 对 Phase 划分的理解是否对齐
- vertical `index.html` 是否有架构图、核心流程图、DDD 边界、代码结构和 `问题 -> SSOT -> 引用方式` 表
- 是否把进度状态、测试执行流水账写进了设计文档；这类内容应归 `dev-workflow` 的 `PROGRESS.html` 或项目进度页
- 无 UI technical vertical 的 L3 是否用 public API / harness 作为黑盒入口，而不是强行要求 UI
- **ADR 是否合规**：在正确的 ADR 目录、文件名是 `NNNN-*.md`、YAML front matter 五字段齐全；`/architect` 新建的正文使用 Context and Problem Statement + Considered Options〔≥2〕 + Trade-off Analysis + Decision Outcome + Consequences 标准模板。HTML 架构页有 ADR 索引表且与 `adrs/README.md` 同步；被推翻的旧 ADR 使用 `status: Superseded` + 非空 `superseded-by`（不删决策史）；`Pending` 状态必须列出需要回答的问题，不得只有 `TBD` / `待补`。对历史提升 ADR 或索引漂移的 review 严重级别按 `code-review` 判定，不反向改变本 authoring 默认值 —— 详见 [`references/adr-format.md`](references/adr-format.md)

---

## Skill 工具链速查

| 步骤 | 任务 | 使用 Skill |
|------|------|-----------|
| Step 0 | Requirements 分析、公司 Skill review、产出 REQUIREMENTS 或 ATTEMPT_NEEDS_INPUT | `requirements-analysis-agent`（按条件调用 `product-tech-research` 等 review Skill） |
| Step 0 Gate | 请求并验证 PO 对 exact Artifact ID/hash 的独立决策 | Coordinator + 独立 PO |
| Step 1 | 从 accepted REQUIREMENTS 派生 User Story | `story-craftsman` |
| Step 2 | 查公司现成服务&能力（调谁/约束/怎么接） | `service-catalog-search` |
| Step 2 | 架构设计：方案发散 | `superpowers:brainstorming` |
| Step 2 | 架构设计：HTML 文档 | `architect` + `references/html-architecture-doc-writing.md` + `visual-documentation-skills:architecture-diagram-creator` / `technical-doc-creator` / `flowchart-creator` / `dashboard-creator` / `timeline-creator` |
| Step 3 | 设计开发/测试环境分层 | `dev-infra` |
| Step 3 / Step 5 | 本地观测契约 | `prom-grafana-dev` |
| Step 4 | 分 Component细节设计 | HTML 页面用本 skill；流程 / dashboard / timeline 按 visual-documentation-skills 选模块 |
| Step 5 | 生成分层测试方案 | `testing-strategy` + `dev-infra` |
| Step 6 | CI Pipeline 配置 | `gitlab-ci` / `embed-ci-setup` / `jenkins` |
| Step 6 | CD 部署全流程 | `cicd-developer`   |
| Step 6 | K8s / 镜像 / 密钥 | `k8s-ops` +  `vault-kv-manager` |
| 全程 | 埋点事件定义与管理 | `tracker-manager` |
| 全程 | A/B 实验 / Feature Flag | `growthbook` |
| 全程 | SLA 指标配置 | `sla-metric` |
| 全程 | 监控 Dashboard / 告警 | `grafana` + `prometheus` |
| 全程 | 错误监控 | `sentry` + `sentry-onboarding` |
| 全程 | 文档 + 代码一起提 MR | `gitlab-mr` |
| 全程 | 研发流程编排 | `dev-workflow` |
| 全程 | 创建或更新 Skill 本体 | `superpowers:writing-skills` |
| Step 7 | 用户手册 | `doc-writing` |

---

## Memory（在 architect skill 范围内何时写 / 不写）

> auto memory 通用规则（4 类：`user` / `feedback` / `project` / `reference` + 写入 / 回查 / 不写边界）在 [Claude Code 的全局 memory 规约](https://docs.claude.com/en/docs/claude-code/memory)；本节只列本 skill 特有的触发点。

### 何时写 memory

| 触发场景 | 类型 | 写什么（含 Why / How to apply） |
|---|---|---|
| §2b 苏格拉底追问中用户给出**非显然偏好**或反驳一个常见做法（"我不要 Outbox"、"先 SSE 不上 WebSocket"） | `feedback` | 偏好 + Why（用户给的理由）+ How to apply（下次遇到同类场景怎么处理） |
| §2d ADR 收敛后选 A 不选 B 的**深层项目约束**（团队规模 / 合规 / 历史包袱），ADR 正文不便展开的 | `project` | 决策 + 约束 + How to apply（后续同类决策默认按这个倾斜） |
| §2.0 catalog 拓扑确认时浮出的**该 Domain / System 的边界共识**（"为什么 admin 单独 System 不并入 backend"） | `project` | 决策事实 + Why + How to apply |
| §2a+ 调 `service-catalog-search` 拿到的**关键外部资源 / 中台位置**（"push 走 notificationPublisher"、"埋点走 tracker-manager"） | `reference` | 资源名 + 用途 + 接入入口 |
| §4 Component 设计追问中暴露的**hard rule**（"这个 Component 永远不直读 X 表"、"该 ports 不抛 panic，错误码统一 errPlatform"）| `feedback` | rule + Why + How to apply |

### 不要写

- **ADR 正文** —— 已在 `docs/architecture/<system>/<component>/adrs/NNNN-*.md`，memory 不复制
- **catalog 拓扑命名**（Domain / System / Component 名）—— `catalog-info.yaml` 是 SSOT，grep 一下就有
- **当前会话的中间思考 / 候选方案对比表** —— ADR 的 Considered Options + Trade-off Analysis 节就是这个，不该再单独存 memory
- **本次任务的临时状态**（"今天写了 overview.html"、"还差 §4 的状态机"）—— 用 TaskCreate / `docs/TODO.html`，不是 memory
- **能 grep / read 的事实**（栈、路径、表名）—— 文件就在那里

### 何时回查 memory

- **进入 §2.0 catalog 拓扑前** —— 先 grep memory：本仓 / 本 Domain 是否已经定过相关决策（避免推翻已生效 ADR / 重复追问已答过的边界）
- **进入 §2b 苏格拉底追问前** —— 看用户对类似关注点（如"事件投递"、"跨服务一致性"）是否已有 standing `feedback`，避免重复让用户表态
- **§2a+ 查能力前** —— 若 memory 已有"X 能力调 Y 服务"的 reference 条目，先验证仍然有效再用（catalog 是动态的，memory 可能过期）

> **memory ≠ ADR 的替代**。ADR 是项目级决策 SSOT；memory 是用户偏好 / 暂未 ADR 化的项目上下文。查 memory 的同时也要读相关 ADR（架构变更前必读 ADR 在 CLAUDE.md 是 hard rule，详见 [§ 代码一级目录 CLAUDE.md 模板](#代码一级目录-claudemd-模板)）。

---

## 公司规范正本（本仓 `public/dev-standards/architecture/`）

本仓 `public/dev-standards/architecture/` 是公司工程架构规范的 **SSOT 正本**（2026-08-11 从 `engineering/architecture` 仓整树迁入，自包含互链 HTML 站，人读版发布在 [Pages](https://pages.addx.ai/engineering/skills/dev-standards/architecture/)）。文档先于 skill：规范正本住在发布树 `public/dev-standards/<域>/`（约束产出物，MR 里能判违规），工作方法住 `public/work-methods/`（约束工作方式，维护于发布分支 `docs/skill-hub`），skill 只承载 AI 面。

- 入口 [`index.html`](../../public/dev-standards/architecture/index.html)；分层 / 命名 / GitLab 规范：`service-layering.html` / `embedded-layering.html` / `app-layering.html` / `naming-standard.html` / `gitlab-standard.html`
- 服务清单与调用规则：`backend-service-architecture.html`（`microservice-integrate` 的查询正本）
- catalog 建模 / schema / 术语 / 逐仓归位：`catalog-*.html`（`service-catalog-onboarding` 的规范正本）
- 公司级 ADR 推理链：`adrs/`；GitLab org 现状快照与全量树：`gitlab-org-*`；架构图设计方法：`system-architecture-diagram-design.html`

**SSOT 纪律**：`public/dev-standards/architecture/` = 正本（人读，讲 why）；`references/` 里的 AI 简洁摘要为待补批次 —— 改规范先改正本再同步摘要；改层名 / 服务名 / Domain 名 = 改正本 → grep 下游同步引用。

---

## 方法论参考（`references/`）

Step 2/4 的追问和方案评估可参考以下方法论决策卡。每张卡只回答三个问题：什么时候用、核心概念速查、什么时候不用。

**AI agent 对这些方法论本身已有足够知识，决策卡的价值是帮助判断"当前场景该不该用"。**

| 决策卡 | 适用信号 |
|-------|---------|
| `references/ddd.md` | 业务术语混乱、服务边界不清、多团队协作 |
| `references/event-driven.md` | 异步处理、跨服务协调、状态变更通知 |
| `references/hexagonal.md` | 多个外部集成、需要可测试性、防止框架锁定 |
| `references/state-machine.md` | 有生命周期的实体、状态转换规则复杂 |
| `references/integration-patterns.md` | 第三方 API 对接、外部系统不可靠 |
| `references/multi-tenant.md` | OEM / 白标 / 多租户场景 |
| `references/adr-format.md` | **写 ADR 时必看**：机器友好的 Markdown ADR 模板、按影响范围落到正确 ADR 目录、状态生命周期、索引、Backstage ADR 插件衔接 |
| `references/html-architecture-doc-writing.md` | **写 / 重构 HTML 文档时必看**：统一模板、页面契约、SVG 图语言、visual-documentation-skills 全量映射、ADR 链接和校验 checklist |
| `references/vertical-doc-writing.md` | **写 / 重构 vertical 文档时必看**：`index.html`、`domain.html`、`integration.html`、`services.html`、`flows/` 的职责、模板、防 drift checklist |

---

## 示例对比

### ❌ Bad — 绕过 Requirements Gate 直接设计

```
产品经理说"做一个 XX 功能" → 直接写 User Story → 架构设计
→ 没有 canonical source、公司 Skill review、不可变 Artifact 或 PO exact-hash ACCEPT
→ 需求不清时仍可能生成项目目标态和 ADR
```

### ✅ Good — 先完成 Requirements Analysis，再按条件研究

```
产品经理说"做一个 XX 功能"
→ Step 0: requirements-analysis-agent 固定来源并运行公司 Skill review
→ 命中竞品/开源/新兴领域条件时，调用 product-tech-research；否则记录 SKIPPED 理由
→ PO 对精确 Artifact ID/hash ACCEPT
→ Step 1: 从 accepted REQUIREMENTS 派生 US，不改变已接受需求
```

---

### ❌ Bad — 读完 US 直接画架构图

```
读完 User Story → 直接写 index.html 画 Mermaid 图
→ 没有追问，服务边界是凭感觉划的，ADR 没有推理链
```

### ✅ Good — 模式识别 → 追问 → brainstorming → 收敛

```
读完 US → 识别到"多租户 + 设备 OTA"信号
→ 追问：租户间数据隔离级别？设备离线时 OTA 补推策略？
→ brainstorming 发散 3 个方案
→ 参考 DDD + 多租户决策卡评估
→ 选定方案，写 Markdown ADR（含完整推理链）
→ 输出 HTML 架构页 + domain-model.html + Markdown ADR
```

---

### ❌ Bad — User Story 混入技术细节

```markdown
## US-GW-01 消息路由
用户发送消息后，Gateway 通过 IntentResolveMiddleware 调用
`resolve_intent()` 方法，查询 PostgreSQL 的 workspace 表获取 agent_id，
再通过 Redis Pub/Sub 分发到对应 Sandbox Pod。
```

问题：AC 应该描述用户看到的结果，不应包含类名、数据库、中间件。

### ✅ Good — User Story 纯用户视角

```markdown
## US-GW-01 消息路由
**背景**：用户需要在不同群聊中维护独立的 Agent 上下文。

**用户故事**：作为团队成员，我希望在不同飞书群发消息时，
Agent 能分别记住各群的上下文，互不干扰。

**AC**：
- Given 用户在群 A 和群 B 分别对话过
- When 用户在群 A 提问
- Then Agent 只基于群 A 的历史回复，不混入群 B 的内容
```

---

### ❌ Bad — 架构正文缺少统一阅读体验，且把多条 ADR 聚合成一页

```
docs/architecture/gateway.md
docs/architecture/gateway-adrs.md
docs/testing/strategy.md
```

### ✅ Good — 同一 HTML shell + 类型内容模块

```
docs/architecture/gateway/
├── index.html             （cockpit，架构图 + 阅读路径 + 子文档导航）
├── intent-resolve.html    （flowchart 模块）
├── thread-lane.html       （technical doc 模块）
├── testing.html           （dashboard / matrix 模块）
└── adrs/
    ├── README.md
    └── 0001-routing-boundary.md
```

---

### ❌ Bad — Superpowers 方案停留在会话里

```
brainstorming 完成后直接说"好，开始写代码"
→ 方案只在会话上下文，下次会话丢失，无法作为测试依据
```

### ✅ Good — 方案先归并进 docs/，再开始编码

```
brainstorming 完成 → 写入 docs/architecture/index.html + ADR/domain/test SSOT → commit → 开始编码
```

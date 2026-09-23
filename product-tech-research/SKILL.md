---
name: product-tech-research
description: AI Agent 驱动的产品技术调研。调研竞品产品体验、开源方案、技术趋势、用户反馈、合规标准，输出结构化报告到 docs/product-tech-research/。两种产出模式：默认 md（喂给 architect/US 的内部输入）；深度分享模式产出自包含单文件 HTML 报告（图表/inline引用/悬浮折叠目录，配合 gitlab-pages-html 发布成可分享链接）。当用户提到"竞品分析"、"行业调研"、"开源方案调研"、"技术选型调研"、"product research"、"competitive analysis"、"看看别人怎么做的"、"出一份调研报告/分析文档（要分享/发布）"时触发。
---

# Product Tech Research

AI Agent 驱动的产品技术调研编排器 — 从产品体验和研发技术两个视角，系统性调研行业竞品、开源方案、技术趋势、用户反馈和合规标准。

**核心理念**：在写 User Story 之前先看看外面的世界。调研结论是架构设计的输入，不是附录。

## Rules

1. **调研先于设计**：本 skill 的产出是 architect Step 1 (US) 的前置输入
2. **追问明确范围**：不盲目搜索，先通过追问收敛调研方向
3. **产品 + 技术双视角**：每个竞品/方案同时评估产品体验和技术实现
4. **结论导向**：每个维度的调研必须产出"对我们的启示"，不是信息堆砌
5. **单文件 ≤ 600 行**：沿用 architect 文档规范
6. **Requirements review-only 优先**：被 `requirements-analysis-agent` 以 `review_only=true` 调用时，必须执行下方专用模式；不得落入默认访谈、并行调研或文档产出流程。
7. **Standalone research boundary**：Only standalone knowledge research that does not define a project-specific feature, behavior, or target state may enter the default workflow directly. A request to research a project-specific feature, behavior, or target state must route to `requirements-analysis-agent` first and participate there as `review_only=true`.

---

## Requirements review-only mode

当调用方是 `requirements-analysis-agent` 或声明 `review_only=true`：

- 只使用当前 Attempt 已提供的 canonical source、允许的只读检索结果和可引用证据，返回 `verdict: PASS|CONDITIONAL|BLOCKED`、稳定 finding ID、evidence refs、research questions 与 evidence gaps。
- 不得写入 `docs/product-tech-research/` 或任何其他文件，不得发布页面，不得修改 source。
- 不得启动并行 subagent；所有审查保持在同一 Requirements Attempt 内，避免产生不可绑定的旁路 session 和产物。
- 不执行“一次一题”访谈。把所有阻断信息一次性合并返回 Requirements Agent，由上游与其他 Skill findings 统一形成问题集。
- 当前事实不足时返回 `CONDITIONAL` 或 `BLOCKED`，不得把待研究的问题冒充事实，也不得自行启动设计、US 编写或技术选型。

Only standalone knowledge research, or a separate research-document action authorized after PO acceptance, may enter the default workflow below. A direct request is not by itself evidence that a project-specific target state passed the Requirements gate.

---

## 执行流程

### Step 0a: 明确调研范围（追问）

通过苏格拉底式追问收敛调研方向，每次只问一个问题，优先提供多选项：

| 维度 | 目的 | 示例问题 |
|------|------|---------|
| **产品定位** | 确定调研关键词 | "你要做的产品/功能用一句话描述是什么？" |
| **已知竞品** | 避免遗漏 | "你已经知道哪些竞品？A) 列举已知的 B) 完全不了解" |
| **调研重点** | 聚焦方向 | "更关注哪个方面？A) 产品体验 B) 技术实现 C) 都要" |
| **技术约束** | 缩小技术选型范围 | "有技术栈偏好吗？A) 有（说明）B) 没有限制" |
| **合规要求** | 确定合规维度 | "有特定的合规/认证要求吗？A) 有（说明）B) 暂无" |

访谈原则：
- **一次一题**：不要同时问多个问题
- **多选优先**：能给选项就给选项
- **及时停止**：信息足够就停止，通常 2-4 个问题足矣

### Step 0b: 并行调研 5 个维度

明确范围后，使用 `superpowers:dispatching-parallel-agents` 并行执行以下调研：

| 维度 | 调研内容 | 工具 | 产出文件 |
|------|---------|------|---------|
| **竞品分析** | 商业竞品的功能对比、定价模式、产品体验优劣势、技术架构（如可知） | WebSearch | `competitors.md` |
| **开源项目** | 可复用的开源方案、GitHub star/活跃度、许可证、技术栈、成熟度评估 | WebSearch | `open-source.md` |
| **技术趋势** | 相关领域的技术演进方向、行业最佳实践、技术会议/博客动态 | WebSearch | `tech-trends.md` |
| **用户反馈** | 竞品用户的痛点、App Store/社区评价、需求缺口 | voc-analysis | `user-feedback.md` |
| **合规标准** | 行业法规、认证要求、数据隐私规范、安全标准 | WebSearch | `compliance.md` |

每个维度的调研文档必须包含：
1. 调研方法（搜了什么、看了哪些来源）
2. 事实发现（客观描述）
3. **对我们的启示**（主观判断，HWPR 标记）

### Step 0c: 整合产出

汇总 5 个维度的调研结论，生成 `overview.md`：

**overview.md 必须包含**：

1. **调研背景与范围** — 做什么、为什么做、调研了哪些方向
2. **关键发现摘要表**：

| 维度 | 关键发现 | 对我们的启示 |
|------|---------|-------------|
| 竞品分析 | ... | ... |
| 开源项目 | ... | ... |
| 技术趋势 | ... | ... |
| 用户反馈 | ... | ... |
| 合规标准 | ... | ... |

3. **子文档导航表**
4. **"对我们的启示"综合章节** — 这是给 architect Step 1 (US) 的直接输入

### 产出目录

```
docs/product-tech-research/
├── overview.md              # 调研总览（≤600 行）
├── competitors.md           # 竞品分析
├── open-source.md           # 开源方案
├── tech-trends.md           # 技术趋势
├── user-feedback.md         # 用户反馈/VOC
└── compliance.md            # 合规标准
```

---

## 文档规范

沿用 architect 文档规范：
- **单文件 ≤ 600 行** — 超过则拆分
- **术语对齐** — 如项目已有 `domain-model.md`，使用一致的术语
- **Mermaid 换行** — 用 `<br/>`，禁止 `\n`

---

## 产出模式二：单文件 HTML 深度报告（要分享/发布时用）

当调研结果**要给老板/团队/外部传阅**（发布到 GitLab Pages 得到链接），不走 md，产出**自包含单文件 HTML**（零外部依赖：无 CDN/外链，图全部内联 SVG/CSS）。

**起步**：复制 [`templates/report-template.html`](templates/report-template.html)；同项目第 2 份起，先 `Read` 既有报告前 ~420 行复用 CSS，保持系列视觉一致。

**核心要点**（完整方法见 [`references/html-report-method.md`](references/html-report-method.md)）：

| 要点 | 规则 |
|------|------|
| **导航** | 悬浮折叠 TOC（左上角固定按钮 + CSS counter 自动编号 + 点击自动收起），正文吃满 1440px——不用侧边栏占列 |
| **图表选型** | 时间序列**必须**用时间轴柱状图/时间线（❌ 禁止横向条形图）；分类对比才用 hbars；占比用 SVG donut；流程/架构用内联 SVG 框图；KPI 用 statband 卡带 |
| **引用** | 关键论断处 `a.ref` 角标直链来源（角标文案=来源名，免编号维护）+ 文末按主题分组的来源清单；置信度 pill（高/中/低）；unknown 如实标注不编造 |
| **结构** | hero（含读者/方法 meta）→ TL;DR + statband + verdict callout → 正文 → 结论与行动清单 → 来源；来源质量警告/方法局限单独开 danger callout |
| **宽表格** | `table.matrix` + colgroup 固定列宽 + 冻结首列（横滚时产品名不消失） |
| **校验** | 交付前必跑 html.parser 标签配对校验（void 名单**必须含 `col`**），脚本见 references |

**发布**：用 `gitlab-pages-html` skill（docs 分支 + private access）；部署成功以 pipeline success + pages API deployment 时间戳为准。

**大报告生产模式**：并行调研 agents（每维度一个，要求结构化要点+来源 URL+unknown 不编造）→ 写作 agent（喂浓缩材料+本规范）→ 主会话复核校验、发布；用户的口径修正由主会话直接 Edit，不重跑 agent。

---

## 与其他 Skill 的关系

- **architect**：本 skill 是 architect Step 0 的执行者，产出是 Step 1 (US) 的输入；`docs/architecture/**` 站点文档的导航遵循 architect 的 `.doc-outline` 体系，本 skill 的悬浮折叠 TOC 用于 standalone 调研报告
- **gitlab-pages-html**：HTML 深度报告的发布通道（docs 分支 + private Pages）
- **voc-analysis**：本 skill 在用户反馈维度调用 voc-analysis
- **story-craftsman**：本 skill 的调研结论帮助 story-craftsman 写出更有依据的 US

---

## Examples

### ❌ Bad — 没有调研直接写 US

```
产品经理说"做一个 XX 功能" → 直接写 User Story → 架构设计
→ 不知道竞品怎么做的，不知道有没有开源方案可以用
→ 重复造轮子，或者设计出已被市场验证失败的方案
```

### ✅ Good — Requirements 内先调研评审，再设计

```
产品经理说"做一个 XX 功能"
→ requirements-analysis-agent 选择 product-tech-research review_only
→ 一次性返回竞品、开源方案和证据缺口，不写文件、不启动旁路 session
→ Requirements Agent 汇总 findings，形成 Artifact 并由独立 PO 接受 exact hash
→ PO 接受后，如需完整分享材料，再单独授权产出 overview.md
→ 后续 US/架构设计只消费已接受 Artifact 与可追溯研究证据
```

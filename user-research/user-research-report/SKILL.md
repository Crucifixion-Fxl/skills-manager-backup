---
name: user-research-report
description: 生成、审查或重写量化问卷、定性访谈和已编码开放题的研究报告；适用于用户研究 Quick Findings、阶段性快报、最终 findings、决策支持报告、建议备忘录及其证据附录。接收已结构化数据或编码包，不负责原始文本编码、招募、投放或数据采集。
---

# user-research-report

## Description

将已结构化的定量结果和已编码定性材料写成易读、可追溯且不替负责人做决定的研究报告。

## Rules

## 能力边界

适用于：

- 量化问卷、访谈结果、已编码开放题和多阶段研究报告；
- 把研究发现整理成可读的决策支持材料；
- 审查报告的统计口径、范围覆盖、中立性和证据可追溯性。

不负责：

- 招募、投放和数据采集；需要时调用相应 acquisition / monitor skill；
- 代替数据分析工具编写项目专用 SQL 或 Python；
- 直接对原始研究文本建立 codebook 或完成主题编码；需要时使用 `user-research-coding`；
- 未经请求者授权替业务负责人选择方案或发布报告。

## 先选择用途、阶段和深度

报告由三个彼此独立的维度决定，不把 Quick Findings 当作第四种用途模式：

### 用途

- `findings_report`：默认。呈现发现、证据和边界，不给方案偏好。
- `decision_support_report`：按待决问题组织，说明证据缩小了什么、仍不能决定什么，不替负责人选择。
- `recommendation_memo`：仅在用户明确要求建议、方案或优先级时使用；研究事实与业务判断必须分开。

### 阶段

- `interim`：数据仍在收集、样本或问卷版本尚未冻结；所有结果均为阶段性快照。
- `final`：数据收集已关闭或明确冻结，可以形成最终读数。

### 深度

- `quick`：面向快速同步，只保留当前最重要的答案、质量状态和边界，并链接完整证据或数据快照。
- `full`：正式研究记录，覆盖全部研究目标、方法、完整证据和审计信息。

用户明确指定时服从其要求；否则按以下规则推断：

- 问卷仍在发放，或用户询问“目前结果、阶段发现、中期快报、周报/迭代同步”时，默认 `interim + quick + findings_report`。
- 数据已关闭或冻结，且用户要求正式、完整、归档或可审计报告时，使用 `final + full`。
- 用户只要求简版、管理层摘要或一页结论时使用 `quick`；Quick Findings 既可以是阶段性的，也可以是最终的。
- 数据未收齐时不得默认生成最终建议；只有用户明确要求临时方案输入时，才可输出标为 provisional 的 `recommendation_memo`。

Quick Findings 的触发、最低内容、滚动查看风险和问卷版本处理，完整遵循 [references/interim-and-quick-reporting.md](references/interim-and-quick-reporting.md)。

使用后两种模式时，完整读取 [references/decision-neutral-reporting.md](references/decision-neutral-reporting.md)。

`decision_support_report` 默认采用“主报告＋证据层”：主报告服务快速理解，证据层保留原始题干、完整分布、方法、低增量分析和审计记录。两者共同构成正式交付，不把仅有主报告视为完整研究记录。

内部报告、方法说明和评审默认使用中文。受访者实际看到的其他语言题干、选项和用户原话保留原文，可附中文说明，不用译文替代原始证据。

## 输入要求

最低需要已清洗、已结构化的数据：

- `metadata`：总样本、数据快照、剔除规则；
- `questions[]`：题目标识、受访者实际看到的措辞、题型和响应分布；
- `respondent_level_ref` 或分群字段、`qualitative` / `coding-package.json`、`quality`：存在且分析需要时提供。

阶段性报告还应尽量提供：收集状态、当前/目标样本量、截止时间、问卷版本及上一次可比快照；缺失时明确写未知，不把阶段性读数包装成最终结论。

详细格式见 [references/input-contract.md](references/input-contract.md)。缺少非核心字段时记录限制并继续；只有数据整体不可读、有效样本无法确定，或核心研究目标完全没有可用证据时，才暂停整份报告并向研究负责人或请求者确认。

## 工作流（6 步）

1. **范围与形态登记**：从研究 brief、请求者要求和研究工具建立“目标 → 题目/证据 → 报告答案”清单；选择用途、阶段和深度，记录负责人明确给出的优先级。没有独立 brief 时，明确说明目标由研究工具还原。
2. **快照冻结**：无论阶段性还是最终报告，都锁定本次样本、截止时间、问卷版本、剔除和缺失口径；阶段性冻结只代表可复现快照，不代表停止收集。
3. **基础分析**：先完成每个范围内目标的直接回答；题目级证据保留原始措辞、人数、分母和完整分布。Top/Bottom 等原始选择按题目设计正常报告；Net、综合排名、指数和分档仅在计算逻辑与研究问题匹配时使用，事后新增者标为探索性并保留关键原始组成。
4. **必要的跨题分析**：只有结论涉及共同出现、分群差异、路径或机制时才检查联合分布；拒绝全量碰运气交叉。
5. **结论与范围评审**：检查混淆变量、信息增量、实际意义、中立性和每个原始目标的可见度。
6. **写作与校验**：按用途、阶段和深度写作，放置证据边界，验证主报告—证据层的一致性并记录版本。

## 阻断与降级

默认只阻断**受影响的结论或证据块**，继续完成其余可回答内容；不要因为局部问题停止整份报告。

以下情况不得保留相关结论，修正后才能恢复：

- 样本、覆盖或精度不足以支持该结论强度，且无法通过缩小表述范围解决；
- 缺少该题的原始措辞，无法判断测量含义；
- 分析方法与测量尺度或研究设计不匹配，或多重比较策略与用途不匹配；
- 把边际比例拼成“同一批人”、用户路径或机制，但没有受访者级联合分布；
- 因果、失败归因或细分差异存在同样合理的替代解释而未披露；
- 用户原话无法追溯、未检查题目上下文或未去标识化；
- 派生阈值、合并口径或分析者推断被写成原始事实。

处理顺序：修正方法 → 降级为方向性线索或未知 → 删除不可靠结论。只有问题会使核心交付整体失效且无法从现有材料解决时，才向研究负责人或请求者提问。

## 不可违反的报告原则

### 证据与统计

- 百分比同时给人数和分母；有序量表、多个比较、交叉分析和样本量规则以 [references/statistical-defaults.md](references/statistical-defaults.md) 为唯一权威定义。
- 原始 Top/Bottom、评分与排名结果按研究设计正常报告；Net、综合排名、指数、分档等派生指标必须说明计算逻辑和用途。主要指标尽量事前定义，事后合理探索可以保留但必须标为探索性，并展示足以识别分化的原始结果。
- 观察性研究不写因果结论；混淆变量无法区分时直接写“无法区分”。
- 统计显著、计算复杂或已经完成分析，都不是进入主文的理由。派生分析按 `decision-neutral-reporting.md` 的晋级门槛分层。

### 范围与结构

- 每个来自 brief、请求者要求或研究工具明确测量的目标，都必须在主报告有一个可扫描的答案或状态；“没有明显胜出项”“数据不足”“题目无效”也是答案。
- 核心摘要只保留最重要、证据足够的少量发现；数量由研究范围与证据决定。未经负责人明确确认，不把范围内目标静默降到附录或合并成不可单独引用的“其他”。
- 主文按待决问题组织；证据层逐题保留原始措辞、完整分布、派生口径和方法。结构细则以 [references/structure-and-scope.md](references/structure-and-scope.md) 为准。

### 写作与中立性

- 标题直接描述数据，不写宣传口号、未测量的先后关系或伪装成发现的建议。
- 中立不等于逐题平铺。核心章节优先使用“直接回答 → 关键差异 → 对待决问题的影响 → 尚缺证据”。
- 会改变或推翻结论的限制紧邻正文；只限定用途的限制放“证据边界”；方法细节进入附录。不能用小字隐藏关键风险。
- 事实、分析解释和建议明确分层；表达与术语规则见 [references/writing-rules.md](references/writing-rules.md)。

### 原话、开放题与隐私

- 原话逐字、可追溯、去标识化；开放题主题给正确分母，并提供去标识化原文或可访问的原文索引。
- 详细规则以 [references/quote-and-openended.md](references/quote-and-openended.md) 为准。

### 产品与阶段边界

- 报告中的产品能力必须与可用产品材料核对；已上线能力与概念能力分开。
- 没问的问题不假装回答；当前阶段不能支持的决策明确列出。

## 交付与确定性校验

完整报告使用 [templates/report-skeleton.md](templates/report-skeleton.md)；Quick Findings 使用 [templates/quick-findings-skeleton.md](templates/quick-findings-skeleton.md)；单题证据需要独立展开时使用 [templates/per-question-block.md](templates/per-question-block.md)。

对于“主报告＋独立证据附录”的正式交付：

1. 复制并填写 [templates/report-package-manifest.json](templates/report-package-manifest.json)；
2. 运行：

```bash
python3 scripts/validate_report_package.py \
  --main /absolute/path/main-report.md \
  --evidence /absolute/path/evidence-appendix.md \
  --manifest /absolute/path/report-package-manifest.json
```

校验器检查版本一致、双向链接、相对文件链接、研究目标标记和关键共享事实。普通单文件草稿可以不创建 manifest；正式两层交付不得跳过基础校验。细节见 [references/report-package-validation.md](references/report-package-validation.md)。

提交或发布本 Skill 前运行离线行为自测：

```bash
python3 scripts/self_test.py
```

## 评审与版本

- 完成后按 [references/review-workflow.md](references/review-workflow.md) 做方法、数据与阅读者视角评审。
- 报告已经共享、评审、用于决策或正式发布后，重大结论撤回、数据快照更新或方法变化使用 [templates/changelog-template.md](templates/changelog-template.md) 留痕；未共享草稿不强制维护变更记录。
- 评审意见分为事实/方法错误、范围或目标变化、表达偏好；前两类按证据修正，偏好类不自动覆盖研究原则。

## 资源导航

| 需要解决的问题 | 权威文件 |
|---|---|
| 输入数据格式 | [references/input-contract.md](references/input-contract.md) |
| 统计方法、样本量、交叉分析 | [references/statistical-defaults.md](references/statistical-defaults.md) |
| 报告结构、产品与阶段边界 | [references/structure-and-scope.md](references/structure-and-scope.md) |
| 决策支持、中立性、证据分层、结论晋级 | [references/decision-neutral-reporting.md](references/decision-neutral-reporting.md) |
| 阶段性报告与 Quick Findings 的触发和边界 | [references/interim-and-quick-reporting.md](references/interim-and-quick-reporting.md) |
| 写作和术语 | [references/writing-rules.md](references/writing-rules.md) |
| 原话、开放题、表格与隐私 | [references/quote-and-openended.md](references/quote-and-openended.md) |
| 评审与版本协作 | [references/review-workflow.md](references/review-workflow.md) |
| 两层报告一致性校验 | [references/report-package-validation.md](references/report-package-validation.md) |

## Examples

### Bad

“A 组更喜欢功能 X，所以应立即上线。”——把小样本差异、解释和业务决定混成一个结论。

### Good

“本次样本中 A 组选择功能 X 的比例更高；样本量和分群差异不足以证明普遍偏好。该结果支持把 X 纳入下一轮验证，但上线决定仍需结合成本与行为数据。”

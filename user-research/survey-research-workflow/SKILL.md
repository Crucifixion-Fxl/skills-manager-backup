---
name: survey-research-workflow
description: >
  规划、设计、评审和执行问卷研究。用于把产品决策转成可测量问题、生成或审查问卷、比较版本、制定 sampling 与分析计划，以及在明确要求时交接到 Google Forms、SurveyMonkey 或 Typeform。研究方法尚未确定时先使用 user-research。
---

# Survey Research Workflow

## Description

以决策质量和受访者体验为核心，选择足以完成当前任务的最轻流程。不要把生产发布流程施加到局部改题，也不要把平台限制或单个项目经验写成通用研究原则。

## Rules

## 1. 先选模式

读取 `references/workflow-modes-and-deliverables.md`：

- `quick_review`：审查一道题、选项、局部模块或两个版本。直接给判断、影响和改写，不创建项目文件。
- `survey_design`：从研究范围生成或系统修订完整问卷。默认输出业务覆盖摘要、干净问卷、主要分支与负担、必要实施说明和方法附录。
- `production_workflow`：用户明确要求建卷、修改线上草稿、投放、回收、监测或报告时使用。此时才启用稳定 ID、平台 preflight、read-back、风险分级测试和外部操作确认。

未明确要求线上执行时，不得自动升级到 `production_workflow`。

## 2. 通用研究内核

完整设计由 `agents/survey-designer.md` 执行，独立评审使用 `agents/research-reviewer.md`。按任务读取以下通用参考：

- 范围、最重要问题、构念、estimand、证据强度、概念测试和版本覆盖：`references/research-scope-and-measurement.md`
- 问卷主线、顺序、第一题、逐题负担、题型、措辞、选项、随机化和去重：`references/questionnaire-design-and-burden.md`
- 受众适配交付、选项来源、合成预测试、审查严重度和风险分级：`references/research-assurance-and-delivery.md`
- Sampling、分析口径、soft launch、数据质量和 live versioning：`references/survey-fieldwork-and-analysis.md`
- 正式答卷的数据治理、隐私、翻译与可访问性：`references/research-data-ethics-and-inclusion.md`

仅在需要视觉刺激时读取 `references/visual-stimulus-and-randomization.md`；仅在发布前需要认知或真实路径走查时读取 `references/agent-pretest-and-path-walkthrough.md`。

所有模式遵守以下原则：

1. 先明确 owner 要支持的决定和原始关注点，再写题。显式目标被删除、延后或实质弱化时必须说明，不能用相邻构念声称完整覆盖。
2. 每个核心问题先定义 population、reference period、estimand、回答依据、结果用途和不能推出的结论。单选主导项份额不能解释为多个可共存需求各自的 incidence。
3. 优先遥测、直接观察、近期具体行为和当前体验；一般态度和未来意向只能按其真实证据强度解释。概念测试先测问题、现状和替代行为，再用中性概念、取舍或任务。
4. 受访者必须拥有回答所需经验或可见信息。避免 leading、loaded、double-barrelled、hidden assumption、forced answer、过度回忆和前题提示污染。
5. 把负担当作受 validity 与 actionability 约束的优化目标：同时看理解、检索、判断、选项映射、交互、敏感压力、实际展示比例和框架切换。第一题要短、易扫读、低敏感，并自然进入主线。
6. 选项回答同一维度、层级一致、尽可能互斥且覆盖合理。原因题区分机制、表现、情境与稳定偏好；需要多层信息时优先渐进诊断。`Other` 不修补明显缺口。
7. 每道封闭题记录顺序策略：有序尺度按自然顺序；无序实质选项通常随机或轮换；`None / Other / Don't know / Not applicable / Prefer not to answer` 固定在末尾。平台不能安全实现时，可选择固定顺序并说明限制，不应为微小顺序风险增加大量作答负担。
8. 去重看受访者实际任务。后一题只有在提供前题、routing、遥测或其他证据无法推导且会改变决策的信息时保留。
9. 范围过大时比较单卷、routing、split sample、分 wave 和其他方法，但只能建议，不能未经 owner 确认擅自拆分或删除明确目标。
10. 源材料不足以支持核心原因选项、用户语言、竞品/市场背景或概念边界，且这些信息会改变设计时，先使用已有研究、VOC 或权威桌面调研补证。记录来源和适用性；外部资料中的出现频率不能解释为自家用户比例。较宽的行业/竞品研究路由到 `user-research` 或可用专项 skill，不在问卷设计中无限扩张范围。

## 3. 交付顺序

先识别主要受众及其用途；受众未说明时，按不具备用研背景的跨职能读者书写。优先展示：

1. 原始业务问题是否被覆盖；
2. 问卷会产出什么结果，以及结果如何影响行动；
3. 干净的受访者问卷；
4. 主要分支、典型/最长路径和关键实施说明；
5. 构念、偏差、证据限制和选项来源等方法附录。

这是一条信息优先级，不是固定角色或标题模板。不要让专业术语主导首页；需要研究审查的构念、口径与限制可放在后部或方法附录，但不能删除。开场文案默认只保留建立相关性或信任所必需的信息；平台已经显示的时长、进度和答题方式不重复说明。

语言默认值：内部研究计划、评审、报告、交接和数据质量说明用中文；受访者实际看到的问卷、邀请邮件和其他招募文案用英文。正式产品名、平台字段、机器字段和用户原话保留原格式。若负责人明确指定其他语言，或目标受访者不能稳定理解英文，则以目标受访者语言为准并记录翻译与测量等价性风险；不得为遵守默认值而牺牲可理解性。

## 4. 平台生产路由

进入 `production_workflow` 后读取 `references/platform-execution-common-contract.md`，再只读取目标平台契约：

- Google Forms：`references/google-forms-execution-contract.md`；`google-forms` 执行器是本家族未捆绑的可选外部 Skill，缺失时只交付人工操作与验证清单。
- SurveyMonkey：`references/surveymonkey-execution-contract.md`。
- Typeform：`references/typeform-execution-contract.md`。

本家族内置 Typeform API 执行与 Typeform response ingestion。Google Forms 执行器、邮件受众同步和邮件 campaign 执行器均为可选外部依赖；没有安装时不得声称已完成相应平台操作。

三平台共用唯一 `research-design/survey_spec.json`。平台 build plan/payload 是 lowering 产物，不是第二份设计源。平台 adapter 不得静默改变题意、样本可达性、随机化或负担。

验证强度按风险分级：

- A — 设计/评审：不创建生产产物，不运行平台 gate。
- B — 手工交接或私有草稿：preflight、read-back 和受影响 renderer 检查；无需发布级哈希 gate。
- C — agent 发布或结构/逻辑/编码变化：版本身份、代表路径、必要的测试答卷与确定性 launch gate。
- D — 医疗、法律、安全、重大财务、弱势人群或不可逆高成本决策：扩大真人预测试、多设备和运行时覆盖；不能以 synthetic pass 替代。

具体风险和证据要求见 `references/research-assurance-and-delivery.md`。只有 `publication_mode=agent_publish` 才强制使用机器生成且发布时复核证据摘要的 launch gate；`manual_handoff` 停在清单与 read-back，不声称 launch-ready。

测试答卷必须使用非个人测试标记、窄检索窗口并从正式分析中排除。记录保留/清理计划；删除答卷属于独立外部动作，需要授权且受平台能力限制。原始答卷不得提交进 skill 仓库。

## 5. Owner gate

需要确认：改变核心范围、目标人群、证据强度或研究架构；接受影响解释的折衷；以及创建/修改线上问卷、上传素材、临时公开测试、提交/删除测试答卷、打开 collector、发布、发送、扩群或使用激励。

不需要逐项确认：措辞润色、内部评审、可逆本地草稿、保持同一推断的去重和低负担优化。

生产项目文件先读取 `references/project-artifact-contract.md`；进入平台 build、受众/邮件外部交接、回收监测或 workflow dry run 时，再按需读取 `references/production-project-artifact-contract.md`。调研邀请、监测、follow-up 和报告分别使用现有专项 agent/skill；未达到数据 readiness 时只交付 sample-status，不写正式结论。

## Examples

### Bad

用一道单选“你最想要哪个功能”估算每个可共存需求的用户占比，或因平台不支持随机化就增加多道重复题。

### Good

先定义每个研究问题的目标总体、时间窗口和需要支持的行动；用适合的多选、行为题或取舍任务测量，并把平台无法实现的细节作为限制披露。

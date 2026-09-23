# Workflow Modes and Deliverables

先按当前请求选择最轻的充分模式。模式决定需要加载哪些 instructions、交付哪些产物和何时需要 owner gate；不能因 skill 支持完整生产链路，就对每个局部问题执行全流程。

## 1. Mode selection

| 模式 | 适用请求 | 默认交付 | 不应做 |
|---|---|---|---|
| `quick_review` | 审查或改写单题、选项、局部模块；比较两个版本；解释设计问题 | 直接判断、风险、建议 wording；必要时简短 action mapping | 初始化项目、生成状态 JSON、完整审计包、平台 preflight、逐项审批 |
| `survey_design` | 从研究范围生成完整问卷；系统重构现有问卷；做正式设计评审 | 业务覆盖摘要、干净问卷、路径/时长、必要实施说明、方法附录 | 自动建卷或投放、为每个内部文件设置 owner gate |
| `production_workflow` | 明确要求建卷、投放、发信、圈人、监测、follow-up 或正式报告 | 项目状态、平台 spec/preflight/read-back、生产审计和执行产物 | 未授权外部写入、把草稿描述为已发布 |

仅说“设计一份问卷”时默认 `survey_design`；仅说“这道题有什么问题”时默认 `quick_review`。只有明确的创建、发布、发送、监测或报告执行意图才进入 `production_workflow`。

## 2. Proportional rigor

研究原则不因模式改变，但证据形式与流程成本应和风险相称：

- `quick_review` 在回答中直接说明构念、题型、bias、选项和 actionability 问题，不要求单独文件。
- `survey_design` 对核心问题完成范围覆盖、目标指标、可回答性、顺序、负担和选项审查；简单研究允许把内部审查合并进方法附录。
- `production_workflow` 才使用稳定 IDs、平台契约、read-back 和可复现状态；机器 launch gate 与更广运行时证据只在 agent publish 或高风险项目启用，不能把所有 production 请求等同于全路径发布测试。

不得以“还没做完整 audit 文件”为由拒绝提供有用草稿，也不得因为只是草稿就隐瞒会使核心结论失效的问题。

## 3. Conditional artifacts

### Survey design 的最小交付

- `stakeholder_coverage_summary`：可作为正文简表，不一定独立成文件。
- `questionnaire`：干净的 respondent-facing 版本。
- `path_and_burden`：典型与最长路径、主要高负担点。
- `methods_appendix`：关键推断、限制、选项依据和未决事项；可合并 measurement、scope regression 与 rationale。

仅在存在对应需要时增加：

- 复杂或冲突 scope：atomic decision register / decision evidence matrix。
- 与上一版比较：scope regression audit。
- 原因选项承担核心决策：option provenance register。
- 复杂分支或预计分群结论：subgroup sample projection。
- 平台交接：survey spec、目标平台 capability check 与原生 build payload/plan。

### Production workflow 的完整产物

先读取 `project-artifact-contract.md`；进入平台 build、受众/邮件外部交接、回收监测或 workflow dry run 时，再读取 `production-project-artifact-contract.md`。按实际执行阶段创建文件，未进入的阶段不制造占位式“完成产物”；在 artifact index 中记录为 planned 或 not applicable 即可。

## 4. Approval policy

内部分析与可逆草稿由 agent 自主完成。只有以下变化需要 owner 决策：

- 改变核心研究范围、目标人群、证据强度或能回答的问题；
- 选择会改变投入、样本或时间的研究架构；
- 接受可能影响解释的已知设计折衷；
- 发生外部写入、发布、发送、扩群或激励；
- 把尚有实质限制的结果作为正式结论传播。

不得把“owner 曾确认一份问卷”扩展为已确认具体长度、额外 workaround 或外部发布；也不得要求 owner 审批每次措辞润色、内部审查表或不改变推断的合并。

## 5. Heuristics, not laws

MIQ 数量、选项数量、移动端行数、预计秒数等阈值用于提醒审查，不构成脱离语境的判决。触发后至少比较一个替代方案，并记录保留当前设计的理由。判断依据是：

- 是否仍匹配目标指标；
- 用户能否稳定理解并映射答案；
- 是否存在明显更低负担的等价设计；
- routing 与平台是否可实现；
- 预测试是否暴露错误或疲劳。

## 6. Language and audience

项目默认：内部工作文档使用中文；受访者实际看到的问卷、招募邮件和其他文案使用英文。两者必须明确分隔，不能把中文分析说明混入英文受访者文案。

这只是可覆盖的默认值，不是脱离目标人群的硬性语言规定。负责人明确指定其他语言，或目标受访者无法稳定理解英文时，受访者文案改用目标受访者语言；重要跨语言研究记录翻译方法、母语复核或预测试情况，以及无法保证的测量等价性。正式产品名、平台字段、机器字段、文件名、命令和用户原话保留原格式。

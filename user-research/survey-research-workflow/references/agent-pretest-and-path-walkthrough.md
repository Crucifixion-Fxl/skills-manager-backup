# Agent Pretest and Path Walkthrough

本参考用于在正式发布前，让 Agent 承担大部分内部预检：以最小充分路径集验证所有实质不同的表单行为，并以多个有依据的目标用户视角进行合成认知走查。它减少对真人预测试的依赖，但不把模拟伪装成真实用户证据。

## 1. 两类测试必须分开

### A. Instrument path walkthrough

验证表单实际行为，而不是讨论题目好不好：

- 从平台 read-back 或 canonical spec 枚举全部条件、目标和汇合点；静态验证条件可达性、优先级、循环、悬空目标及预期的 true/false 结果。
- 生成最小充分的 renderer 路径集。至少覆盖每种不同的跳转目标、汇合方式、必答阻塞、选择上限、Other/None、返回导航、结束页和其他会改变交互或结果的行为；同时包含能暴露额外问题的最短、典型、最长及边界路径。若多个答案产生完全相同的页面、跳转和编码行为，只选代表答案，不做排列组合。
- 在真实 renderer 中重点检查手机宽度和高负担组件；桌面端抽查布局可能不同的题型或刺激材料。无平台差异风险时，不要求每条逻辑路径同时在两种宽度重复执行。
- 平台允许且 owner 已授权时，用最少量带测试标记的答卷覆盖不同编码类别和关键条件组合，验证导出与稳定 ID 映射；未授权时只做不提交的 preview 走查。不要为了每条 renderer 路径各生成一份答卷。

每条运行路径记录输入答案、实际经过的题目、预期路径、结果和证据，并维护覆盖映射，说明哪些逻辑行为由哪条路径验证。静态检查不能代替代表性真实 renderer；preview 不能被写成 response-verified。

Typeform 项目先使用 `scripts/survey_preflight.js generate-typeform-paths` 从实际 payload 生成路径计划。脚本能求解的条件必须由它生成并做覆盖映射；无法求解、hidden allocation 或不可达条件会形成 blocker，不能由 Agent 手写一条“看起来覆盖”的路径替代。renderer 结果必须绑定路径计划的 `case_id`、`form_id` 和 `instrument_sha256`。相同题型与验证配置的 required、Other 和多选交互可按等价类抽取代表题；内容特异的图片、inline group 或不同限制配置不能因此合并。

“覆盖全部主要分支”指覆盖全部会改变受访者体验、样本归属或数据解释的逻辑行为，不指穷举所有答案序列。测试成本应随逻辑风险增长，而不是随所有可选答案的组合数增长。

### B. Synthetic cognitive walkthrough

验证受访者可能怎样理解、回忆、判断和映射答案。通常建立 5–8 个互有差异的测试视角；简单问卷可以更少，复杂 routing 可以更多，但至少要覆盖典型、反例、边界和所有会改变核心推断的分群，并说明覆盖依据。视角只能来自研究 brief、现有数据、VOC 或明确假设；不要编造人口比例、人格故事或不存在的用户事实。

对每个视角沿与其经历一致的实际分支逐题执行；视角集合需覆盖会改变核心推断的分群，但不要求每个视角重复全部路径：

1. 用该用户可能使用的简单语言复述题意；
2. 指出回答所需的记忆、可见信息和参照时间；
3. 尝试选择答案，并标出多项同时成立、无项可选或需要猜测的地方；
4. 检查题干是否暗示“正确答案”、预设功能或要求未体验者评价；
5. 记录阅读、回忆、判断、选项扫描、交互、敏感压力和模块切换负担；
6. 检查前文是否改变当前答案，或当前题是否只是重复前题；
7. 对问题给出最小修订，并说明修订是否改变 estimand、routing 或业务动作。

至少包含一个“反例视角”：其经历不符合产品默认假设；以及一个“边界视角”：处于两个答案或两个分支之间。开放题只能评估任务是否清楚，不得伪造用户会写出的主题分布。

## 2. 降低自我确认偏差

设计者不能只用自己预期的答案证明问卷成立。认知走查应隐藏内部 rationale，先使用干净的 respondent-facing 版本，并按以下优先顺序提高独立性：

1. 使用未参与 wording 的独立 reviewer 或新的隔离审查上下文；
2. 若用户明确授权 delegation，可由独立 agent 盲测；
3. 条件不具备时，由同一 Agent 开启独立评审 pass，先冻结问卷，再按预设视角和检查表逐项审查，不边读边为原设计辩护。

不得给评审 pass 提供“预期正确理解”或希望它发现的具体问题。修订后只重测受影响路径以及与其相邻的污染、逻辑和分析映射；只有共享组件或全局逻辑发生变化时才扩大回归范围。

## 3. 输出与证据边界

生成一个简明的 `agent_pretest_report.md`，至少包含：

```yaml
evidence_class: synthetic_cognitive_walkthrough
instrument_version:
target_audience_basis:
profiles_tested: []
profile_coverage_rationale:
paths_covered: []
issues:
  - location:
    observed_failure:
    affected_profiles: []
    impact: comprehension | retrieval | judgment | response_mapping | interaction | sensitivity | order | routing
    severity: blocker | major | minor
    proposed_fix:
    retest_status: pending | pass | fail
overall_status: pass | needs_fix | blocked
human_pretest_status: not_run | planned | completed
residual_risks: []
```

同时维护目标平台的 `<platform>_logic_test_matrix.md`。不要把两者合并成一份含糊的“测试通过”：前者是模拟认知证据，后者是表单行为证据。

以下结论禁止从合成走查得出：

- “5/8 用户会这样理解”或任何发生率、偏好比例；
- 真实用户一定能理解、一定愿意完成或不会退出；
- 某选项已经覆盖常见原因；
- 模拟角色的文字是用户 quote 或 VOC；
- 通过模拟即可证明翻译、无障碍、敏感性或文化适配有效。

## 4. 修订与 gate

- `blocker`：导致错误 cohort、错误分支、无法作答、核心构念改变或关键结论不可解释；修复前不得进入发布确认。
- `major`：存在可信的误解、多项难以区分、显著负担或顺序污染；修订并重测，或由 owner 明确接受解释风险。
- `minor`：不改变答案含义与核心路径的局部摩擦；可修订后抽样复查。

Agent 可自主修复不改变 scope、estimand 和平台权限的措辞或逻辑缺陷。修订改变核心题意、删减研究目标、引入平台 workaround 或需要外部写入时，遵守原有 owner gate。

对于低敏感、成熟题型、清楚目标人群且没有剩余 major/blocker 的问卷，Agent 走查可以作为主要内部 preflight；`human_pretest_status=not_run` 和残余风险必须透明保留，但默认是 warning，不要求 owner 另行签署风险接受。只有法规、安全、重大财务/医疗、不可逆决策或 owner 明确要求时，才设置 `require_residual_risk_resolution=true` 并把真人预测试或风险接受升级为硬门槛。

出现以下任一情况时，真人认知预测试仍为强烈建议，不能被 synthetic pass 静默替代：

- 新概念、陌生术语、复杂权限或重要概念卡；
- 敏感、医疗、法律、财务、未成年人或弱势人群；
- 翻译、多文化语境、无障碍或低读写能力要求；
- 复杂 grid、排序、视觉刺激或高认知任务；
- 合成走查仍存在多种同样合理的理解；
- 研究结论将触发高成本或难以逆转的决定。

即使执行真人预测试，Agent 仍负责测试方案、记录模板、问题聚类、修订建议和回归走查；真人证据与 synthetic evidence 分开标记。

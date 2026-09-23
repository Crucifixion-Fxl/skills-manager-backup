# 研究范围与测量

本参考回答三个问题：产品真正要决定什么、什么证据能回答它、问卷结果允许推断到哪里。它适用于完整设计与高影响局部评审；简单改题只使用相关部分。

## 1. 从产品范围收敛到最重要问题

先登记源文档、子文档和 owner 指令中的目标、概念测试、目标人群、约束与非目标。每项记录来源、原始关注点、`keep / defer / drop / clarify`、去向和理由。

- `keep` 必须落到研究问题、问卷模块或明确的非问卷证据。
- owner 明确提出的目标不能被 agent 自行删除、延后、拆 wave 或弱化；可以提出更优架构并说明取舍。
- 来源冲突标为 `clarify`。新增核心构念必须能追溯到产品决定；用户对隐私、理解或 “it depends” 的反馈首先是测量条件，不自动成为新研究主线。
- 范围追溯只证明“有去向”，不证明“等价测量”。

把宽泛目标拆成少量 Most Important Questions（MIQs）。每个候选问题至少明确：decision、population/context、required output、evidence gap、method fit、decision rule 和 priority。优先：

1. 会改变具体决定；
2. 现有证据无法回答；
3. 当前样本与方法能回答；
4. 相比负担具有足够信息价值。

MIQ 数量是架构审查信号，不是机械上限。范围过大时比较全员 core + routing、定向 wave、split sample、访谈/原型/实验等方案；只给建议和影响，不替 owner 决定。

## 2. 原子决策与覆盖等价

同一业务主题可能包含不同原子决策，例如需求覆盖率、首要优先级、具体内容类型、更新节奏、分享渠道和流程改进。对每项声明：

- population 与 denominator；
- construct 与所需颗粒度；
- estimand/输出；
- 最强可用证据；
- 高低信号对应动作。

只有 population、construct、estimand、evidence strength 和行动颗粒度均保持时，才能称 `full/preserved`。常见不等价替换包括：

- 多项 incidence/coverage → 单一 top choice；
- 各渠道使用/需求覆盖 → primary channel；
- 实际行为 → 假设意向；
- 具体实施偏好 → 宽泛兴趣或有用性；
- 私域分享需求 → 整体分享需求，或反之。

新版相对源材料、owner 增补或已接受版本出现 `partial / weakened / lost` 时，先执行 Coverage–Burden Recovery：比较短核心题、资格分支、渐进诊断、外部证据和其他方法。无法低负担恢复时，把具体取舍交 owner，不照搬旧题，也不静默丢失信号。

## 3. Measurement contract

每个核心问题在正式 wording 前定义：

| 字段 | 含义 |
|---|---|
| Decision | 哪个决定会使用结果 |
| Target construct | 真正要测的概念及边界 |
| Population/context | 对谁、在什么场景测量 |
| Reference period | 行为或体验时间窗 |
| Estimand/output | incidence、主因分布、强度、排序、取舍或主题 |
| Respondent basis | 用户何时、通过什么体验知道答案 |
| Intended inference | 回答允许支持什么 |
| Alternatives | 哪些其他原因也可能产生同样回答 |
| Strongest evidence | 当前可获得的最强证据 |
| Decision action | 不同结果会改变什么 |
| Limit | 不能据此推出什么 |

构念无法定义、同一题混合多个未固定构念、用户不知道答案、题型不能产生计划指标或关键替代解释未处理时，不能定稿。

## 4. 证据强度与题型

通常按以下顺序优先：遥测/直接观察 → 近期具体 past behavior → 最近一次 current experience → 有现实约束的 choice/trade-off/task → 一般偏好或态度 → 条件明确的 intention → 无条件 future prediction。

- 可靠遥测能回答行为发生、频率、路径或留存时，问卷只补动机、情境与不可观察因素。
- 能问最近一次做了什么，不用“以后会不会”替代。
- 单选适合互斥状态、唯一事件、主导项或 forced choice；只能报告相应份额。
- 多选/逐项适合估计可共存需求各自的 incidence 或 coverage；如还需优先级，再加独立取舍任务。
- 量表只测一个明确维度；模糊“兴趣/有用性”不能承担采用预测。
- 开放题用于自然心智模型或未知尾部，并预先考虑编码；不能补救明显缺失的封闭选项。
- routed subgroup 若预计样本不足，提出预分群、定向招募、定性补充或结论降级。

## 5. 可回答性、预设与 bias

逐题检查：

- 用户是否亲历或看见了回答所需信息，能否在时间窗内可靠回忆？
- 是否有未固定前提，使合理答案只能是 “it depends”？
- 是否依赖未经验证的产品事实或把未来概念写成当前能力？
- 是否默认需求、价值、满意或问题存在？是否有真实的 current way / none / not applicable 路径？
- 是否把相关性、偏好或意向误当成因果与行为？
- 是否存在 leading、loaded、framing、double-barrelled、hidden assumption、acquiescence、social desirability、demand characteristics、recall/telescoping、hypothetical、anchoring 或 forced-answer bias？

Sampling、coverage、self-selection、non-response、survivorship、mode 和 reporting bias 属于招募/分析计划，不能靠 wording 宣称解决。

## 6. 概念测试

直接问 “Would you use / Do you like / Is this useful?” 容易得到礼貌性 yes，通常只测 stated attitude/intention。更强设计依次考虑：

1. underlying problem 是否近期发生；
2. 用户目前如何解决、付出什么成本；
3. 中性概念是否被正确理解；
4. 与 current way / none / 其他方案的真实或近真实取舍；
5. 原型任务、pilot opt-in、候补名单或其他可撤回行为；
6. 最后才是条件明确的意向。

结果必须按证据层级命名，不能把概念好感直接解释为 adoption forecast。概念出现前先完成需要保持 unaided 的问题；若图片或界面是判断依据，另读 `visual-stimulus-and-randomization.md`。

## 7. 多题量表与成熟指标

测量信任、满意度、归属感、感知价值等抽象构念时，先查找适用人群与语境中已有证据的量表。采用或修改时记录原来源、构念、计分方式、授权条件和本次变更。

- 不因题目“看起来像 Likert”就声称量表有效。
- 删除、改写或翻译成熟量表条目后，原 reliability/validity 证据不自动继承。
- 多题必须共同服务一个预先定义的构念；正反向题只有在降低特定响应偏差的收益大于理解错误时使用。
- 发布前定义 score construction、允许缺失条目数、反向计分和最低解释样本。
- Reliability 不能单独证明 content validity；高 alpha 也可能来自重复题。
- 跨语言、设备或关键分群比较时，记录 measurement equivalence 风险；证据不足时只做带限制的组内描述。

普通单题产品反馈不必机械升级为多题量表。

## 8. 最小门槛

以下问题会使核心结论失效，必须修复或明确交 owner：

- 明确源目标无去向或未经确认被弱化；
- population、construct、estimand 或颗粒度变化却仍声称完整覆盖；
- 用户缺少回答依据；
- 题型与计划指标不匹配；
- 无约束意向被当作行为预测；
- 核心题依赖未验证事实或有严重预设/诱导；
- 可靠、更强证据存在却无理由使用高负担自报；
- 多个独立研究主线被强行拼接，且 owner 未确认架构与取舍。

## 方法依据

- SurveyMonkey survey bias 与中性措辞：https://www.surveymonkey.com/learn/survey-best-practices/how-to-avoid-common-types-survey-bias/
- Pew Research Center 问题措辞、顺序与回答选项：https://www.pewresearch.org/writing-survey-questions/
- AAPOR questionnaire 与 sample best practices：https://aapor.org/standards-and-ethics/best-practices/
- UK Government Respondent Centred Design：https://analysisfunction.civilservice.gov.uk/policy-store/questionnaire-design-guidance/

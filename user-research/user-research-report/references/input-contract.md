# 研究报告输入契约

本文件定义 `user-research-report` 可接受的最小数据结构。它是语义契约，不要求上游工具使用同一种文件格式；CSV、JSON、表格、数据库导出或人工整理数据都可以先映射到这些字段。

## 1. 必需元数据

```yaml
schema_version: 1
metadata:
  title: "研究标题"
  objectives:
    - "本研究要回答的决策问题"
  population: "目标人群"
  sampling_method: "抽样或招募方式；未知时明确写 unknown"
  fieldwork_start: "YYYY-MM-DD"
  fieldwork_end: "YYYY-MM-DD 或 null"
  collection_status: "in_progress | closed"
  instrument_version: "问卷或访谈提纲版本"
  instrument_hash: "可选；有自动化链路时提供"
  snapshot_at: "数据快照时间"
  raw_n: 0
  valid_n: 0
  exclusion_rules:
    - "排除规则及排除人数"
  weighting: "none，或权重定义"
```

至少要能回答：研究对象是谁、数据何时收集、当前是否仍在回收、有效样本如何得到、报告对应哪个研究工具版本。

## 2. 题目级数据

```yaml
questions:
  - id: "Q1"
    topic: "主题标签"
    wording: "受访者实际看到的题目"
    type: "single_choice | multi_select | ordinal_scale | numeric | ranking | matrix | open_text"
    denominator_definition: "谁有资格回答，以及百分比以谁为分母"
    n_eligible: 0
    n_responded: 0
    missing_n: 0
    options:
      - value: "编码"
        label: "选项原文"
        count: 0
        percent: 0.0
    summary:
      median: null
      mean: null
      quantiles: null
    notes: []
```

规则：

- 单选、多选和量表优先保留各选项原始人数与百分比，不只保留均值或净值。
- 多选题百分比之和可以超过 100%，必须注明“多选”。
- 跳题、显示逻辑和可答人群必须进入 `denominator_definition`。
- `n_eligible`、`n_responded`、缺失值和百分比分母应能互相核对。
- 题目原文与选项原文应保留，避免分析阶段凭标签误解构念。

## 3. 联合分析所需数据

交叉分析、配对比较、路径分析或 respondent-level 去重需要受访者级数据。报告包可以提供受控引用，不应强制把原始个人数据嵌入主报告。

```yaml
respondent_level_ref:
  location: "受控文件或数据集引用"
  join_key: "去标识化 respondent_id"
  fields_available: ["Q1", "Q2", "segment"]
  access: "restricted"
```

若只有汇总表，必须说明哪些联合分析无法执行，不能从边际百分比推断个体关联。

## 4. 派生分析

```yaml
derived_analyses:
  - analysis_id: "A1"
    kind: "cross_tab | model | composite | net_score | tiering | other"
    status: "planned | exploratory"
    source_question_ids: ["Q1", "Q2"]
    research_question: "该分析帮助回答什么问题"
    estimand: "实际估计的量或比较对象"
    method: "计算、模型或分组规则"
    result: {}
    uncertainty: {}
    caveats: []
```

- 原始题目中的 Top/Bottom 选择、排名或评分是题目结果，应正常报告，不需要额外称为“预先定义的派生指标”。
- Net、综合排名、指数、分档及其他二次计算必须说明公式、适用理由和状态。
- 研究开始前确定的主要派生指标标为 `planned`；数据后新增的合理探索标为 `exploratory`，而不是禁止分析。
- 派生结果不能替代其关键原始组成；例如展示 Net 时，同时保留 Top 与 Bottom 分布。

## 5. 开放题与编码包

```yaml
qualitative:
  coding_package_ref: "可选；user-research-coding 的产物"
  raw_text_ref: "可选；受控的原始文本位置"
  language: ["en"]
  consent_and_use_notes: "引用、翻译和共享限制"
```

存在正式编码包时优先复用其 codebook、编码矩阵、备忘录和异常案例，不要在报告阶段重新发明一套标签。只有原始文本时，可做轻量归纳，但必须说明方法与局限。

## 6. 数据质量与研究限制

```yaml
quality:
  duplicate_handling: "规则"
  speed_or_attention_checks: "规则或 none"
  missingness_notes: []
  branch_coverage_notes: []
  known_biases: []
  privacy_constraints: []
```

不要求每项都有复杂检测，但未知、未检查与确认无异常必须区分。

## 7. 常见语义陷阱

- 只有标准推荐意愿题（通常为 0–10 分）并按 Promoter / Passive / Detractor 定义计算时，才称为 NPS。
- 购买、续费、订阅或使用意愿不是 NPS，应按题目原意命名。
- “不知道某功能”不能自动解释为“教育后一定会购买”。
- “更喜欢 A”不能自动解释为“A 会提升留存或收入”。
- 未随机分配的组间差异通常是关联，不是因果效应。

## 8. 上游适配要求

Google Forms、Typeform、SurveyMonkey、访谈工具或监测脚本的原始输出可能不同。接入前应通过适配器或映射表生成本契约所需字段，并验证：

1. 工具版本和数据快照一致；
2. 题目 ID、显示逻辑和分母没有丢失；
3. 选项编码与展示文案一致；
4. 多选、矩阵、排名和开放题没有被误读；
5. 上游未提供的字段被标为未知，而不是虚构默认值。

# 生产项目产物契约

本契约仅用于 `production_workflow`，或用户明确要求可执行的平台交接包。`quick_review` 不使用本契约；普通 `survey_design` 可按 `workflow-modes-and-deliverables.md` 合并或省略内部产物，不得被本文件强制扩张为生产项目。

## 文档语言约定

项目内研究计划、评审、报告、交接和数据质量说明默认使用中文。

受访者实际看到的问卷、邀请邮件和其他招募文案默认使用英文。负责人明确指定其他语言，或目标受访者不能稳定理解英文时，以目标受访者语言为准，并在 `project-context.md` 中记录覆盖理由和翻译/测量等价性风险。JSON 字段名、枚举值、平台固定字段、正式产品名、API 字段、文件名、命令和用户原话按原格式保留。

## 标准项目结构

```text
<project>/
  project-context.md
  artifact-index.md
  workflow-state.json
  decision-log.md
  assumption-log.md
  research-design/
    research-brief.md
    atomic_decision_register.md
    decision_evidence_matrix.md
    instrument_architecture.md
    survey_outline.md
    measurement_construct_audit.md
    question_wording.md
    design_rationale_pack.md
    scope_regression_audit.md
    platform_capability_check.md
    survey_quality_gate.json
    agent_pretest_report.md
    survey_spec.json
    email_brief.md
  survey-build/
    <platform>_build_plan.json or native payload
    <platform>_preflight_report.md
    <platform>_id_mapping.template.json
    <platform>_id_mapping.json
    <platform>_readback.json
    <platform>_readback_diff.json
    <platform>_logic_test_matrix.md
    <platform>_response_export.csv (when used)
  email-campaign/
    email_copy.md
    campaign_copy_rationale.md
    campaign_spec.json
    campaign_spec.snapshot.json
    mailchimp_campaign_plan.json
    mailchimp_preflight_report.md
    campaign_id_mapping.json
    mailchimp_readback_diff.json
    mailchimp_manual_setup_checklist.md
  monitoring/
    monitor_status.json
    health_report.md
    review_tab.csv
    findings_data.json
  follow-up-and-report/
    followup_strategy.md
    followup_strategy.json
    sample-status.md
    research-report.md
  workflow-dry-run/
    workflow_dry_run_status.json
    workflow_dry_run_report.md
```

## Workflow Stages

`workflow-state.json` 使用以下稳定 stage name：

- `intake`
- `research_plan`
- `survey_design`
- `survey_build`
- `campaign_copy`
- `audience_sync`
- `campaign_draft`
- `collection_monitor`
- `followup_strategy`
- `report`
- `workflow_dry_run`
- `closed`

## Follow-up Routes

`followup_strategy.json` 和 `workflow-state.json` 使用以下稳定 route：

- `revise_survey`：回到 `survey-designer`，修订统一 spec 后重新走目标平台 preflight、read-back 和全路径 preview。
- `revise_email`：回到 `research-email-writer`，修订邮件后重新走 Mailchimp draft/test。
- `sample_action`：`follow-up-strategist` 已判断应等待、追发或扩群；owner 确认并完成动作后，才回到 `user-research-monitor` 复评 readiness。
- `sample_status_memo`：不继续扩样或修订，只输出 `sample-status.md`，不写正式结论。

`follow-up-strategist` 负责提出 route；owner 负责确认；`survey-research-workflow` 只负责记录 route、更新 index/state 并路由下一步。

`followup_strategy.json` 最小契约：

```json
{
  "status": "followup_needed",
  "route": "sample_action",
  "sample_action": "wait | resend | expand_audience | split_new_wave | null",
  "diagnosis": "Evidence-backed bottleneck summary",
  "recommended_action": "Concrete next action for owner review",
  "owner_decisions_needed": [],
  "requires_new_wave": false,
  "next_files_to_update": []
}
```

## Artifact Status

`artifact-index.md` 使用以下稳定 status：

- `missing`
- `draft`
- `needs_review`
- `approved`
- `superseded`
- `blocked`
- `final`

## 必须更新状态的场景

创建或修改任何产物后：

1. 在 `artifact-index.md` 新增或更新对应行。
2. 更新 `workflow-state.json` 的 current stage、gate status 和 next action。
3. 如果用户批准、否决或改变方向，追加写入 `decision-log.md`。
4. 如果产物依赖未验证事实，追加写入 `assumption-log.md`。

## Research Brief 最小字段

```yaml
project_id:
owner:
research_goal:
decision_to_support:
decision_options: []
decision_rule: []
method: survey
target_audience:
audience_context:
recency_window:
key_asks: []
candidate_research_questions: []
miq_prioritization:
  - research_question_id:
    decision_mapping:
    evidence_gap:
    method_fit:
    priority: core | supporting | defer | separate_method
    rationale:
    owner_confirmation: pending | confirmed
hypotheses: []
known_facts: []
unknowns: []
business_levers: []
sample_plan:
  target_valid_responses:
  minimum_valid_responses:
  segment_quota: []
constraints:
  language:
  max_minutes:
  channel:
privacy_constraints: []
must_not_ask: []
assumptions: []
evidence_source_allocation:
  - signal:
    preferred_source: telemetry | survey | interview | prototype | experiment | existing_evidence
    survey_role:
    rationale:
survey_spine:
  primary_decision_family:
  supporting_modules: []
  separate_wave_or_method: []
  module_order: []
  order_rationale: []
  concept_exposure_boundary:
  introduction_contract:
    respondent_relevance:
    estimated_time:
    trust_information:
    prohibited_priming: []
  first_question_rationale:
  opening_question_candidates:
    - question_intent:
      immediate_answerability:
      estimated_seconds:
      mobile_first_screen_burden:
      substantive_option_count:
      mobile_option_line_count:
      longest_option_line_count:
      option_distinction_difficulty: low | medium | high
      estimated_scan_seconds:
      routing_value:
      narrative_transition:
      priming_risk:
      selected: true | false
      rationale:
  shortest_path_estimate:
  longest_path_estimate:
  response_burden_budget:
    per_question_components: [comprehension, retrieval, judgment, response_mapping, interaction, sensitivity]
    shortest_route:
      estimated_seconds:
      option_display_lines:
      open_text_count:
      complex_task_count:
      conceptual_switches:
    typical_route:
      estimated_seconds:
      option_display_lines:
      open_text_count:
      complex_task_count:
      conceptual_switches:
    longest_route:
      estimated_seconds:
      option_display_lines:
      open_text_count:
      complex_task_count:
      conceptual_switches:
    burden_peaks: []
```

MIQ 数量较多是 instrument-architecture review signal，不是核心原子决策或题目数量的硬上限。一个 primary decision family 可以包含多个必要子决策；当范围明显超过单一问卷可承载能力时，比较全员 core + routing、定向 wave、split sample 和其他方法，而不是先删目标。具体阈值由项目复杂度、路径负担和样本能力决定。Scope Fidelity Audit 继续保留所有源目标的去向，不能借“优先级”静默删除。

## Decision Coverage 产物契约

复杂范围、版本比较或生产交接时按 `references/research-scope-and-measurement.md` 生成：

- `atomic_decision_register.md`：把宽泛目标拆成可触发不同产品动作的原子决策。
- `decision_evidence_matrix.md`：为每个原子决策声明 required output、population、estimand、证据来源、分析去向、decision rule、coverage equivalence 和 coverage status。
- `instrument_architecture.md`：比较单卷、routing modules、分 wave、split sample 与多方法方案，并估计关键 routed subgroup n。
- `scope_regression_audit.md`：定稿前比较源材料、owner 增补、上一版与新版的信号变化。

核心 decision 的 `coverage_status` 只能在 measured construct 与 source construct 等价、证据足以形成 required output 时标为 `covered_strong / covered_weak`。相邻构念、颗粒度下降或只覆盖部分 facet 必须标 `partial`。任何未经 owner 确认的 `partial / deferred / lost`，以及版本回归中的 `weakened / lost`，都阻断 final `survey_spec.json`。

Population、denominator、estimand 与 evidence strength 必须逐项比较。把多项 incidence/coverage 改成单一 top choice，或把各渠道 coverage 改成 primary channel，默认属于 estimand 变化，不能记作 `full`。源需求未说明要 coverage、priority 还是两者时，状态为 `clarify`，由 owner 决定。

包含功能概念时，brief 还必须声明：

```yaml
concept_test_contract:
  decision_to_support:
  underlying_problem:
  evidence_target: problem_evidence | comprehension | relative_appeal | usability | behavioral_commitment | stated_intention
  prior_behavior_evidence: []
  current_alternatives: []
  concept_exposure_order:
  tradeoff_or_task:
  strongest_followup_validation:
  prohibited_inference:
```

若 `evidence_target` 只是 `stated_intention`，`prohibited_inference` 必须明确不得据此预测真实采用率。

## Survey Outline 最小契约

`survey_outline.md` 是问卷正式 wording 前的设计方案。它必须证明每个模块和题目意图都能回答 key asks，并能支持后续分析或业务动作。

每个 outline item 至少包含：

```yaml
module_id:
sequence:
transition_from_previous:
order_rationale:
concept_exposure_status: pre_exposure | current_product_exposure | post_concept_exposure
module_objective:
key_ask_mapping: []
atomic_decision_mapping: []
question_intent:
proposed_type:
analysis_use:
required_output:
owner_action_if_signal_high:
owner_action_if_signal_low:
keep_cut_rationale:
respondent_risk:
platform_risk:
dependencies:
cohort_specific_variants: []
```

`question_wording.md` 只能在 outline review 通过后生成。正式 wording 必须继承 outline 中的 `key_ask_mapping`、`atomic_decision_mapping`、`required_output` 和 `analysis_use`，不得新增无法追溯到原子 decision 的核心问题。

## Measurement / Construct Audit 契约

`measurement_construct_audit.md` 是强制门禁产物，包含 `pre_wording` 和 `post_wording` 两次审查。每次至少包含：

```yaml
scope_audit_status: pass | needs_fix | blocked
source_scope_traceability:
  - source:
    source_concern:
    type: goal | decision | concept_test | audience | constraint | non_goal
    disposition: keep | defer | drop | clarify
    destination: []
    rationale:
    owner_confirmation: not_needed | pending | confirmed
phase: pre_wording | post_wording
overall_status: pass | needs_fix | blocked
measurement_contracts:
  - key_ask_id:
    target_construct:
    construct_definition:
    construct_exclusions: []
    population_context:
    reference_period:
    intended_inference:
    alternative_explanations: []
    strongest_available_evidence:
    selected_measure:
    evidence_strength:
    external_validation_data: []
question_audits:
  - question_id:
    primary_construct:
    estimand:
    response_format_rationale:
    measurement_role: observed_behavior | past_behavior | current_experience | preference_tradeoff | attitude | intention | prediction | screener
    respondent_basis:
    eligible_population:
    routing_evidence:
    product_assumptions:
      - assumption:
        source:
        verification_status: verified | unknown | contradicted
    presupposition_check:
      status: pass | needs_fix | blocked
      neutral_exit:
    construct_boundaries:
      includes: []
      excludes: []
      adjacent_constructs: []
    marginal_decision_value:
    deletion_consequence:
    atomic_decision_mapping: []
    required_output:
    information_yield:
      decision_criticality: low | medium | high
      uniqueness: duplicated | incremental | unique
      action_specificity: weak | moderate | strong
    burden_distribution:
      estimated_percent_shown:
      seconds_when_shown:
      expected_seconds_per_invited_user:
    burden_assessment:
      estimated_seconds:
      mobile_complexity: low | medium | high
      comprehension: low | medium | high
      retrieval: low | medium | high
      judgment: low | medium | high
      response_mapping: low | medium | high
      interaction: low | medium | high
      sensitivity: low | medium | high
      primary_burden_source:
      simpler_alternative_considered:
      validity_tradeoff:
    wording_status: pass | needs_fix | blocked
    bias_flags: []
    option_set_status: pass | needs_fix | blocked
    cross_question_flags: []
    actionability_status: pass | needs_fix | blocked
    required_revision:
blockers: []
owner_decisions_needed: []
```

Scope fidelity audit 在 outline 前执行；显式源目标无去向、未经确认的 `defer / drop`、来源冲突或无法追溯决策的新增核心构念都会阻断后续设计。Pre-wording audit 不是 wording review，而是检查构念、推断和测量策略，并为每题建立 Question Evidence Contract。Post-wording audit 必须逐题检查题干、选项、顺序和跨题关系。任何 scope、question contract 或核心问题为 `blocked` 时，不得生成 final `survey_spec.json`。

## Survey Quality Gate 契约

`survey_quality_gate.json` 是把分散审查结果收敛为发布级机器门禁的产物。初始模板位于 `assets/templates/survey-quality-gate.json`，严重度与风险分级见 `references/research-assurance-and-delivery.md`。只有 `publication_mode=agent_publish` 的 C/D 级执行强制使用；manual handoff/private draft 使用审查摘要、read-back 和受影响 renderer，不要求此文件。Quick review 和普通设计草稿也不生成。

发布前另生成 `agent_pretest_report.md`，按 `references/agent-pretest-and-path-walkthrough.md` 记录覆盖典型、反例、边界和关键分群的 synthetic cognitive walkthrough（通常 5–8 个视角，按复杂度调整并记录覆盖依据）。其 `evidence_class` 必须为 `synthetic_cognitive_walkthrough`，并与目标平台的 `<platform>_logic_test_matrix.md` 分开：前者审查理解和作答映射，后者验证真实表单行为。`human_pretest_status` 不得因模拟通过而写成 `completed`。

必须覆盖：architecture approval、独立的 burden approval、source estimand equivalence、Respondent Task Delta Audit、platform workaround、stakeholder delivery、Agent pretest、reason option sets、完整的 `closed_question_ids` 清单及每道封闭题的 option order check、所有子检查状态、显式 blockers、overall status 与 launch readiness。Agent pretest 必须分别记录有依据且覆盖充分的 synthetic cognitive walkthrough、真实 renderer 的主要分支与移动端走查，以及真人预测试是否实际发生。任一封闭题缺少顺序记录或出现无法对应题目的多余记录都阻断。状态按最严重项聚合：任一 `blocked` => overall `blocked`；无 blocked 但任一 `needs_fix` => overall `needs_fix`；只有全部通过且必要 owner confirmation 完成才可为 `pass`。

使用：

```bash
node scripts/survey_quality_gate_check.js check <project>/research-design/survey_quality_gate.json
```

脚本非零退出时不得执行 agent publish 或声称 launch-ready；设计稿与 manual handoff 仍可交付，但必须清楚标记未通过或不适用的发布证据。

## Question Wording 与 Rationale 契约

`question_wording.md` 写 respondent-facing 题干、选项、说明和 routing；`design_rationale_pack.md` 写内部解释。

`stakeholder_coverage_summary.md` 是面向普通 PM / 运营的首页摘要，按原始业务问题列出 survey evidence、owner output、high/low signal actions、cannot conclude 和 coverage。`option_provenance_register.md` 为每个核心原因选项记录 causal layer、provenance type、evidence reference、commonness evidence、action bucket、distinctness 与状态。详细规则见 `references/research-assurance-and-delivery.md`。

每个核心问题必须能追溯到：

- `research-brief.md` 中的 key ask。
- `survey_outline.md` 中的 question intent。
- 对应的 `analysis_use`。
- 对应的 owner action。
- Actionability Test 结果。

如果某题或某个核心选项无法说明后续动作，必须删除、合并或改成开放探索题。

原因题超过 6 个实质选项、跨两个以上因果层、父子项竞争或多个选项导向同一 action bucket 时，必须使用宏观机制到条件细因的 progressive diagnosis，或记录 owner-confirmed exception。`expert_hypothesis` 不能作为“常见原因”的证据。

## Survey Spec 最小契约

`survey_spec.json` 是 survey design 到问卷平台执行层的交接文件。标准位置是 `<project>/research-design/survey_spec.json`。它是平台中立的执行意图，不应冒充任何平台 API 的原生 payload；具体 payload 必须由已验证的 connector/API 能力生成。

```json
{
  "survey_id": "string",
  "title": "string",
  "description": "string",
  "language": "en",
  "estimated_minutes": 5,
  "pages": [
    {
      "page_id": "stable_page_id",
      "title": null,
      "description": null,
      "questions": [
        {
          "question_id": "stable_question_id",
          "type": "single_choice",
          "required": true,
          "title": "Respondent-facing question",
          "description": null,
          "options": [
            {
              "option_id": "stable_option_id",
              "label": "Respondent-facing option",
              "is_other": false,
              "image": null
            }
          ],
          "validation": null,
          "logic": [],
          "randomization": "none",
          "option_order": {
            "strategy": "natural | randomized | rotated | fixed",
            "fixed_option_ids": [],
            "rationale": "string"
          },
          "image": null,
          "visual_stimulus": null,
          "metadata": {
            "key_ask_mapping": ["K1"],
            "analysis_use": "What this answer supports"
          }
        }
      ]
    }
  ],
  "collector_requirements": {
    "type": "web_link",
    "anonymous_responses": "owner_decision_required",
    "multiple_responses": "owner_decision_required",
    "response_editing": "owner_decision_required",
    "cutoff": null,
    "response_limit": null,
    "custom_variables": []
  }
}
```

统一 spec 使用 `pages[].questions[]`，不再为任何平台生成第二份顶层 `questions[]` 设计文件。平台 adapter 可以在内存或 build plan 中 flatten pages，但不得覆盖 canonical spec。`page_id`、`question_id`、`option_id` 全局稳定且唯一；平台 read-back 后建立对应 ID 映射。

最小 logic item 使用 `{"condition":{"operator":"selected","option_id":"..."},"action":{"type":"go_to_page","target_page_id":"..."}}`。更复杂条件可以扩展，但每个平台必须先证明能无损表达；不能表达时产生 blocker。为了兼容旧 Google Forms 产物，adapter 可读取 legacy `routing`，但新 spec 不应继续写两种逻辑。

`image` 是平台执行所需的可见媒体（含 HTTPS source、neutral alt text 和需要时的尺寸）；`visual_stimulus` 是研究语义与随机化记录，两者不能互相替代。

包含图片、概念卡或视觉比较时，`visual_stimulus` 不得省略为实现细节。至少记录稳定 stimulus/concept IDs、判断所需细节级别、展示载体、刺激位置/概念顺序/受访者版本分配策略、答案映射及移动端与桌面端 renderer 状态。答案选项随机化不能代替后三种随机化；完整字段见 `visual-stimulus-and-randomization.md`。

不要把业务设计理由写进用户可见文案。设计理由放在 `design_rationale_pack.md`。

## 平台执行与后续阶段

平台 build、可选受众/邮件执行器、monitor status 和 workflow dry run 的产物契约已拆分到 `production-project-artifact-contract.md`。进入对应生产阶段时按需读取；只做研究设计时不要加载这些执行产物。

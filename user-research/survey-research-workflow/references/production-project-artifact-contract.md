# 平台执行与后续项目产物契约

本参考承接 `project-artifact-contract.md` 的生产执行部分。只有进入平台 build、受众/邮件外部交接、回收监测或 workflow dry run 时才按需读取。

## 三平台共同 Build 契约

Google Forms、SurveyMonkey 和 Typeform 共同遵守 `platform-execution-common-contract.md`。所有平台从同一 `survey_spec.json` lowering，并按 `documented → api_accepted → readback_verified → renderer_verified → response_verified` 分层留证。平台切换不复用 capability pass；任何改变题意、样本可达性、刺激呈现、随机化或 respondent burden 的 workaround 必须回写设计记录并在必要时由 owner 确认。

## Google Forms Build 产物契约

- `google_forms_capability_check.md`：账户、Cloud project、OAuth scopes、quota、题型、section routing、图片、shuffle、publishSettings 和证据层级。
- `google_forms_build_plan.json`：从统一 spec 生成的 Create + batchUpdate + 二阶段 routing 计划。
- `google_form_preflight_report.md` 与 `form_build_instructions.md`。
- `google_forms_id_mapping.template.json` / `google_forms_id_mapping.json`：稳定 IDs 到 form/item/question IDs、answer value、revisionId 和 publish state 的映射。
- `google_forms_readback.json` / `google_forms_readback_diff.json`。
- `google_forms_logic_test_matrix.md`、`google_forms_launch_gate.json` 与 `google_forms_publish_result.json`。

Google Forms 必须使用 `unpublished=true` 创建；创建、批量写入、提交测试答卷、发布和打开回答分别确认。具体约束见 `google-forms-execution-contract.md`；实际 API 执行需要本家族未捆绑的可选 `google-forms` Skill。

## SurveyMonkey Build 产物契约

执行层必须先把 `survey_spec.json` 转成可检查的本地计划，再创建真实 SurveyMonkey draft。没有可用且已授权的 SurveyMonkey connector/API 时，只生成手工建卷说明，不声称已创建。

- `surveymonkey_build_plan.json`：按 survey → pages → questions → choices → logic → collector 分层的执行计划、blockers 和 warnings。
- `surveymonkey_preflight_report.md`：账户/套餐/权限、能力、逻辑兼容性、隐私与 collector 预检。
- `surveymonkey_manual_build_instructions.md`：connector/API 不可用时的逐页人工建卷、逻辑和 collector 设置说明。
- `surveymonkey_id_mapping.template.json`：read-back 前的占位模板。
- `surveymonkey_id_mapping.json`：read-back 后的真实 SurveyMonkey IDs 与研究稳定 IDs 映射。
- `surveymonkey_readback_diff.json`：线上 draft 与 `survey_spec.json` 的差异。
- `surveymonkey_logic_test_matrix.md`：覆盖最短、典型、最长、disqualify 和每条分支的 preview 结果。

如果 `surveymonkey_build_plan.json.preflight_status` 是 `block`，必须回到 `survey-designer` 修改 `survey_spec.json`。如果是 `warn`，必须在 owner gate 里展示 warning。普通 Question Skip Logic 必须向前跳转并跨 page break；逻辑应在题目与页面稳定后配置。创建 draft 后必须 read back，并在 preview 中测试全部可达路径。

## SurveyMonkey ID Mapping 契约

`surveymonkey_id_mapping.json` 用于连接 research IDs、SurveyMonkey IDs、collector 与导出列。

```json
{
  "survey_id": "string",
  "surveymonkey_survey_id": "string",
  "collector_id": "string or null",
  "collector_url": "https://... or null",
  "pages": [
    {
      "page_id": "stable_page_id",
      "surveymonkey_page_id": "string"
    }
  ],
  "questions": [
    {
      "question_id": "stable_id",
      "surveymonkey_question_id": "string",
      "title": "Question title"
    }
  ],
  "export_columns": {},
  "created_at": "ISO-8601",
  "preflight_status": "pass"
}
```

## Typeform Build 产物契约

选择 Typeform 时，用以下产物替代对应的 SurveyMonkey build 产物：

- `typeform_capability_check.md`：账户、workspace、region、token scopes、题型、Logic Jumps、布局、图片、各随机化层级、套餐依赖及每项能力的 evidence level。
- `typeform_image_result.json`：需要图片时保存 Typeform image URL、image ID、源文件名和 SHA-256；多图时可使用编号文件或数组清单。
- `typeform_form_payload.json`：符合 Create API schema 且显式 `settings.is_public=false` 的原生 payload。
- `typeform_form_payload.snapshot.json`：每次外部写入前的不可变 snapshot。
- `typeform_create_result.json`：form ID、HTTP 状态和 Location，不包含 token。
- `typeform_readback.json`：创建或更新后通过 GET 获取的真实表单定义。
- `typeform_readback_diff.json`：预期 payload 与 read-back 的用户可见及逻辑差异。
- `typeform_id_mapping.json`：稳定 field/choice refs 到 Typeform IDs 和 display URL 的映射。
- `typeform_instrument_identity.json`：把 form ID、payload/read-back 文件摘要、规范化内容指纹和稳定 refs 绑定为同一测试版本。
- `typeform_test_path_plan.json`：由实际 payload 自动生成的 Logic Jump assignments、最小充分 renderer cases、行为检查和覆盖映射。
- `typeform_logic_test_matrix.md`：最短、典型、最长及每条 Logic Jump 的 preview/mobile 测试，并覆盖 Other/None、同页多题、图片呈现和分析映射等实际使用能力。
- `typeform_renderer_results.json`：按 path `case_id` 保存实际题序、结束页、设备和证据引用，并绑定 instrument 指纹。
- `typeform_response_expectations.json`、`typeform_test_responses.json`、`typeform_response_verification.json`：经授权提交测试答卷后，用稳定 refs 验证答卷编码；原始答卷不得进入 skill 仓库。
- `typeform_evidence_manifest.json`：仅在 `publication_mode=agent_publish` 时声明风险等级、gate 证据、移动端/response requirement、测试数据生命周期和独立 owner 发布确认。
- `typeform_launch_gate.json`：质量、读回、逻辑、移动端测试和 owner 发布确认的机器门禁。
- `typeform_publish_result.json`：owner 明确批准发布后，`is_public` 与公开链接的读回结果。

具体步骤、整表 PUT 风险和命令见 `references/typeform-execution-contract.md`。创建私有草稿、上传图片、替换草稿、提交/删除测试答卷、启用 webhook 与公开发布分别属于外部状态变化；必须逐次遵守 owner gate。Typeform payload 检查通过只证明引用和基本结构一致；API 接受、read-back、真实 renderer 和响应编码必须分层留证，不能互相替代。仅 agent publish 强制由确定性脚本生成 launch gate 并在发布时复核证据；manual handoff/private draft 不手填 pass，也不宣称 launch-ready。

## Audience Sync 产物契约

若项目另行安装并选择 `mailchimp-audience-sync` Skill，它负责把项目 raw pool / exclusion list 转成已验证的 Mailchimp segment，供 campaign draft 使用；本家族不捆绑该执行器。

必需产物：

- `audience_sync_spec.json`：项目、cohort、raw pool、exclusion、Mailchimp audience 和 safety gate 输入。
- `target_contacts.csv`：最终目标触达名单。
- `audit_contacts.csv`：圈选、排除、去重和 overflow 审计明细。
- `audience_build_result.json`：本地圈人结果。
- `mailchimp_preflight_result.json`：Mailchimp contact status 检查结果。
- `contact_upsert_results.csv`：联系人同步结果。
- `segment_id_mapping.json`：cohort 到 Mailchimp `audience_id` / `segment_id` 的映射。
- `verify_result.json`：segment read-back 校验结果。

`segment_id_mapping.json` 最小契约：

```json
{
  "generated_at": "ISO-8601",
  "project_code": "string",
  "audience_id": "string",
  "segments": [
    {
      "cohort_key": "string",
      "business_label": "string",
      "audience_id": "string",
      "segment_name": "string",
      "segment_id": "string",
      "member_count": 0,
      "target_member_count": 0,
      "landing_url": "https://..."
    }
  ]
}
```

正常顺序是 `build` → owner 确认 → `preflight` → owner 确认 → `apply --confirm-apply` → `verify`。任何 blocked contacts、unexpected segment members 或 read-back mismatch 都必须阻断进入 campaign draft。

## Campaign Spec 最小契约

为兼容现有 `mailchimp-research-campaign` skill，字段名暂保留为 `form_url`；其值必须是已通过 owner gate 的目标平台正式回答链接，不得填 preview、design、未发布或未打开回答的地址。

```json
{
  "campaign_name": "string",
  "cohort_key": "string",
  "audience_id": "string",
  "segment_id": "string or null",
  "form_url": "https://...",
  "from_name": "string",
  "reply_to": "email@example.com",
  "subject": "string",
  "preview_text": "string",
  "html_body": "string",
  "plain_text_body": "string",
  "merge_tags_used": [],
  "test_recipients": [],
  "send_policy": "draft_and_test_only"
}
```

## Mailchimp Campaign 产物契约

若项目另行安装并选择 `mailchimp-research-campaign` Skill，它必须先把 `campaign_spec.json` 转成可检查的本地计划，再创建真实 Mailchimp draft；本家族不捆绑该执行器。

- `campaign_spec.snapshot.json`：执行前保存的 spec 快照。
- `mailchimp_campaign_plan.json`：本地校验结果、计划调用的 API、draft payload 和安全边界。
- `campaign_id_mapping.json`：cohort 到 Mailchimp campaign ID、audience、segment、test 状态的映射；未创建时 `campaign_id` 为 null。
- `mailchimp_readback_diff.json`：Mailchimp campaign/content read-back 与 spec 的差异。
- `mailchimp_preflight_report.md`：给 owner 审核的 preflight、diff、test email 和 send checklist 报告。
- `mailchimp_manual_setup_checklist.md`：credentials 缺失或 API 阻断时的人工建 campaign checklist。

正常顺序是 `build` → owner 确认 → `draft` → owner 确认 → `test` → owner 审核 test result / checklist。MVP 不支持正式 send / schedule。

## Monitor Status 最小契约

```json
{
  "snapshot_time": "ISO-8601",
  "report_readiness": "ready | not_ready | blocked",
  "sample_sufficiency": "met | underfilled | uneven | unknown",
  "cohorts": [
    {
      "cohort_key": "string",
      "business_label": "string",
      "raw_n": 0,
      "valid_n_conservative": 0,
      "valid_n_strict": 0,
      "minimum_valid_n": 0,
      "status": "ready | underfilled | quality_risk | blocked"
    }
  ],
  "quality_risks": [],
  "followup_recommendation_inputs": {}
}
```

`user-research-monitor` 在 workflow 中必须把这份文件写到 `monitoring/monitor_status.json`。如果 monitor 的业务分析产物位于数据快照目录，也要复制或生成一份 workflow 入口文件到 `monitoring/findings_data.json`。

## Workflow Dry Run 契约

每次生成或修改 `monitor_status.json`、`followup_strategy.json`、`sample-status.md` 或 `research-report.md` 后，运行：

```bash
node skills/user-research/survey-research-workflow/scripts/workflow_dry_run.js check <project_dir> --out-dir <project_dir>/workflow-dry-run
```

`workflow_dry_run_status.json` 输出：

```json
{
  "overall_status": "pass | needs_action | block",
  "blockers": [],
  "warnings": [],
  "next_actions": [],
  "summary": {
    "report_readiness": "ready | not_ready | blocked | missing",
    "followup_route": "revise_survey | revise_email | sample_action | sample_status_memo | null",
    "sample_action": "wait | resend | expand_audience | split_new_wave | null",
    "report_gate": "ready_for_formal_report | formal_report_present | formal_report_blocked | sample_status_needed | sample_status_present | waiting_for_monitor_status"
  }
}
```

`block` 表示不得继续写正式报告或执行外部动作。`needs_action` 表示下一步需要 owner、agent 或 skill 处理，但没有违反硬 gate。

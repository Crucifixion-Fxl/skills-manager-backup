# Google Forms 执行契约

本文件规定统一 `survey_spec.json` 到 Google Forms 的 lowering、创建、读回、测试与发布边界。实际 API 操作需要另行安装的 `google-forms` Skill；本家族未捆绑该执行器，缺失时只交付人工操作与验证清单。

## 官方依据

- Forms API：https://developers.google.com/workspace/forms/api/reference/rest
- Create form：https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/create
- Batch update：https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/batchUpdate
- Form / Item / Question / Image schema：https://developers.google.com/workspace/forms/api/reference/rest/v1/forms
- Publish settings：https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/setPublishSettings
- Responses list：https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses/list

官方 schema 说明可能能力；目标 OAuth identity、Cloud project quota、真实 API、read-back、renderer 和测试答卷共同证明项目可用性。

## 1. Preflight

记录目标 Google account、Cloud project、OAuth scopes、quota、执行接口和以下能力：

- `forms.body`、`forms.responses.readonly` 及需要时的 Drive 权限；
- 题型、section/page break、choice routing、images、shuffle、publishSettings；
- 邮箱收集、匿名性、响应编辑、重复作答和文件权限等 collector/设置需求；
- API 无法创建的题型或 UI-only 设置；
- 实际使用的每项能力证据层级。

核心能力为 unknown/unavailable 时阻断或重设计，不凭 Google Forms UI 的一般印象猜测 API 支持。

## 2. 统一 spec 与 adapter

执行：

```bash
node <google-forms-skill>/scripts/form_spec_adapter.js build <project>/research-design/survey_spec.json --out-dir <project>/survey-build
```

脚本名保留旧称，但 canonical input 是 `survey_spec.json` 的 `pages[].questions[]`。旧顶层 `questions[]` 仅兼容读取并产生迁移 warning；不得同时维护 `form_spec.json`。

adapter 把第二页起的 page 转为 PageBreakItem，保留稳定 IDs，并输出 `google_forms_build_plan.json`、preflight、人工说明和 mapping template。Google 不可表达的逻辑、局部固定选项或题型必须成为 blocker，不能静默近似。

保存 `read_form(raw_json=true)` 的完整结果后，运行 adapter 的 `readback` 命令，生成 `google_forms_readback_diff.json` 与 `google_forms_id_mapping.json`。首次 diff 可因 routing 尚未回填而 block；完成二阶段 routing 后重新读回，最终 diff 必须 pass。

## 3. 创建与批量写入

- Create API 只用于标题/document title，且必须显式 `unpublished=true`；省略该参数会默认发布并接受回答。
- 题目、说明、page breaks、图片和设置通过后续 batchUpdate 创建。
- 写入前 `read_form(raw_json=true)` 取得 revisionId；批量更新传 `requiredRevisionId`，若版本已变化则重新读回和审核。
- section routing 在所有 items 创建并取得真实 itemId 后二阶段回填。路由只用于平台支持的单选/下拉 choice navigation，不能把复杂条件伪装成 option jump。

## 4. 图片与随机化

- 新图使用匿名可访问的 HTTPS `sourceUri`，并提供中性 alt text；Google 抓取后 read-back 通常返回临时 `contentUri`，不能拿临时 URL 当永久刺激主键。
- 题干图和选项图都要在真实 renderer 检查尺寸、裁切和移动端可读性；细节任务遵守 `visual-stimulus-and-randomization.md`。
- `choiceQuestion.shuffle=true` 随机全部普通答案选项；原生 Other 可保持语义特殊。若需要随机普通项同时固定自定义 None/Not sure 等选项，当前 adapter 阻断并要求改为固定顺序或其他等价设计。
- Google 的 answer shuffle 不等于问题顺序、图内刺激位置、概念顺序或 respondent assignment。

## 5. Read-back、renderer 与响应

创建和每次更新后保存完整 raw JSON，建立 stable page/question/option ID 到 itemId/questionId/answer value 的映射。semantic diff 至少检查标题、说明、题序、page breaks、required、选项、shuffle、图片、routing 和 publish state。

按共同契约的风险等级测试：B 级检查受影响 renderer 与代表路径；C/D 级才根据实际使用能力扩大逻辑、移动端和答案编码验证。经单独授权提交最少量、带非个人 test_case 标记的答卷后，用 Responses API 验证 questionId、choice value 与 option mapping，并记录窄检索窗口、分析排除、保留期与 cleanup status。没有 response-risk 时不得为流程完整强制提交答卷。

## 6. 发布与停止收集

发布是独立外部动作。publication_mode=agent_publish 时，仅在该风险等级要求的质量、preflight、read-back、renderer、逻辑和必要测试答卷通过且 owner 明确确认 formId 后调用 set_publish_settings。manual_handoff 不由 agent 执行发布，也不要求伪造机器 launch gate：

- 发布并收集：`isPublished=true`、`isAcceptingResponses=true`；
- 停止发布：两者均为 false；
- 不允许 unpublished 但 accepting responses。

旧表单可能没有 publishSettings；先 read back，再决定可否使用 API。创建成功或拿到 responderUri 不等于获准分发。

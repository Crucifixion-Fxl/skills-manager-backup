# Typeform 执行契约

本文件规定从已批准的统一 `survey_spec.json` lowering 到 Typeform 私有草稿、微调、读回、测试与公开发布的边界。先遵守 `platform-execution-common-contract.md`；本文件只补充 Typeform 特有限制。默认执行路径是官方 Create API；若未来存在已授权的 Typeform MCP/connector，仍须遵守相同的 preflight、read-back 与发布门禁。

## 官方依据

- API 入门与限流：https://www.typeform.com/developers/get-started/
- Create API：https://www.typeform.com/developers/create/
- Create form：https://www.typeform.com/developers/create/reference/create-form/
- Retrieve form：https://www.typeform.com/developers/create/reference/retrieve-form/
- Update form（整表覆盖）：https://www.typeform.com/developers/create/reference/update-form/
- Update form（有限 PATCH）：https://www.typeform.com/developers/create/reference/update-form-patch/
- Logic Jumps：https://www.typeform.com/developers/create/logic-jumps/
- Create image：https://www.typeform.com/developers/create/reference/create-image/
- Picture Choice：https://help.typeform.com/hc/en-us/articles/360052865591-Picture-Choice-question
- Multi-Question Page：https://help.typeform.com/hc/en-us/articles/38099463383188-How-to-add-multiple-questions-to-a-form-page
- 发布与 draft/live：https://help.typeform.com/hc/en-us/articles/360057454351-Editing-a-form-that-s-already-live-and-collecting-responses
- Webhooks：https://www.typeform.com/developers/webhooks/

文档说明平台可能支持什么；当前账户、套餐、region、token scopes、实际 API 响应、创建后的 read-back、真实 renderer 和答卷数据共同决定项目可用性。未写入公开 schema 但经隔离探针发现的字段只能作为候选能力，不能直接推广到其他账户或长期版本。

## 0. 支持目标与非目标

目标是让 agent 在 owner 已批准研究设计后完成：

1. 生成平台中立 `survey_spec.json`；
2. 生成并本地校验 Typeform 原生 `typeform_form_payload.json`；
3. 经授权创建 `is_public=false` 的私有草稿；
4. 读回并比较字段、选项、required、随机化、逻辑和完成页；
5. 在私有草稿阶段进行受控微调；
6. 完成与变更风险相称的桌面/移动端 renderer、代表路径及必要的测试答卷验证，并获 owner 发布确认后，将表单设为公开。

本 skill 不把 API 成功等同于研究质量通过，也不把 `is_public=true` 等同于已完成分发。邮件发送、名单导入、webhook 启用和响应数据处理分别受自己的授权与隐私门禁控制。

## 1. Capability preflight

执行前建立 `typeform_capability_check.md`，至少记录：

- account、workspace、API region 与操作者；
- `TYPEFORM_ACCESS_TOKEN` 是否存在，但不得输出 token 值；
- `forms:write`、`forms:read`，上传或读图时的 `images:write` / `images:read`，以及需要时的 webhook/response scopes；
- 所需题型是否出现在当前 Create API schema；
- Logic Jumps、Hidden Fields、choice randomization、刺激/题目/版本随机化、Other/None、Multi-Question Page、图片、矩阵、文件上传、主题和 webhook 的需要与可用状态；
- 每项套餐依赖及当前账户验证状态；
- API base URL：仅允许官方 `api.typeform.com`、`api.eu.typeform.com` 或 `api.typeform.eu`；
- API 限流预算：Create/Responses API 每账号每秒最多 2 次请求；
- 预览、移动端、路径和测试答卷由谁完成、证据保存在哪里。

availability 使用 `verified_available / unavailable / unknown / not_needed`，并单独记录 evidence level：

```text
documented → api_accepted → readback_verified → renderer_verified → response_verified
```

证据只可向已实际完成的层级声明。API 201/204 不证明渲染正确，read-back 不证明交互、路径或响应编码正确。核心能力为 `unknown` 或 `unavailable` 时，`preflight_status=block`，除非问卷已重设计并消除依赖。

### 隔离 capability probe

确有必要验证未公开或账户相关能力时，可以在 owner 明确授权后创建隔离私有探针：

- 标题明确包含 capability probe，显式 `is_public=false`；
- 使用无敏感信息的假文案和测试图片；
- 保存 payload snapshot、API 结果、read-back 与 diff；
- 未获得新的发布确认前不设为公开，不提交测试答卷；
- 测试成功只更新当前账户/region/日期的 capability evidence，不把未公开能力写成普遍保证。

## 2. Native payload contract

- Typeform payload 必须从 canonical `research-design/survey_spec.json` lowering；原生 payload 是执行产物，不是第二份问卷设计。记录 page 到 step/inline group、question/option ID 到 ref 的映射，以及所有不可无损转换。
`typeform_form_payload.json` 直接遵循 Typeform Create API schema，并满足以下附加约束：

- 首次创建必须显式设置 `settings.is_public=false`；API 默认值为 true，不能依赖默认值。
- 每个 field、choice、welcome screen 和 thank-you screen使用稳定、可读、唯一的 `ref`；所有逻辑引用 `ref`，不引用显示序号。
- `ref` 只使用字母、数字、下划线和连字符，长度小于 255。
- required、选择数量、Other、随机化、说明和附件均显式设置；不依赖 UI 默认值支撑关键推断。
- Typeform 原生 `randomize=true` 只随机支持题型的答案选项；它不随机题目、page、Question Group、合成图内部刺激、概念卡顺序，也不把受访者随机分到不同版本。
- 自动化操作随机化多选题时，每次选择后都必须重新读取当前 DOM/checked 状态，再按稳定文案或已观察到的稳定 locator 选择下一项；不得缓存初始位置、快捷键字母或连续复用旧 locator。Typeform 可能在选择后重新渲染或重排选项，提交前必须核对最终已选集合。
- 原生 Other 与 None 应在 renderer 中验证固定位置与互斥交互。若研究需要将自定义 `Don't know / Not applicable` 等固定在末尾，必须先验证 API/UI 是否能同时满足；不能验证时，优先使用不随机或研究上等价的设计，不能只为微小顺序风险给所有人增加重复题。
- 真正的同页多题使用 `inline_group`；`group` 是 Question Group，受访者仍逐题作答。`inline_group` 虽已在一次 2026-09-08 REST 私有探针中被接受并读回，但未出现在当时公开 Create schema；每个目标账户仍需 capability evidence。
- `inline_group` 的 child type 受限；禁止把 `payment`、`group` 或另一 `inline_group` 嵌套其中。每个 child 保留独立 ref 与 required。
- 图片必须先存在于 Typeform 账户；Create API 不能注入任意第三方内容。
- 非装饰图片必须有中性 alt text。Picture Choice 即使隐藏可见 label，也必须保留稳定 label/ref 用于无障碍和结果映射。
- payload 中不得包含 access token、受访者名单或其他密钥。

## 3. Layout、视觉刺激与随机化

### Group 与同页多题

- `group`：逻辑分组/umbrella，默认逐题显示；不能因 API 中 child fields 嵌套就称为同页多题。
- `inline_group`：Multi-Question Page；API 接受和 read-back 后仍须在真实 renderer 确认多个 child 同处一个 fieldset/page。
- 在 Builder 中把 `group` 转成 Multi-Question Page 可能重建 container/child IDs 与 refs、修改标题或 required、移除按钮属性。任何 UI 结构转换后必须重新 GET，并回归 required、逻辑、集成和分析映射。
- 已发布表单的 UI 编辑先进入 draft；只有点击 **Publish edits** 后才进入 live。比较 API/live 前先记录当前版本状态，不能把 draft preview 与公开链接混用。

### 图片与概念测试

上传图片使用：

```bash
node scripts/typeform_api.js upload-image <image.png> --out <image-result.json>
```

图片创建是账户外部写入，需要 owner 对目标账户和文件确认。上传结果保存原文件名和 SHA-256；返回的 Typeform image URL 再写入 question attachment 或 Picture Choice choice attachment。

- Picture Choice 适合判断整体风格；`supersized` 只是较大缩略图，不提供点击放大/zoom。需要读取细节时改用题干大图、全尺寸链接、逐概念展示或原型。
- 少量方案需要并排比较时，可把方案合成一张标注清楚的大图，作为普通选择题 attachment；答案顺序与图中顺序保持一致。
- 合成图内部位置不能由 Typeform 原生随机。两个方案至少准备 AB/BA；更多方案使用平衡排列，并由外部 allocator、名单分组或 URL hidden field + branching 分配。
- 随机答案文字不能消除视觉位置偏差。分析必须按 concept ID，而不是 A/B/C 位置合并。
- API 建卷时 Typeform 可能复制已存在图片并返回新的 attachment URL。diff 将其记录为 `typeform_image_copy` equivalence；renderer 仍须确认内容、裁切和可读性。

视觉研究的通用设计规则读取 `visual-stimulus-and-randomization.md`。

## 4. Logic Jumps

- 每个 field 最多一个 Logic Jump definition；其 `actions` 按顺序判断，第一个满足的条件生效。
- 更具体、更严格的条件排在一般条件之前；为多选组合设计的条件要排在单项条件之前。
- 所有触发 field、choice、hidden variable、目标 field 和 thank-you `ref` 必须存在。
- 本 workflow 默认只允许向后跳转。若业务确需其他结构，必须证明不会形成循环并单独测试。
- 每个分支尾部要有显式跳转或可证明的自然汇合点，避免受访者继续进入不相关分支。
- 复杂 AND/OR 条件不能只靠肉眼阅读 payload；必须为每条条件的 true/false 建立测试路径。
- “隐藏后题某个答案选项”若缺少可验证 API 表示，可在条件简单时建立两个题目版本并 branching 到其中一个，再显式汇合。两个版本除目标选项外保持题干、选项 ID 语义和顺序策略一致；分析层预先定义合并映射。
- 重复题 fallback 只减少 respondent burden，不减少 build/QA/analysis burden。若条件或需隐藏选项很多，会发生分支爆炸；此时改用可用的原生能力、逐项状态题、外部表单或 owner 确认的其他架构。

## 5. 创建、读回与差异检查

使用 `scripts/typeform_api.js`：

```bash
node scripts/typeform_api.js check <typeform_form_payload.json>
node scripts/typeform_api.js create-private <payload.json> --out-dir <dir>
node scripts/typeform_api.js get <form_id> --out <readback.json>
node scripts/typeform_api.js diff <payload.json> <readback.json> --out <diff.json>
```

read-back 后生成版本身份文件：

```bash
node scripts/survey_preflight.js verify-typeform-version <payload.json> <readback.json> --form-id <form_id> --out <typeform_instrument_identity.json>
```

该文件将 payload、read-back、form ID、稳定 refs 和规范化内容 SHA-256 绑定。`is_public` 作为外部状态单独记录，不进入内容指纹，因此私有草稿和内容相同的公开版本可保持同一 instrument version。任何题目、选项、逻辑或可见设置变更都会使旧路径、renderer 和答卷证据失效。

创建私有草稿属于外部写操作，必须在调用前获得 owner 对目标账户/workspace 和 payload snapshot 的确认。创建后必须保存：

- `typeform_form_payload.snapshot.json`
- `typeform_create_result.json`
- `typeform_readback.json`
- `typeform_readback_diff.json`
- `typeform_id_mapping.json`

API 成功但 read-back 有用户可见文案、题序、required、选项、随机化、逻辑或完成页偏差时，状态仍为 `block`。以下规范化只解决已观察到的序列化差异，不替代 renderer：

- 预期空数组而 API 省略该字段，可视为等价；
- Typeform 把账户图片复制为新 attachment URL 时，记录 `typeform_image_copy` 映射；
- 其他附件变化、图片属性缺失或 URL 非 Typeform image resource 仍阻断。

## 6. 微调与覆盖风险

Typeform `PUT /forms/{form_id}` 会用提交内容覆盖整份表单。遗漏已有 field 可能删除该 field 及其历史结果，因此：

- 内容、题序、选项或逻辑微调只自动应用于 `is_public=false` 的私有草稿；
- 更新前必须 GET 当前表单并保存不可变备份；
- 替换命令要求显式重复确认 form ID；
- payload 必须包含所有要保留的 field，并尽量保留 read-back 中已有的 field/choice IDs；
- 已公开或已收集真实答卷的表单，默认不使用整表 PUT。创建新版本或由 owner 明确接受迁移/数据风险；
- `PATCH` 只用于官方允许的有限路径，例如 title、workspace、theme 和 `settings.is_public`，不能假设它能修改任意题目。

```bash
node scripts/typeform_api.js replace-private <form_id> <payload.json> --confirm-form-id <form_id> --out-dir <dir>
```

## 7. Preview 与发布 gate

Typeform API read-back 不能证明真实交互路径正确。先生成机器路径计划：

```bash
node scripts/survey_preflight.js generate-typeform-paths <typeform_form_payload.json> --out <typeform_test_path_plan.json>
```

脚本为受支持的 Logic Jump 条件求解命中和 fallthrough assignments，再用确定性 greedy set cover 生成覆盖全部可达逻辑行为的较小候选路径集。它不等于每次运行时都必须执行全部候选路径：按共同契约分级，B 级做版本/read-back 与受影响页面检查；C 级执行受影响行为的代表路径并在编码风险存在时提交最少测试答卷；D 级再扩大覆盖。无法求解的操作符、hidden allocation、shadowed 或不可达条件必须阻断并人工补充设计事实，不能静默略过。所选 typeform_logic_test_matrix.md 或结构化 renderer result 按本次风险覆盖：

- 最短、典型和最长路径；
- 每个 screening、提前结束和分支条件的 true/false；
- 多选组合中更严格条件是否优先；
- required、min/max selection、Other、随机化和完成页；
- Other/None 固定位置、互斥行为和 Other 文本结果；
- `group` 与 `inline_group` 的真实页结构；
- 图片尺寸、裁切、alt text、标签、放大限制和刺激/答案映射；
- 手机宽度下题干、选项换行、附件、Picture Choice、inline group 和返回导航；
- 所有问题是否可达，是否出现循环、错误分支或无关问题。

renderer 结果使用 `assets/templates/typeform-renderer-results.json`，每条 run 引用路径计划中的 `case_id`，并记录相同的 `form_id` 与 `instrument_sha256`。执行质量检查时保存派生结果：

```bash
node scripts/survey_quality_gate_check.js check <survey_quality_gate.json> --out <survey_quality_gate_result.json>
```

只有 publication_mode=agent_publish 时，将上述证据路径填入 assets/templates/typeform-evidence-manifest.json 并生成 launch gate。manual_handoff/private_draft 使用 read-back、renderer 结果与未执行项清单，不要求发布级哈希 gate：

```bash
node scripts/survey_preflight.js build-typeform-gate <typeform_evidence_manifest.json> --out <typeform_launch_gate.json>
```

只有生成的 gate 为 pass 并获得 owner 明确发布确认后，才可以由 agent 执行发布。不得复制 typeform-launch-gate.json 后手填 pass。发布命令会重新计算所有引用证据的文件 SHA-256；任何证据被修改后 gate 自动失效：

```bash
node scripts/typeform_api.js publish <form_id> --confirm-form-id <form_id> --launch-gate <typeform_launch_gate.json> --out <publish-result.json>
```

其中 survey quality、read-back diff 和 logic test 均须为 `pass`；只有 manifest 明确设置 `require_mobile=true` 时 mobile test 才是硬门槛，项目要求 response verification 时也必须为 `pass`。blockers 为空，并记录 owner、确认状态和时间。发布是独立的外部状态变化；创建草稿或批准文案不自动授权发布。

## 8. Webhook 与数据交接

Webhook 创建和启用是独立外部操作；URL 必须使用 HTTPS，建议设置 secret 并在接收端验证 HMAC。不得把 webhook secret 写入项目文件或日志。

没有 webhook 时可使用 Responses API 或不可变导出。保留 form ID、field/choice refs、response status、导出时间、timezone 与测试答卷处理规则。稳定 `ref` 是分析映射主键；不要只依赖可变化的题号或完整题干。

经授权提交测试答卷后，用窄时间窗拉取并本地核验：

```bash
node scripts/typeform_api.js responses <form_id> --confirm-form-id <form_id> --since <ISO-8601> --until <ISO-8601> --response-type completed --out <typeform_test_responses.json>
node scripts/survey_preflight.js verify-typeform-responses <typeform_response_expectations.json> <typeform_test_responses.json> --identity <typeform_instrument_identity.json> --out <typeform_response_verification.json>
```

若表单原本私有且 private preview 不写入 Responses API，使用测试事务而非正式 `publish`：

```bash
node scripts/typeform_api.js begin-response-test <form_id> --confirm-form-id <form_id> --payload <payload.json> --identity <identity.json> --out-dir <survey-build>
# 在公开链接提交最少量、带 test_case hidden field 的测试答卷并完成 Responses 验证
node scripts/typeform_api.js end-response-test <survey-build/typeform_response_test_transaction.json> --confirm-form-id <form_id> --out-dir <survey-build>
```

`begin-response-test` 会在改状态前保存 read-back 与原 `is_public`，并拒绝与 identity 不一致的表单；`end-response-test` 恢复原状态并读回确认。后续任何步骤失败也必须在同一执行轮次尝试恢复。本事务只覆盖一次明确授权的测试窗口，不授予正式发布、分发或保留测试答卷的权限。

expectations 使用 test hidden field、response token、response ID，或现有开放文本/Other 中不含个人信息的唯一测试标记来匹配测试答卷，并按稳定 field/choice ref 声明 present、absent、equals 或 contains_all。为增加隐藏字段而需要整表替换、但唯一答案标记已足够时，不扩大表单修改风险。匹配不到、匹配多份、ref 不存在、答案或版本不一致均阻断。Responses 文件可能包含个人数据，应限制时间窗、保存位置和保留期；记录正式分析排除规则与 cleanup_status。删除答卷需要独立授权和平台支持；无法删除时保留可审计排除规则。不得把原始答卷提交到 skill 仓库。

Typeform 官方说明非常新的答卷可能约 30 分钟内尚未出现在 Responses API。首次匹配不到时记录 `pending_api_visibility`，稍后以同一窄时间窗重试，或在事先获准并配置安全接收端时使用 webhook；不得把暂时不可见误判成编码失败，也不得无限轮询。

正式上线前用明确标记的测试答卷验证实际使用题型，至少覆盖 multi-select、Other、None、inline group、图片题和重复题 branching；测试答卷的提交与清理由 owner 分别授权。UI 转换导致 refs 变化时，旧映射作废并重新生成，不允许靠题干模糊匹配。

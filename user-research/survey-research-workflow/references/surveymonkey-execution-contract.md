# SurveyMonkey 执行契约

本文件规定从已批准的统一 `survey_spec.json` 到 SurveyMonkey draft、collector 和数据交接的边界。先遵守 `platform-execution-common-contract.md`；本文件只补充 SurveyMonkey 特有限制。它不是 SurveyMonkey API payload 说明；不得猜测 API 字段或假设当前账户拥有某项付费能力。

## 官方依据

- Question Skip Logic：https://help.surveymonkey.com/en/surveymonkey/create/question-skip-logic/
- Advanced Branching：https://help.surveymonkey.com/en/surveymonkey/create/advanced-branching/
- Question & Page Randomization：https://help.surveymonkey.com/en/surveymonkey/create/question-page-randomization/
- Block Randomization：https://help.surveymonkey.com/en/surveymonkey/create/block-randomization/
- Collector Options：https://help.surveymonkey.com/en/surveymonkey/send/collector-options/
- Anonymous Responses：https://help.surveymonkey.com/en/surveymonkey/send/anonymous-responses/
- Web Link Collector：https://help.surveymonkey.com/en/surveymonkey/send/web-link-collector/

官方文档用于识别可能存在的能力和约束；实际账户 UI、已授权 connector/API 的 read-only 探测与创建后的 read-back 才能证明项目可用性。

## 0. Wording 前的逻辑可行性检查

在正式题干和页面结构定稿前，先列出每个 routing 需求、eligible cohort、page-break、所需 SurveyMonkey 能力和验证状态。这个早期检查用于约束设计；后续账户 preflight 与 read-back 仍然必须执行。

关键能力为 `unknown` 或 `unavailable` 时，只能选择：验证能力、重设计逻辑、缩小适用 cohort、拆模块/分波次，或把有明确增量 burden 的 workaround 交 owner 选择。不得默认把一项本可由 routing 限定的问题改成全员重复回答，也不得用平台限制为 respondent-perceived duplication 辩护。任何增加 exposed population、题数、时长或 conceptual switch 的 workaround 都写入 `survey_quality_gate.json` 并单独确认 burden。

## 1. 执行前能力预检

先记录：

- workspace / account 与操作者权限。
- 套餐和 feature availability；未知时不得根据套餐名称猜测。
- 可用执行路径：官方 connector、API、人工 UI 或都不可用。
- 目标 collector 类型及其可用 options。
- 是否需要 Advanced Branching、same-page logic、piping、A/B、question/page/block randomization、custom variables 或 quotas。
- 数据获取方式：API、CSV export 或尚未确定。

每项状态只能是：

- `verified_available`：在当前账户和执行路径中已验证。
- `unavailable`：当前账户、套餐、权限或执行路径明确不可用。
- `unknown`：尚未验证。
- `not_needed`：本项目不需要。

关键能力为 `unknown` 或 `unavailable` 时，`preflight_status` 必须为 `block`，除非问卷已重设计并消除依赖。

`surveymonkey_capability_check.md` 对每项至少记录 `requirement`、`product_documentation`、`current_account_status`、`execution_interface_status`、`readback_status`、`impact_if_missing` 和 `resolution`。产品文档显示可能支持，但后三项尚未验证时，不能记为 pass。

## 2. 逻辑与随机化约束

- 普通 Question Skip Logic 从一个 closed-ended answer 向后续问题/页面、问卷结束或 disqualify 跳转；必须向前跳，触发题与目标之间需要 page break。
- 同一页面不要放多个需要不同去向的 Question Skip Logic 触发题；多选题若多个所选答案对应不同路径，也会产生歧义。需要多条件或 show/hide 时，先验证 Advanced Branching。
- 先稳定 pages、questions 和 choices，再配置高级逻辑，避免结构修改造成返工或冲突。
- Question/Page Randomization 可能与 skip logic 或 piping 产生意外行为；Block Randomization 也有组合限制。任何组合使用都必须列出兼容性检查和实际 preview 结果。
- 不能把“平台支持随机化”当成“研究上应该随机化”。筛选、时间顺序、因果链、量表锚点、`None/Other` 等有语义位置的内容保持固定。

## 3. Build plan

SurveyMonkey build plan 必须从 canonical `research-design/survey_spec.json` lowering；不得复制并维护另一份平台专属问卷文案。每次 lowering 记录不支持、近似转换和需 owner 决策的差异。

`surveymonkey_build_plan.json` 至少包含：

```json
{
  "source_spec": "research-design/survey_spec.json",
  "preflight_status": "pass | warn | block",
  "execution_mode": "connector | api | manual",
  "capabilities": [],
  "survey": {},
  "pages": [],
  "questions": [],
  "logic": [],
  "randomization": [],
  "collector_plan": {},
  "blockers": [],
  "warnings": []
}
```

人工模式必须生成逐页 instructions，包含题型、required、choices、validation、logic、randomization 和 collector requirements。人工操作完成前，ID mapping 和 collector URL 保持 null。

## 4. 创建与 read-back

创建 live draft 属于外部写操作，必须在 owner 批准 preflight 后进行。执行时：

1. 保存 `survey_spec.json` snapshot。
2. 先创建 survey/pages/questions；结构稳定后再配置 logic/randomization。
3. read back survey、pages、questions、choices 和可读取的逻辑设置。
4. 写入 `surveymonkey_id_mapping.json` 和 `surveymonkey_readback_diff.json`。
5. diff 存在用户可见文案、required、choice、page order、logic 或 randomization 偏差时阻断发布。

没有可用 connector/API 时，不得通过浏览器自动化绕过权限或声称 read-back 已完成；仅提供人工建卷与核对清单，等待用户明确要求并授权下一步。

## 5. Collector 是独立 gate

survey draft、collector 创建、collector 打开和发送链接是不同状态。每个 collector 单独确认：

- collector type 与 cohort/波次用途。
- 匿名性及其对身份信息、custom data、custom variables 的影响。
- 是否允许 multiple responses，以及去重依赖 cookie、email 或 unique variable 的局限。
- response editing、cutoff、response limit、disqualification 与 end page。
- Mailchimp CTA 使用的必须是 owner 批准的 collector URL。

默认值是 `collector_status: planned`。没有 owner 对隐私、样本识别和重复作答策略的确认，不得打开 collector。

## 6. Preview 与 logic test matrix

SurveyMonkey preview 不等于浏览一遍。根据共同契约的风险等级选择代表性行为；B 级只覆盖受影响路径，C/D 级扩大到本次问卷实际使用的不同逻辑、交互和编码类别。surveymonkey_logic_test_matrix.md 按适用性覆盖：

- 最短、典型和最长路径。
- 每个 screening / disqualify 分支。
- 每个 skip / advanced branching 条件的真与假。
- required、validation、Other/open text、移动端换行与页面转场。
- randomization 是否保留固定选项并不破坏 logic/piping。
- introduction、首题、完成页与 privacy wording。

每条记录 expected destination、observed destination、结果与证据。相同页面、跳转和编码行为不做答案排列组合。任何不可达页面、循环、跳过应答但仍 required、错配文案或 collector 设置错误都必须阻断发布。

## 7. 回收与导出交接

若当前 `user-research-monitor` 没有经过验证的 SurveyMonkey API ingestion，使用人工导出的 `surveymonkey_response_export.csv`：

- 保存不可变原始导出，不直接覆盖。
- 在 artifact index 记录 export time、survey ID、collector ID、response status/filter、timezone 和导出格式。
- 建立稳定 question/choice IDs 到导出列和值的映射；不能依赖易变的题号或完整题干作为唯一键。
- 明确 complete、partial、disqualified 和 test responses 的处理。
- 多 collector / cohort 时保留 collector 标识或分别导出，不能在来源不明时合并。

测试答卷使用非个人唯一标记和窄时间窗；记录分析排除、保留/清理状态。删除需要独立授权和平台能力；无法删除时保持可审计排除规则。原始响应不得进入 skill 仓库。

CSV 手工交接只能证明“文件已导出并验证”，不能声称 API 自动监测已经可用。

# 三平台共同执行契约

仅在 `production_workflow` 使用。先确定目标平台、`publication_mode` 和风险等级，再读取对应平台契约。研究设计只有一个 source of truth：`research-design/survey_spec.json`。

## 1. 单一来源与 lowering

- `survey_spec.json` 保存受访者内容、稳定 page/question/option IDs、required、validation、logic、顺序、视觉刺激语义和 collector requirements。
- 平台 build plan/payload 由 spec lowering 得到，不得成为第二份设计源。
- 平台迫使题型、页面、逻辑、随机化或呈现变化时，记录差异；改变题意、样本可达性、estimand 或负担时回到 spec 并由 owner 决定。
- 平台切换后重新 preflight 和 lowering diff，不能沿用另一平台的 pass。

## 2. 能力与成熟度

能力状态：`verified_available | unavailable | unknown | not_needed`。证据层级：

`documented → api_accepted → readback_verified → renderer_verified → response_verified`

API 成功不证明页面显示正确；read-back 不证明分支或答案编码。至少检查题型、required/validation、页面模型、logic、Other/None、各类随机化、图片/alt text、移动端、身份/匿名设置、发布状态、response access 和 ID mapping。

当前 adapter 成熟度必须如实声明：

| 平台 | 当前执行能力 | 不得声称 |
|---|---|---|
| Typeform | 本 skill 有确定性 payload 检查、版本身份、路径计划、response 校验和发布 gate | 未经当前账户/API/renderer 实测的能力已经可用 |
| Google Forms | 通过可选外部 `google-forms` Skill 生成 adapter/build plan，并结合 Forms API/read-back；未安装时使用人工交接 | 与 Typeform 脚本完全同等的自动发布保障 |
| SurveyMonkey | 以能力契约、build plan、人工/API read-back 为主；取决于当前 connector、API、套餐和权限 | 三平台自动化已完全对等，或未知字段可以猜测 |

平台选择由研究体验、逻辑/视觉需求、账户能力、数据交接和运维成本决定，不能因为某个 adapter 更成熟而静默改变研究设计。

## 3. Publication mode 与风险等级

`publication_mode`：

- `none`：只交付设计；
- `manual_handoff`：交付建卷说明/草稿，由 owner 在平台 UI 最终复核和发布；
- `private_draft`：agent 创建或修改不接收正式回答的私有草稿；
- `agent_publish`：agent 经明确授权执行公开/开启回答。

风险等级：

- A：设计/评审，无平台证据。
- B：manual handoff、private draft 或低风险文字变化；需要 preflight、read-back/人工核对和受影响 renderer。
- C：agent publish，或逻辑、结构、随机化、复杂题型、response encoding 变化；增加 instrument identity、静态逻辑、代表路径、必要 response smoke 与确定性 gate。
- D：医疗、法律、安全、重大财务、弱势人群或不可逆高成本决定；在 C 基础上扩大真人、多设备与运行时覆盖。

只有 `publication_mode=agent_publish` 强制生成并在发布时复核证据摘要的 launch gate。Manual handoff 使用清单和 read-back，明确未执行项，不声称 launch-ready。测试路径覆盖不同行为等价类，不穷举所有答案组合。

## 4. 推荐状态链

按风险跳过不适用步骤，不制造占位文件：

1. 批准 canonical spec。
2. 完成目标账户 capability/preflight。
3. 本地 lowering；发现不等价立即回到设计。
4. 对外部创建/修改单独确认，保存写入前 snapshot。
5. 创建私有草稿并 read back，做 semantic diff。
6. 按等级执行受影响 renderer、代表路径、移动端和 response 检查。
7. `agent_publish` 从同一版本证据生成 launch gate，并单独获得发布确认。
8. read back 最终状态后才交付正式回答链接；创建、发布、打开 collector 和分发是不同动作。

任何失败停在当前可恢复状态，不因重试扩大授权。

## 5. 测试答卷生命周期

只有 response encoding 对上线结论重要且 owner 已授权时才提交：

- 使用非个人、唯一 `test_case` 标记；
- 只覆盖不同编码类别的最少答卷；
- 用窄时间窗、form/version identity 与标记检索；
- 验证后在正式分析中显式排除；
- 记录 `storage_location`、`retention_until`、`analysis_exclusion_rule`、`cleanup_status`；
- 删除是独立外部动作，需要授权且平台支持；不能删除时保留可审计排除规则；
- 原始 responses、tokens、邮箱或个人数据不得进入 skill 仓库。

建议在 evidence manifest 中写：

```json
{
  "publication_mode": "none | manual_handoff | private_draft | agent_publish",
  "risk_level": "A | B | C | D",
  "test_data_lifecycle": {
    "required": false,
    "test_case_marker": null,
    "retrieval_window": null,
    "analysis_exclusion_rule": null,
    "retention_until": null,
    "cleanup_status": "not_needed | planned | retained_excluded | deleted | blocked"
  }
}
```

## 6. 外部动作确认

以下分别确认：选择账户/workspace、创建/修改线上草稿、上传媒体、临时公开测试、提交测试答卷、删除测试数据、启用 webhook、打开 collector、发布和分发。一次确认不自动授权下一动作。

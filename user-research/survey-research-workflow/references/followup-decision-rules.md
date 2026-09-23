# Follow-Up 决策规则

监测回收后判断下一步动作时使用本参考。

## 最低输入

- `monitor_status.json`
- Campaign metrics（如有）
- 样本目标和 minimum valid n
- Cohort definitions
- 目标平台正式填写链接和当前问卷版本
- 已知发送日期和 follow-up 历史

## 瓶颈地图

| 现象 | 可能瓶颈 | 候选动作 |
|---|---|---|
| Delivery 低或 bounce 高 | List health | 清理 list，验证 audience source |
| Open rate 低 | Subject、sender、timing、list recency | 改 subject、测试发送时间、清理 segment |
| Open 正常但 click 低 | Body、CTA、value proposition | 改 body / CTA，明确时长，优化 incentive |
| Click 正常但 completion 低 | Survey friction | 缩短问卷、优化首题、检查移动端体验 |
| Raw n 足够但 valid n 低 | Quality / cohort mismatch | 复核规则、验证 cohort、加 screener、拆 wave |
| Segment 样本不足但 rate 健康 | Audience size / quota | 扩群或带 caveat 放宽 quota |
| 开放题质量低 | 动机不足或题目不清 | 优化 prompt、改为 optional、谨慎加例子 |

## 推荐状态值

- `ready_for_report`：有效样本足够，质量可接受。
- `wait`：回收仍在进行，趋势健康。
- `followup_needed`：需要更多样本或更高质量样本。
- `blocked`：设计、受众或数据问题导致无法有效分析。

## Follow-Up 选项

- 继续等待。
- 给未点击或未完成用户发送 reminder。
- 在同一 cohort 定义下扩群。
- 拆出新 cohort 或 new wave。
- 改 subject / preview。
- 改 body / CTA。
- 增加或调整 incentive，但必须先获批。
- 缩短或修复问卷。
- 冻结为方向性结果，只写 limited memo。

## Route 输出

`follow-up-strategist` 必须把候选动作收敛成一个 route：

- `revise_survey`：缩短或修复问卷、修改题目、修复跳转或移动端体验。
- `revise_email`：改 subject、preview、body、CTA 或 incentive framing。
- `sample_action`：继续等待、发送 reminder、同 cohort 扩群、拆 new wave 或调整 quota。具体是等待 / 追发 / 扩群必须由 strategist 写清楚，不能只写"去 monitor"。
- `sample_status_memo`：不建议继续 follow-up，只写 limited memo。

Workflow skill 只记录 route 和路由下一步，不负责判断等待、追发还是扩群。`user-research-monitor` 只在动作完成或等待窗口结束后复评 readiness。

## Wave 规则

以下情况需要标记为 new wave：

- 问卷发生变化。
- 激励发生变化。
- 目标用户发生实质变化。
- 邮件 hook 变化到可能影响受访者动机。

不要静默合并不同 waves。报告里必须呈现差异，或声明结果仅作为方向性参考。

## 需要 Owner 确认的决策

以下动作前必须确认：

- 任何 resend。
- 任何 audience expansion。
- 任何 incentive addition。
- 任何平台的 live survey / collector edit。
- 任何 sample readiness 重分类。
- 在 segment 样本不足情况下写报告。

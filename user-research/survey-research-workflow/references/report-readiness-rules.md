# Report Readiness 规则

写正式研究报告前使用本参考。

## Readiness 输入

- `monitor_status.json`
- 已确认或最终版 `research-brief.md`
- Question wording 和 SurveyMonkey ID / export-column mapping
- Cleaned responses 或结构化 `findings_data.json`
- 质量规则和剔除决策
- 开放题编码结果（如使用）

## 正式报告 Gate

只有满足以下条件，才写正式报告：

- `monitor_status.report_readiness` 为 `ready`。
- `workflow-dry-run/workflow_dry_run_status.json.overall_status` 不是 `block`。
- 支持主决策的最低有效样本量已达标。
- 核心 cohort definitions 清楚。
- 核心题目的 wording 可追溯。
- 剔除规则已记录。
- 主要质量风险已解决或已披露。

如果任一条件不满足，只写 sample-status memo。

如果 `monitor_status.report_readiness` 不是 `ready` 但项目里已经存在 `research-report.md`，这是 workflow blocker，必须撤回到 sample-status memo。

## 样本量默认参考

除非项目定义了更严格门槛，否则使用：

- `n < 10`：不作为 segment 分析。
- `n 10-29`：仅方向性参考。
- `n >= 30`：可用于简单描述性发现。
- `n >= 200`：更适合 cohort comparison。
- `n >= 300`：可考虑更复杂模型或 conjoint-style analysis。

## 报告规则

- 按 key ask 和业务决策组织报告。
- 主要结果必须附原始用户可见 question wording。
- 百分比必须同时给人数和分母。
- 避免从非实验性问卷里下因果结论。
- 区分 fact、interpretation 和 recommendation。
- 清楚标记 assumptions 和 unresolved questions。
- 使用 cohort business labels。
- 开头披露数据质量 caveats。
- Quote 只使用去标识化且可追溯的原文。

## Sample-Status Memo

如果数据未 ready，只写：

- 当前 sample 和 valid sample by cohort。
- 质量风险。
- 哪些 key asks 能回答，哪些不能。
- 还需要哪些数据或 owner 决策。
- 推荐 follow-up 路径。

Sample-status memo 里不要写最终 roadmap、pricing 或 packaging 建议。

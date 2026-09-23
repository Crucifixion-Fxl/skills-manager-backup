# 错误与恢复

报告原生状态、安全错误码和可用的 HTTP 状态。`queued`、`running` 表示任务尚在处理；
只有权威 `status=succeeded` 才证明完成。[宿主配置](host-configuration.md#errors-and-recovery)说明 key、路由和传输诊断。

| 情况 | 后续处理 |
| --- | --- |
| 不支持的条件 | 解释缺少的字段、操作符或边，并准备接入提案。 |
| `project_query_plan_stale`（HTTP 409） | 读取精确历史 `criteria`，验证当前能力与语义，再按[历史条件恢复](project-query.md#read-and-rebuild-historical-criteria)验证并创建新计划。criteria 为 null 时需用户提供原始条件。 |
| 预览过期 | 重新预览同一计划，使用返回的凭证与人数。 |
| binding 或 revision 不匹配 | 根据权威证据修正 Project 或目标 binding。 |
| 超时或效果未知 | 保留所选 key、原始 idempotency_key 和精确请求载荷；读取返回的精确请求 ID。 |
| `reconcile_required` / `outcome_unknown` | 报告尚未证实的结果，以及所需的 Platform 对账。 |
| 请求失败 | 报告 `safe_error_code` 和支持的修正方式。 |

重放使用同一 key 和字节等价的请求载荷；输入变化需要重新决策。
保留确切的 `plan_id`、`request_id` 和 `materialization_run_id`；不得替换为最新 run，或在执行途中切换凭据。
关闭新效果开关后，精确请求回读仍可用。

只展示返回且验证通过的成功网址。缺少网址不影响已经成功的完成结果，也不能成为自行构造替代网址的理由。

列表或发现操作失败时，说明无法读取记录。不得因端点不可用或缺少原生工具就推断没有记录。
只要 `next_cursor` 存在，即使当前页为空也继续翻页。Project 范围的同步观察可以读取其他所有者的任务，
但不允许重新提交他们的圈人计划。

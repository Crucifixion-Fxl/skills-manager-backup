# 同步确认

将确认绑定到所选 Project、圈人计划、成功物化的请求与 run、成员人数，以及公布的目标和 revision。
`sync_audience_query` 要求 JSON `confirmed=true`。

若用户现有授权覆盖这些精确输入，沿用该授权。缺少授权或输入变化时再请求确认；
只读 key 验证和状态查询不需要额外许可。

物化要求同一圈人计划的fresh `preview_attestation_id` 和 `expected_member_count`。
同步要求物化 `status=succeeded`。在线开关与精确 binding 见 [Project 圈人查询](project-query.md)。

普通确认用业务摘要表达：圈人计划名称、实际筛选条件、已知的数据快照分区、
成功物化人数和目标显示名称。快照分区不是筛选日期；人数明确标为“已物化人数”。
内部仍将这份摘要绑定到上述精确请求、run、人数、目标及 revision，不要求用户核对内部 ID，
也不在默认确认中展示 preview attestation。缺失信息保持未知，不额外调用接口补齐展示。
诊断例外与输出口径见[完成回复](project-query.md#completion-replies)。

格式示例（尖括号为说明性占位符）：

```text
将“<实际计划名称>”的已物化 <人数> 人同步到“<目标显示名称>”。
筛选条件：<实际字段/操作符/值及逻辑关系>；数据快照分区：<已知分区>。
请确认本次同步。
```

仅在缺少覆盖这些输入的现有授权时使用上述确认；已有授权仍按前述规则沿用。

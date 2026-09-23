# alert_state.json Schema

Dispatcher 每轮 cron 读写此文件，记录全局状态。

## 结构

```json
{
  "last_poll_at": "2026-03-25T10:44:00Z",
  "processed_incident_ids": ["Q1ABC123", "Q1DEF456"],
  "active_correlation_groups": {
    "CG-443258": {
      "created_at": "2026-03-25T10:44:00Z",
      "incidents": ["Q1ABC123"],
      "service": "grafana-ai",
      "environment": "cn-prod",
      "status": "investigating",
      "investigation_agent_id": null,
      "phase1_sent": true
    }
  },
  "completed_correlation_groups": {
    "CG-443258": {
      "completed_at": "2026-03-25T10:55:00Z",
      "result_path": "investigations/CG-443258/report.yaml",
      "solutions_status": {
        "0": {"status": "pending_approval", "approval_sent_at": "2026-03-25T10:55:00Z", "instance_id": "feishu_approval_instance_xxx"},
        "1": {"status": "auto_executed", "executed_at": "2026-03-25T10:56:00Z"}
      }
    }
  },
  "cg_counter": 443259
}
```

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| last_poll_at | ISO8601 | 上次 poll PagerDuty 的时间 |
| processed_incident_ids | string[] | 已处理的 PD incident ID，防止重复 |
| active_correlation_groups | object | 调查进行中的告警组 |
| completed_correlation_groups | object | 调查完成的告警组 |
| cg_counter | int | CG 编号自增计数器 |

## solutions_status 枚举

| 状态 | 含义 |
|------|------|
| pending_approval | 审批中（L4） |
| approved | 已批准，待执行 |
| rejected | 已拒绝 |
| expired | 已过期（48h） |
| auto_executed | L5 自动执行 |
| executing | 执行中 |
| executed_success | 执行成功 |
| executed_failure | 执行失败 |
| pattern_extracted | 已提取 AR pattern |

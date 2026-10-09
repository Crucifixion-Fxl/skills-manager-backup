# 输出契约

```json
{
  "schema": "test-failure-triage/v1",
  "window": {"from": "UTC instant", "to": "UTC instant", "timezone": "UTC"},
  "coverage": {"plans_complete": true, "jobs_complete": true, "logs_truncated": false,
               "recovered_attempts_complete": false, "missing_evidence": [], "sampled": false},
  "incidents": [{
    "run_key": "environment/job/scenario/attempt",
    "run_kind": "GENERATION|EXECUTION|UNKNOWN",
    "failure_stage": "GENERATION|EXECUTION|INFRASTRUCTURE|UNKNOWN",
    "result": "failed|recovered|blocked|unknown",
    "root_cause": "failure-taxonomy enumeration",
    "secondary_causes": [],
    "certainty": "confirmed|provisional|unknown",
    "evidence_ids": ["job:<id>", "rp-log:<id>", "attachment:<id>", "source:<sha/path/line>"],
    "chain": ["last successful prerequisite", "first failure", "final exit"],
    "missing_evidence": [],
    "optimization": {"owner_layer": "component", "change": "minimal proposal",
                     "validation": "effect + original AC + restoration"},
    "capability_candidate": false,
    "candidate_gate": "READY|NEEDS_EVIDENCE|NEEDS_SPEC|NOT_CAPABILITY"
  }],
  "summary": {"by_run_kind": {}, "by_failure_stage": {}, "by_root_cause": {}}
}
```

证据 ID 必须来自输入/取证结果，不可虚构日志、截图内容、版本或已跑验证。
报告可以用 Markdown 展示，结构化 JSON 用于评测、去重和后续 CapabilityGap 转换。
没有失败且完整覆盖时 incidents 为空；采集失败时 coverage 写明阻断，不能以空 incidents
声称成功。统计按 incident 身份去重，recovered 与 final failed 分开。
plans_complete 只表示所选 Job 的关联计划是否取全，不表示全平台计划扫描。
collector 当前不全量扫描最终通过 Scenario 的历史失败 attempt，因此不能据此声称已统计全部重试恢复。

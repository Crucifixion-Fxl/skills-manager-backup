# M2 指标与业务意图验收

M2 在 M1 staging 技术 PASS 之后运行。通用 CLI 比较机器可判定的指标数值；AI 按 PRD 与 observability design 审查“这个指标是否回答业务问题”。两份证据分开，不以 Chart 有数字冒充业务验收。

## 指标契约

每个指标一个独立 YAML，加入 `events/selected-metrics.yaml`：

```yaml
# events/selected-metrics.yaml
metrics:
  - metrics/issue-101.yaml
```

```yaml
schemaVersion: 1
kind: metric
issue: "101"
metricId: paid_completion_rate
decisionQuestion: 付费用户有多少比例完成命名？
prd:
  uri: repo:docs/product/prd/name-that-bird.md
  revision: sha256:REPLACE_WITH_SOURCE_DIGEST
observabilityDesign:
  uri: repo:docs/observability/name-that-bird.html
  revision: sha256:REPLACE_WITH_SOURCE_DIGEST
formula:
  kind: ratio
  numerator:
    event: { type: SELF_DEFINE, trackerType: BASE, spm: [naming_completed], eventName: naming_completed }
  denominator:
    event: { type: PAGE, trackerType: BASE, spm: [naming_entry] }
  distinctBy: user_id
  population: paid_users
  window: 2026-10-02T00:00:00Z/2026-10-03T00:00:00Z
superset:
  datasetId: 1321
  chartId: 41
  assetRevision: chart-v2
expected:
  value: 0.5
  tolerance: 0.01
```

此例仅说明字段，ID 和摘要须替换为真实值。缺 PRD/observability revision、分母、去重粒度、人群、时间窗或预期值时返回 `CONTRACT_INCOMPLETE`。目前数值引擎支持 ratio；其它公式须先扩展契约与失败测试，不能让 AI 猜一种算法。

## 查询证据

项目 `metric` hook 从同一 release-candidate artifact 和 L4 runId 生成 `.tracking/metric-evidence.json`。顶层为 `{ "metrics": [ ... ] }`，每个元素按 metricId 一一对应，至少包含：

```json
{
  "metricId": "paid_completion_rate",
  "runId": "run-101",
  "commit": "<candidate commit>",
  "artifactDigest": "sha256:<candidate artifact digest>",
  "region": "<events/staging-target.yaml 中的 region>",
  "warehouseId": "<events/staging-target.yaml 中的 warehouseId>",
  "envFilter": ["staging"],
  "warehouse": {
    "queryId": "athena-1",
    "sourceTables": ["staging_events.dwd_event_naming_completed_hi", "staging_events.dwd_event_naming_entry_pv_hi"],
    "numerator": 1,
    "denominator": 2,
    "value": 0.5
  },
  "superset": {
    "queryId": "superset-chart-1",
    "datasetId": 1321,
    "chartId": 41,
    "assetRevision": "chart-v2",
    "sourceTables": ["staging_events.dwd_event_naming_completed_hi", "staging_events.dwd_event_naming_entry_pv_hi"],
    "value": 0.5
  }
}
```

表名仅是格式示例，实际值以经过审核的 `events/staging-target.yaml` 为准。数仓和 Chart 的查询 ID 必须不同，所有 UNION/JOIN 分支的实际物理源表都列入 `sourceTables`；缺表、目标文件外的表、空/多选 env、Chart 资产漂移或数值不一致失败。项目查询适配器须真正从查询 API 取结果并记录查询 ID/SQL 或 Dataset 修订；该 JSON 是运行证据接口，不是手工录入表。由于 Superset Dataset 的 `env` 切表语义属于运行时资产，通用 CLI 只能验证适配器报告的过滤值和物理表，不能独立证明其报告未造假。启用生产硬门禁前应在有权限环境对查询 API 与 Dataset SQL 做现场验收。

调用：

```bash
node cli/bin/events-tdd.js verify-metrics \
  --candidate=.tracking/release-candidate.yaml \
  --target=events/staging-target.yaml \
  --staging-report=.tracking/staging.json \
  --metric-list=events/selected-metrics.yaml \
  --evidence=.tracking/metric-evidence.json \
  --out=.tracking/acceptance.json
```

CI 模板仅在 `TRACKING_M2_ENABLED=true` 时运行 `tracking:metric`，并让 `publish` 复核 `acceptance.json` 与本次所选指标契约的摘要。未启用 M2 时不会伪造 acceptance PASS。

## AI 业务意图审查

读取契约中固定修订的 PRD 和 observability design，逐个决策问题输出以下字段并链接到源文档位置：`question`、`status: covered|gap|unknown`、`metricId`、`formula`、`population`、`window`、`dimensions`、`eventDependencies`、`datasetId/chartId`、`independentQueryId`、`evidence`、`reason`。检查是否漏了关键触点或维度、分子/分母与去重口径是否同 PRD、Dashboard 筛选是否让用户回答问题。

AI 的 `covered` 是可供人 review 的结论；若 PRD 未定口径、来源读不到或 Chart 配置未核对，必须用 `unknown` 或 `gap`。当前 Skill 不把 AI 自评当成独立的数值门禁，也不自动修改 Superset 资产。

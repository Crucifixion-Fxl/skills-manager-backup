---
name: grafana-dashboard-alert-update
description: Update and deploy Grafana dashboards and alert rules. Use when editing dashboard template, adding/editing panels, configuring alert rules, running format script, or deploying to Grafana (staging/prod). Project-agnostic; adapt paths and tooling to your repo.
---

# Grafana Dashboard & Alert Update

Workflow for updating Grafana: edit dashboard template and alert rules, run formatter, deploy dashboard and alerts. Adapt file paths, script names, and env vars to your project.

## Description

本 Skill 用于在代码库中**编辑并部署** Grafana 面板与告警规则：从定义指标、编辑 Dashboard 模板与告警 JSON，到执行格式化脚本、按环境部署到 Grafana（先 staging 再 prod）。适用于需要新增/修改监控面板、配置或调整告警规则、或运行 format/deploy 脚本的场景；与具体项目解耦，路径与脚本名需按仓库适配。

## Rules

- **先 staging 再 prod**：模板或告警变更后先部署到 staging 验证，再部署到 prod；禁止跳过 staging 直接改 prod。
- **模板必跑 formatter**：编辑 Dashboard 模板后必须执行格式化脚本，由脚本统一维护 panel ID 与 `gridPos`，禁止手改 ID。
- **数据源与 job**：Dashboard 使用模板变量（如 `${datasource}`、`$job`），禁止硬编码 datasource UID；告警中用占位符（如 `${job}`）由部署脚本按区域/环境替换。
- **Grafana v9+ 使用 Provisioning API**：使用 `PUT /api/v1/provisioning/folder/:folderUid/rule-groups/...`，使用 folder **UID** 而非 name；禁止使用 Ruler API 或按 folder 名称写规则。
- **新实例先 detect**：新 Grafana 实例或新区域接入时，先执行 detect/discovery，再在配置中填写 datasource UID 与 job 名称。
- **写操作需用户确认**：执行部署、创建/修改 Dashboard 或告警前需经用户确认。

## Environment overview (公司研发须知)

- **AWS**：区域为 **cn**、**eu**、**us**；每个区域均有 **staging**、**pre**、**prod** 三类环境。配置 Grafana 数据源、Prometheus job 与告警部署时需按区域与环境区分（如 `staging-cn-*`、`prod-us-*`）。
- **GCP**：算法等项目在 GCP 上另有 **staging**、**pre**、**prod** 三个环境，若需在 Grafana 中监控 GCP 上的服务，需单独配置对应数据源与 job。

**当前数据源（供检查 Dashboard 使用）**：
- **AWS**：**thanos-us**、**thanos-eu**、**thanos-cn**（各区域最新 Thanos 数据源）。
- **GCP**：**us-gcp-prometheus**。

研发可先在面板中选用上述数据源验证 Dashboard 是否正常出数。若选对数据源后仍无数据，需向运维确认：该服务的指标是否已被 Prometheus/Thanos 采集（如 job、scrape 配置是否覆盖该实例）。

部署脚本中的 `ENV_CONFIG`、datasource UID 与 job 命名需与上述环境对应；新实例或新区域接入时先跑 `detect` 再填配置。

## Prerequisites (adapt to your project)

- Metrics defined in your codebase (e.g. Go `promauto`, or Prometheus client in Python/Java/etc.)
- Dashboard template JSON (e.g. `grafana/**/template.json`)
- Alert rules config (e.g. `grafana/**/alerts/rules.json`)
- A formatter script for dashboard JSON and a deploy script for dashboard + alerts — see [reference.md](reference.md) for full **format** and **deploy** script logic and API usage.

## Workflow

### Step 1: Define Prometheus Metrics

Add metrics following Prometheus conventions. Example in Go with `promauto`:

**Counter** (request count, token usage):
```go
MyFeatureRequestRate = promauto.NewCounterVec(
    prometheus.CounterOpts{
        Name: "my_feature_request_rate",
        Help: "Rate of my feature requests",
    },
    []string{"model"},
)
```

**Histogram** (latency):
```go
MyFeatureRequestDuration = promauto.NewHistogramVec(
    prometheus.HistogramOpts{
        Name: "my_feature_request_duration_seconds",
        Help: "Duration of my feature requests",
    },
    []string{"model"},
)
```

**Naming rules**:
- snake_case; prefix matches the feature
- Counters: `_rate` or `_total` suffix
- Histograms: `_duration_seconds` suffix
- Common labels: `model`, `status`, `provider`

### Step 2: Instrument Service Code

Use metrics in handlers/services:

```go
timer := prometheus.NewTimer(service.MyFeatureRequestDuration.WithLabelValues(modelName))
defer timer.ObserveDuration()
service.MyFeatureRequestRate.WithLabelValues(modelName).Inc()
```

For success/failure:
```go
if err != nil {
    service.MyFeatureRequestsTotal.WithLabelValues("provider", "failure").Inc()
    return err
}
service.MyFeatureRequestsTotal.WithLabelValues("provider", "success").Inc()
```

### Step 3: Add Dashboard Panels

Edit your dashboard template. Add a **row header** and panels under it. Typical: one row per feature with 2 panels (rate + latency).

**Row header:**
```json
{
  "gridPos": { "h": 1, "w": 24, "x": 0, "y": 0 },
  "id": <next_id>,
  "title": "My Feature Metrics",
  "type": "row"
}
```

**Request rate panel (timeseries):**
```json
{
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
  "id": <next_id>,
  "options": {
    "legend": { "displayMode": "list", "placement": "bottom" },
    "tooltip": { "mode": "multi" }
  },
  "targets": [
    {
      "datasource": { "type": "prometheus", "uid": "${datasource}" },
      "expr": "sum(rate(my_feature_request_rate{job=~\"$job\"}[$__rate_interval])) by (model)",
      "legendFormat": "{{model}}",
      "refId": "A"
    }
  ],
  "title": "My Feature Request Rate",
  "type": "timeseries"
}
```

**Latency panel (avg + p95):**
```json
{
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
  "id": <next_id>,
  "options": {
    "legend": { "displayMode": "list", "placement": "bottom" },
    "tooltip": { "mode": "multi" }
  },
  "targets": [
    {
      "expr": "sum(rate(my_feature_request_duration_seconds_sum{job=~\"$job\"}[$__rate_interval])) by (model) / sum(rate(my_feature_request_duration_seconds_count{job=~\"$job\"}[$__rate_interval])) by (model)",
      "legendFormat": "avg {{model}}",
      "refId": "A"
    },
    {
      "expr": "histogram_quantile(0.95, sum(rate(my_feature_request_duration_seconds_bucket{job=~\"$job\"}[$__rate_interval])) by (le, model))",
      "legendFormat": "p95 {{model}}",
      "refId": "B"
    }
  ],
  "title": "My Feature Request Duration",
  "type": "timeseries",
  "unit": "s"
}
```

### Step 4: Format Dashboard JSON

After editing the template, run your formatter so panel IDs and `gridPos` are correct (sort by row sections, sequential IDs, 2-column layout). Run before committing or deploying. **Format script usage and full implementation**: [reference.md § Format Script](reference.md#1-format-script-fmtpy).

### Step 5: Add Alert Rules

Edit your `rules.json` (or equivalent) and add entries to the `rules` array.

**Error rate alert:**
```json
{
  "name": "My Feature Error Rate High",
  "expr": "sum(rate(my_feature_requests_total{job=\"${job}\", status=\"failure\"}[5m]))",
  "condition": "gt",
  "threshold": 0.1,
  "for": "5m",
  "severity": "critical",
  "summary": "My feature error rate is elevated"
}
```

**P95 latency alert:**
```json
{
  "name": "My Feature P95 Latency High",
  "expr": "histogram_quantile(0.95, sum(rate(my_feature_request_duration_seconds_bucket{job=\"${job}\"}[5m])) by (le))",
  "condition": "gt",
  "threshold": 30,
  "for": "5m",
  "severity": "warning",
  "summary": "My feature P95 latency exceeds 30s"
}
```

**Rule fields:**
| Field | Description |
|-------|-------------|
| `name` | Alert display name |
| `expr` | PromQL; use a `${job}` (or similar) placeholder if your deploy substitutes it per region/env |
| `condition` | `gt` or `lt` |
| `threshold` | Numeric value |
| `for` | How long condition must hold (e.g. `5m`) |
| `severity` | e.g. `critical` / `warning` → map to your notification routes |
| `summary` | Notification message |

### Step 6: Deploy

Use your deploy script with **staging first**, then prod (guard prod with an env flag or CI). Commands: after `fmt`, run `deploy-dashboard`; run `deploy-alerts` (use `--dry-run` first to inspect generated JSON). **Deploy script commands, config shape, and API usage (dashboard, legacy/unified/Provisioning alerts)**: [reference.md § Deploy Script](reference.md#2-deploy-script-deploypy).

---

## When to Update Grafana (Best Practices)

| When | Action | Note |
|------|--------|------|
| **Template changed** (new/edited panels or queries) | Run formatter, then deploy dashboard | Ensures correct panel IDs and layout |
| **Alert rules changed** (new rules, thresholds, expr) | Deploy alerts (prefer `--dry-run` first) | Validate in staging before prod |
| **New feature** (metrics + panels + alerts) | Deploy to staging, verify, then prod via CI or manual | Never skip staging for prod |
| **Only code/metrics changed, no JSON** | No Grafana deploy needed | Deploy when you change template or rules |
| **CI** | If pipeline includes Grafana, deploy on merge to target branch | Use tokens and prod guard in CI |

Recommended order: change template or rules → run formatter (for template) → deploy to staging → verify → deploy to prod (CI or explicit approval). Avoid deploying to prod without staging verification.

---

## Grafana v9+ / Unified Alerting (Provisioning API)

If your Grafana uses **unified alerting** (e.g. v9+), use the **Provisioning API**, not the legacy Ruler API.

### Use Provisioning API, not Ruler

- **Wrong**: `POST /api/ruler/...` often returns 403 with admin token.
- **Right**: `PUT /api/v1/provisioning/folder/:folderUid/rule-groups/:group` with body: `AlertRuleGroup` (`title`, `folderUid`, `interval`, `rules`).

### Use folder UID, not name

- Write rules using the folder **UID** (e.g. from `GET /api/folders` or `GET /api/v1/provisioning/alert-rules`).
- Configure `folder_uid` (or equivalent) in your deploy config, not folder display name.

### New instance: detect then configure

- Datasource UID and Prometheus job names may differ per instance. Run a "detect" or discovery step, then set datasource UIDs and job names in config so alerts resolve correctly.

### Notifications

- Unified alerting uses Grafana contact points and routing. If your `rules.json` has a `notifications` map, it may be ignored; route by severity in Grafana UI instead.

### Folder behavior (legacy vs provisioning)

| Item | Legacy (e.g. v8) | Provisioning (v9+) |
|------|------------------|---------------------|
| Dashboard folder | Often by folder ID or name | By folder ID / UID per your script |
| Alert folder | By folder **name** | By folder **UID** |
| Alert API | Ruler: `POST /api/ruler/...` | Provisioning: `PUT /api/v1/provisioning/folder/{uid}/rule-groups/...` |

Reference: [Grafana Alerting Provisioning HTTP API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/alerting_provisioning/).

---

## Checklist

- [ ] Metrics defined and used in service/handler code (timer + counter)
- [ ] Dashboard panels added to template (row + rate + latency)
- [ ] Formatter run on template (panel IDs + grid layout)
- [ ] Alert rules added to rules config
- [ ] Deploy alerts to staging and verify
- [ ] (If new Grafana instance) Run detect/discovery and set datasource UIDs and job names

## Examples

### Bad

```bash
# 未跑 formatter 就部署（会导致 panel ID 错乱）
./deploy-dashboard --env prod

# 告警里硬编码 job 名称，无法按区域/环境替换
"expr": "sum(rate(my_metric_total{job=\"prod-us-myservice\"}[5m]))"

# v9+ 仍用 Ruler API（常返回 403）
curl -X POST -H "Authorization: Bearer $TOKEN" "$GRAFANA_URL/api/ruler/..."
```

```json
// Dashboard 里硬编码 datasource UID
"datasource": { "type": "prometheus", "uid": "thanos-us" }
```

### Good

```bash
# 编辑模板后先 format 再部署，先 staging 再 prod
./fmt.py grafana/**/template.json
./deploy-dashboard --env staging
# 验证后
./deploy-dashboard --env prod

# 告警使用占位符，由部署脚本按环境替换
"expr": "sum(rate(my_metric_total{job=\"${job}\", status=\"failure\"}[5m]))"
```

```json
// Dashboard 使用模板变量
"datasource": { "type": "prometheus", "uid": "${datasource}" }
```

```bash
# v9+ 使用 Provisioning API + folder UID
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @rule-group.json \
  "$GRAFANA_URL/api/v1/provisioning/folder/${FOLDER_UID}/rule-groups/my-group"
```

## Conventions

- Dashboard: use template variables (e.g. `${datasource}`, `$job`) — do not hardcode datasource UIDs.
- Alerts: cannot use dashboard template variables; use a placeholder (e.g. `${job}`) that the deploy script substitutes per region/env.
- Panel IDs: let the formatter assign them; do not hand-maintain.
- Severity: map to your notification routes (e.g. critical → pagerduty, warning → slack).
- Provisioning (v9+): use folder UID and Provisioning API; do not use Ruler or folder name for writing rules.

---
name: prom-grafana-dev
description: Prom + Grafana 的完整开发方法论——Dashboard-as-Code 设计 + 本地 docker-compose 栈 + PromQL 契约测试（scrape/alert/provisioning/panel round-trip）+ CI 门禁。当 observability-design 产出方案后、准备写实现前触发。也用于 "dashboard 已配但容易被改坏" 的回归保护场景。曾用名 tdd-prom-grafana（短暂命名）；TDD 只是其中 Rule 3/5 的验证层，不是全部方法论。
---

# prom-grafana-dev

## Description

**为什么需要这个 skill：** `observability-design` 产出的告警规则、scrape config、Grafana dashboard PromQL 只定义了 "Target state"——写在设计文档里，不等于写在回归网里。很容易出现这样的 silent drift：

- 有人改了 metric 名字或 label，dashboard 整页变空
- 有人加了一条 alert，但 PromQL 引用了不存在的 metric，alert 永远不 fire（"loaded 但 silent"）
- 有人升级了 OTel exporter，scrape config 还能拿到 target up==1，但业务 metric 不再暴露
- dashboard JSON 有 `$namespace` 变量，本地 scrape 没打这个 label，dashboard 看起来 "都绿"，线上却空白

这套 skill 的定位是：**把 dashboard JSON / alert YAML / scrape config 视为 "需要测试保护的代码资产"**——在本地 docker-compose 起 Prom + Grafana，round-trip 校验每个 panel 的 PromQL 真的能取到数、每条 alert 的 expr 真的能解析到 series、每个必选 metric 真的注册上来。CI 每个 MR 都跑一遍，dashboard / alert 改动同步跟着代码走。

## 适用场景

| 场景 | 用户说法 | 入口 |
|------|--------|------|
| `observability-design` 出完方案，准备落地 | "给 X 观测方案加本地回归" | 直接进全流程 |
| Dashboard 已上线但没测试保护，频繁出现 "图又哑了" | "给 dashboard 加回归门禁" | 从 Rule 3 + Rule 6 切入 |
| CI 里要加 dashboard / alert 不回退门禁 | "加 test-observability stage" | 从 Rule 5 切入 |

## 不适用场景

- 纯前端埋点 / 业务指标链路（Snowplow / 数仓 / BI）——看 `observability-design` 的"链路①"；本 skill 聚焦链路②③（错误 + 系统指标）的 PromQL 层
- 探索性 ad-hoc dashboard（只是临时看一下，下周就删）
- 不以 Prometheus / Grafana 为消费端的方案（换成对应的 query round-trip 策略）

## 与其他 skill 的关系

- **上游**：`observability-design` 产出设计文档、指标清单、dashboard / alert 目标态
- **下游 / 归属**：`testing-strategy` 的 L2-2 子层（真实 infra、mock 外部服务、契约断言）；verify.sh 等价于该层的一组集成用例
- **协作**：`dev-infra`——观测容器必须 per-worktree 命名，否则多个 worktree 并行开发时 Prom / Grafana 端口和容器名冲突（见 customer-care 实践：`customer-care-prometheus-${WORKTREE_ID}` / `customer-care-grafana-${WORKTREE_ID}`，其中 `WORKTREE_ID = sha1(REPO_ROOT)[:8]`）
- **执行工具**：`grafana-dashboard-alert-update`（dashboard JSON / alert YAML 的编辑和下发）；本 skill 负责 "改完怎么测"，不负责 "怎么改"

## Rules

### Rule 1 — 四类契约（不重不漏）

Observability 本地回归必须覆盖这四类、且只覆盖这四类：

| 契约 | 验证什么 | 不验证什么 | 失败意味着 |
|------|---------|----------|----------|
| **1a. Scrape target 健康** | 每个 job 的 `up{job="..."} == 1` | 具体业务逻辑 | scrape config 坏了 / 容器起不来 / 端口没通 |
| **1b. Alert rules 加载 + expr 可解析** | `/api/v1/rules` 能列出 expected group；每条 alert 的 `query` 回灌 `/api/v1/query` `status==success` | alert 阈值数值是否合理（应在设计阶段定） | alert 写错了，生产上永远不 fire |
| **1c. Dashboard provisioning** | Grafana 有对应 datasource；dashboard JSON 按 UID 被 provisioning 加载到；datasource proxy 能真跑 PromQL 返回 success | 视觉渲染 | provisioning 坏了 / datasource UID 写错 / Grafana 版本不兼容 JSON schema |
| **1d. Panel PromQL round-trip** | 从 dashboard JSON 抽每个 panel 的 `expr`，替换 `$variable` 后打到本地 Prom，断言 `status==success` 且有 result | 具体数值 | metric / label 被改名、`$namespace` 变量在本地无匹配 label、PromQL 语法写错 |

**关键不重不漏**：只有 Panel PromQL round-trip（1d）能发现 "metric 改名导致 dashboard 空白"；只有 Alert expr round-trip（1b）能发现 "alert loaded 但 silent"。两者都不能省。

### Rule 2 — 本地栈最小集合

一个可以跟主 `docker-compose.yml` 合并或独立 override 的 observability compose：

```yaml
# scripts/observability/docker-compose.observability.yml（示意）
services:
  prometheus:
    image: prom/prometheus:v2.x
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/rules.d:/etc/prometheus/rules.d:ro
    # 指向被测服务暴露的 /metrics endpoint（或 mock）
  grafana:
    image: grafana/grafana:11.x
    volumes:
      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources:ro
      - ./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ../../grafana/dashboards:/var/lib/grafana/dashboards:ro  # 和 prod 同源
```

**硬性要求：**

- Prometheus config 里必须有一个本地专属 label（例：`namespace: "customer-care-local"`），让 dashboard JSON 的 `$namespace` 变量有匹配值。否则 1c/1d 会假绿。
- scrape interval 在本地建议 5s（生产 15s-30s），verify.sh 触发流量后 `sleep 6` 等一次采集。
- 所有容器名、端口必须支持 per-worktree 后缀，避免多 worktree 冲突。customer-care 实践：`PROMETHEUS_CONTAINER="customer-care-prometheus-${WORKTREE_ID}"`、`GRAFANA_CONTAINER="customer-care-grafana-${WORKTREE_ID}"`，见 `scripts/dev-env.sh`。
- Prometheus config 从模板渲染，把端口和 WORKTREE_ID 注入进去：`envsubst '$GO_PORT $WORKTREE_ID' < prometheus.yml.tpl > prometheus.yml`。

### Rule 3 — 断言脚本结构（参照 customer-care verify.sh）

`e2e/observability-local/verify.sh` 分段组织，每段对应一类契约。失败时必须打印 Prom / Grafana 返回原文，方便本地调试。

```
== Env == （3 个 require_var）
  ✓ GO_PORT=<port>
  ✓ PROMETHEUS_PORT=<port>
  ✓ GRAFANA_PORT=<port>

== Prometheus == （契约 1a + 1b + 部分 1d 前置）
  ✓ Prometheus /-/healthy responds 200
  ✓ Prometheus scrape target <job> is UP
  → 触发流量（curl 关键 endpoint）+ sleep 6s 等 Prom 抓取
  ✓ Metric <主流量 counter> is queryable and >0
  ✓ metric registered: <REQUIRED_METRIC_1>
  ✓ metric registered: <REQUIRED_METRIC_N>
  ● / ○ activity metrics（soft report，不计 fail）
  ✓ Namespace label present（dashboard $namespace filter 生效）
  ✓ Alert rules loaded（<group> 至少 N 条 alerting 规则）
  ✓ Per-alert PromQL round-trip（python 脚本，逐条 expr 打回 Prom）

== Grafana == （契约 1c + 1d）
  ✓ Grafana /api/health returns 200
  ✓ Grafana Prometheus datasource is provisioned
  ✓ Grafana datasource proxy 能跑一条示例 PromQL 并拿到 result
  ✓ Shipped dashboard <uid> is provisioned into Grafana
  ✓ Per-panel PromQL round-trip（python 脚本，逐 panel expr 打回 Grafana proxy）

== Summary: N pass / M fail ==
```

**verify.sh 编写硬规则：**

- `set -euo pipefail`，单次失败 exit 1
- 不硬编码端口——从 `dev-env.sh`（或同名脚本）读，保证 per-worktree 正确
- 触发流量时要避开不经中间件的 `/health` endpoint，选一个真的会打点的 endpoint
- **必选指标 vs 活动指标区分**：`REQUIRED_METRICS` 是只要服务起来就该注册的（HTTP middleware counter、info-gauge）；`ACTIVITY_METRICS` 需要触发特定代码路径才会出现，verify.sh 里走 soft report（`●` / `○`）——避免在干净 dev 环境误报

### Rule 4 — PromQL round-trip 的实现锚点

Alert round-trip 和 Panel round-trip 都不该用 shell 手写，单独拆成 Python 脚本：

**alert round-trip（`check_alert_rules.py`，参照 customer-care 实现）：**

```
读 /api/v1/rules
 → 遍历每个 group 下 type == "alerting" && query != "" 的 rule
 → 每条 query 打到 /api/v1/query
 → 断言 body.status == "success"
失败时打印  alert <name>: <errorType>: <error msg>
```

**panel round-trip（`check_dashboard_panels.py`，参照 customer-care 实现）：**

```
读 grafana/dashboards/<dashboard>.json
 → 递归遍历 panels[] 和 panels[].panels[]（row/嵌套）
 → 收集每个 panel.targets[].expr
 → 替换 $variable（$namespace / $__rate_interval 等，在脚本 SUBSTITUTIONS dict 里集中管）
 → 每条 expr 走 /api/datasources/proxy/uid/<ds_uid>/api/v1/query
 → 断言 body.status == "success"
失败时打印  panel <title>: <errorType>: <error msg>
```

**变量替换不要怕粗糙**：`$__rate_interval` 用 `"1m"` 足矣——这里的校验点是 "PromQL 可解析 + 能匹配到 series"，不是数值正确性。

### Rule 5 — make target 接入 + CI 门禁

**make 接入（参照 customer-care `Makefile` 的 `test-observability-local` target）：**

```makefile
test-observability-local:
	@bash -c 'source $(DEV_ENV) && \
		ensure_mysql >/dev/null 2>&1 && \
		ensure_<其他 L1 依赖> && \
		ensure_<业务服务> && \
		ensure_observability && \
		exec "$$REPO_ROOT/e2e/observability-local/verify.sh"'
```

target 必须：

- 幂等拉起 L1 infra + 被测服务 + observability 栈（`ensure_*` 是 customer-care 里 `source dev-env.sh` 后暴露的函数，作用是 "如果容器已起就复用，没起就启动"）
- 不 tear-down（让开发者可以失败时手动进 Prom UI 看）
- 退出码透传 verify.sh 的退出码

**CI 接入（`.gitlab-ci.yml`）：**

```yaml
stages:
  - test-observability   # 新增 stage

test-observability-local:
  stage: test-observability
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - make test-observability-local
```

**门禁语义：**

- Dashboard JSON 改动但 verify.sh 没同步更新（例：panel 新增 但 `$variable` 在脚本 SUBSTITUTIONS 里没定义）→ CI 红
- 新增 alert rule 但 PromQL 引用不存在 metric → CI 红
- 新增 panel 但被测服务没暴露对应 metric → CI 红（panel round-trip 返回空 result 或 `bad_data`）
- 可选加 meta-check："dashboard JSON panel 数量 == verify.sh 通过断言数 - 基线数"，防止 panel 新增但没真正被 round-trip 覆盖

### Rule 6 — Dashboard-as-Code 的纪律

`verify.sh` 只有在 "dashboard JSON / alert YAML 是唯一 source-of-truth" 的前提下才有意义。规矩：

- **Dashboard JSON 必须进 Git**（customer-care：`grafana/dashboards/smart-popup-overview.json`，和 Grafana provisioning 挂载的是同一份）
- **Alert rules YAML 必须进 Git**（customer-care：从 `k8s/api/base/prometheusrule.yaml` 在 `make dev` 阶段渲染出本地 `rules.d/*.yml`，让本地 Prom 和线上加载同一份 rule 定义）
- **verify.sh 从 JSON 用 `jq` / Python 动态抽 PromQL**，严禁把 PromQL 硬编码到脚本里——这样 dashboard 改 PromQL verify.sh 自动跟着变
- **禁止 "UI 里改 dashboard → 导出 → 覆盖 JSON"** 的流程；必须 "JSON edit → provisioning 重新加载 → UI 里验证视觉"；反向流程会悄悄引入 UI 注入的字段（`id`、`version`、`iteration`）扰动 diff
- **Alert 阈值用常量变量**（例如 rule file header 里集中 `$critical_p99 = 5`），避免一个阈值散落在多条规则里

## 参考实现：customer-care `e2e/observability-local/`

参考实现完整路径：`/home/jchen/customer-care/e2e/observability-local/`，包含：

| 文件 | 作用 |
|------|------|
| `verify.sh` | 主入口，20 项断言，彩色输出，pass/fail 计数 |
| `check_alert_rules.py` | Alert expr round-trip（契约 1b 深度部分） |
| `check_dashboard_panels.py` | Panel expr round-trip（契约 1d） |

配套资源：

| 路径 | 作用 |
|------|------|
| `grafana/dashboards/smart-popup-overview.json` | Dashboard JSON，Grafana provisioning + check_dashboard_panels.py 共用 |
| `scripts/observability/prometheus/prometheus.yml.tpl` | Prom 配置模板，`envsubst $GO_PORT $WORKTREE_ID` 渲染 |
| `scripts/observability/render-alerts.py` | 从 k8s PrometheusRule YAML 渲染本地 rules.d |
| `scripts/dev-env.sh` 中 `ensure_observability()` | 幂等起 Prom + Grafana 容器 |
| `Makefile` 中 `test-observability-local` target | 本地 + CI 入口 |

### 20 项断言分布（customer-care 实际统计）

| 段 | 断言 | 契约 |
|----|------|------|
| Env | GO_PORT 已设置 | - |
| Env | PROMETHEUS_PORT 已设置 | - |
| Env | GRAFANA_PORT 已设置 | - |
| Prometheus | `/-/healthy` 返回 200 | 1a 前置 |
| Prometheus | scrape target `customer-care` up==1 | 1a |
| Prometheus | `smart_popup_http_requests_total` queryable 且 > 0 | 1a + 实际有数据 |
| Prometheus | metric registered: `smart_popup_http_requests_total` | 1a（契约漂移） |
| Prometheus | metric registered: `smart_popup_http_request_duration_seconds` | 1a |
| Prometheus | metric registered: `smart_popup_js_engine_version` | 1a（info-gauge） |
| Prometheus | metric registered: `smart_popup_rules_version` | 1a（info-gauge） |
| Prometheus | metric registered: `smart_popup_growthbook_query_duration_seconds` | 1a |
| Prometheus | metric registered: `smart_popup_growthbook_query_errors_total` | 1a |
| Prometheus | `namespace` label present（dashboard `$namespace` 能匹配） | 1d 前置 |
| Prometheus | Alert rules 加载：`smart-popup.rules` group 至少 4 条 alerting | 1b |
| Prometheus | Per-alert PromQL round-trip（`check_alert_rules.py`） | 1b 深度 |
| Grafana | `/api/health` 返回 200 | 1c 前置 |
| Grafana | Prometheus datasource 已 provisioning | 1c |
| Grafana | Datasource proxy 能跑示例 PromQL 拿到 result | 1c + 1d 端到端 |
| Grafana | Shipped dashboard uid=`smart-popup-overview` 已 provisioning | 1c |
| Grafana | Per-panel PromQL round-trip（`check_dashboard_panels.py`） | 1d 深度 |

额外还有一段 "活动指标 soft report"（`●` / `○`）——不计入 fail，只提醒开发者 "本地环境还没触发到这个 code path"。customer-care 当前把 `smart_popup_resolver_recipes_returned` 放在这里，因为本地 GrowthBook OSS 无法执行 `/api/eval`，所以 resolver 不会打点。这是一种 "诚实承认 local 覆盖不到" 的模式。

## Examples

### Bad

- **Dashboard 配了但从没本地跑过 PromQL**——过两周 Prom scrape 配置调整或 metric 改名，dashboard 全哑，直到业务同学截图问 "为什么都是 No data" 才发现
- **verify.sh 把 PromQL 硬编码进脚本**——dashboard JSON 改了一个 expr 但脚本忘改，脚本依然绿，线上依然空
- **prom-grafana-dev 只跑 smoke（健康检查 + target up）**，不做 PromQL round-trip——contract drift 完全逃脱
- **Dashboard JSON 不进 Git，只在 Grafana UI 里 "手动编辑 → 导出一次"**——下次有人编辑又不导出，JSON 和 UI 不一致，verify.sh 过了但生产上被覆盖
- **本地 Prom scrape 没打 `namespace` label**，但 dashboard 用 `{namespace="$namespace"}` 过滤——panel round-trip 返回空 result 被当成 "数据还没到" 忽略，实际是契约在本地就已经断开
- **Alert 阈值只在本地改，忘了同步 k8s/ 下 PrometheusRule**——本地绿，线上阈值还是旧的

### Good

- **Dashboard JSON 每次 PR 必过 verify.sh 20 项断言**——合入主干前一定跑绿，panel 改 PromQL / 改变量名测试自动跟随
- **Alert rules 加载后逐条 expr round-trip**——误删 metric 导致 alert silent 立即暴露；CI 拦住 "loaded 但永远不 fire" 这种最阴险的回归
- **本地 Prom 打 `namespace="customer-care-local"` label**，dashboard 的 `$namespace` 模板变量在 verify.sh 里替换成同一个值——本地契约和生产契约同构
- **Observability 容器 per-worktree 命名**（`customer-care-prometheus-<WORKTREE_ID>`），多 worktree 并行开发各自拉起独立栈，verify.sh 也天然并发安全
- **必选指标列表 + 活动指标 soft report 分治**——干净 dev 环境不误报，真正的 "doc↔code drift"（设计文档里列了 metric 但代码没埋点）一定失败
- **`ensure_observability()` 幂等**——开发者本地反复跑 `make test-observability-local` 不会把上一次的状态洗掉，失败时可以直接进 Prom UI 调试

## References

- **customer-care 参考实现**：`/home/jchen/customer-care/e2e/observability-local/`（`verify.sh` 20 项断言、`check_alert_rules.py`、`check_dashboard_panels.py`）
- **customer-care Makefile target**：`/home/jchen/customer-care/Makefile` 的 `test-observability-local`
- **customer-care 容器命名 + Prom 模板**：`/home/jchen/customer-care/scripts/dev-env.sh`（`ensure_observability`、`PROMETHEUS_CONTAINER`）、`/home/jchen/customer-care/scripts/observability/prometheus/prometheus.yml.tpl`
- **上游 skill**：`observability-design`（设计文档、指标清单、目标态 PromQL）
- **归属层**：`testing-strategy` L2-2（真实 infra、mock 外部服务、契约断言）
- **并发环境**：`dev-infra`（per-worktree 容器 / 端口命名规则）
- **dashboard 下发**：`grafana-dashboard-alert-update`（本 skill 负责 "怎么测"，该 skill 负责 "怎么改"）

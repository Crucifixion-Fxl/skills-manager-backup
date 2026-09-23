# Metrics Config-as-Code — 多消费者模式

## 适用场景

当业务指标需要被 ≥ 2 个 BI/实验工具同时消费（GrowthBook + Superset + SLA + Grafana ...）时，本参考定义跨工具的 config-as-code 规约，保证口径单一来源 + UI drift 物理隔绝。

## 决策目录 (D1-D8)

### D1 — 范围边界：入 code / 留 UI

| 层 | 位置 | 变化频率 | 示例 |
|---|---|---|---|
| 核心口径 | **Git YAML** | 低 | dataset schema、filter SQL、metric 公式 |
| 展示层 | **UI** | 高 | chart viz type、dashboard 布局、颜色、筛选器位置 |

**原则**：把最多变的那层留给 UI 自由发挥，否则 YAML 膨胀 + MR 评审疲劳 + 大家绕过 code 直接改 UI，反而毁掉"code is SSOT"契约。

**边界 case**：如果 chart 里不得不写 SQL（如多层聚合），答案是**拆一个新的 virtual dataset**，不是放宽 policy。"偶尔写 SQL"是漏洞起点。

### D2 — 共享 vs 重复：YAGNI 决策

| 消费者数量 | 策略 | 理由 |
|---|---|---|
| ≤ 2 | 各写各 YAML，配 set parity 校验 | 抽象成本 > 重复成本 |
| ≥ 3 | 考虑共享层（dbt Semantic Layer / MetricFlow） | 漂移风险随消费者数量 N² 增长 |

**别做**：自发明中间 schema + 多 adapter 编译器。这套 YAGNI 上来直接爆——每次新增 filter 要改三处（shared + 两个 adapter），加速度走"配置即代码"的反面。

### D3 — SSOT 定位：Git vs Tool-native（**两种注册路径的根本选择**）

"SSOT 在哪里"决定整个 config-as-code 的架构。要么 Git 是 SSOT（工具被动接收），要么工具自身是 SSOT（Git 只放设计意图）。**二者不能并存**——中间态 = two-state 漂移。

| 路径 | SSOT 位置 | 注册方式 | 适用场景 |
|---|---|---|---|
| **CI-sync / Git-SSOT** | Git 仓里的 YAML | CI job 监听 YAML 变更 → 调工具 API 同步 | 配置即口径（口径漂移代价高）、变更频率低、多消费者需 parity 校验 |
| **Skill-driven / Tool-native SSOT** | 工具自身 state（Superset / Grafana / ...） | 设计意图写在 docs，人工 / AI skill 读 docs → 调 API 注册 | 配置即展示（chart 频繁迭代）、严 governance 成本 > 收益、需要灵活性 |

**选择决策矩阵**：

| 维度 | CI-sync 偏好 | Skill-driven 偏好 |
|---|---|---|
| 口径变更频率 | 低（季度级）| 高（周级） |
| 口径错误代价 | 高（影响 AB 决策 / SLA）| 低（chart 看错纠正快）|
| 消费者数 | ≥ 2（需 parity）| 1（独占）|
| Service user 可行性 | 有 service user | 只能用个人账号 |
| UI-native 审查价值 | 低（review 靠 Git diff）| 高（reviewer 在 UI 里能看渲染结果）|
| Review 频率 | 低频度、门禁型 | 高频度、对话型 |

**典型搭配**：
- **GrowthBook**：CI-sync（口径 = AB 实验决策，错代价高；消费者≥2 需 parity）
- **Superset dataset + metric**：Skill-driven（chart 迭代频繁；口径错代价低；独占消费者）
- **Grafana dashboard**：Dashboard-as-Code（provisioning YAML 在 Git，介于两者之间——因 alert 口径错代价高）
- **SLA 指标（dapp）**：CI-sync 优先（口径硬阈值 + oncall）

**反模式**：**Git YAML + 人工 skill 注册的混合**（比如 "YAML 在 git review 但不被 CI sync，要人 skill 跑一下"）——Git YAML 和工具 state 两边都是"有"但都不是"权威"，漂移时谁对不清楚。要么 CI sync 要求一致（Git-SSOT），要么彻底不落 YAML（Tool-native SSOT）。

### D4 — Set parity 强规约（**仅 Git-SSOT 路径适用**）

当两条 Git-SSOT 路径的工具（比如 GrowthBook + SLA 都 CI sync）共享同一组指标，必须强 parity：

**规则**：同一业务指标必须在所有 Git-SSOT 消费者侧都定义（各自原生 schema 表达）。

**豁免条件（唯一允许的）**：
- 该指标属于**强隔离 pipeline**（如 dryrun 离线分析不进 AB 平台），且被豁免的工具侧本就不消费这条 pipeline
- 豁免 case 必须在 YAML 里显式标记 `parity.<tool>: null` + `rationale: <一句话解释>`
- 再给一条 MR description rationale 供 reviewer 核对

**CI 门禁**：lint 级别（deterministic Node script），不是编译器。双向 diff：
```
set_a = {m.parity.growthbook for m in <tool>_metrics if m.parity.growthbook}
set_b = {m.id for m in growthbook.factMetrics}
if set_a != set_b → exit 1
```

**Skill-driven 路径不适用 set parity**：因为工具 state 不在 Git，没有"YAML 对 YAML" 的 diff 可做。改走 D4' 设计审查：

### D4' — 设计意图一致性审查（**Skill-driven 路径适用**）

Skill-driven 路径下，**设计意图 docs**（如 `docs/architecture/<sys>/observability/link-1-*.md` 的 Metric Catalog）是 review 对象，不是 tool state 本身。

审查点：
- 新增 metric 时，docs 的 catalog 是否更新
- 设计 doc 里列的 metric name 和 Git-SSOT 工具（如 GrowthBook metrics.yml）里的命名对齐（为人脑 cross-ref 方便）
- Semantic Layer（见 §6 / docs 里的 filter / metric 定义）里的公式和工具原生表达应当同义

**谁做审查**：reviewer 人工 + `/code-review` skill（AI 辅助），基于 docs diff 判断是否需要重新跑 `/superset register` 之类的 skill 操作。

### D5 — UI drift lock（**两条路径通用**）

无论哪条路径，工具侧**必须有**防 UI 绕过机制：

| 工具 | 锁机制 | 实施方（取决于路径）|
|---|---|---|
| Superset | `is_managed_externally: true` + `external_url` | CI sync path: sync script 注入; Skill-driven: skill 注册时注入 |
| GrowthBook | "Official" badge | CI sync 自动打（Git-SSOT 独占路径）|
| Grafana | Provisioning 模式（非 API 创建）| provisioning YAML 在 git; provisioner 读取 |

`external_url` **必须指到 git blob / docs blob**（带 commit sha，不是 branch 名）——reviewer 点进去能看到准确那版定义。

- CI sync 路径：`external_url` 指 git blob of the YAML
- Skill-driven 路径：`external_url` 指 git blob of the design doc section（docs anchor）

### D6 — 环境切换（**两条路径通用**）

无论 YAML 还是设计意图 docs 驱动，运行时 env 切换**首选 native 模板**：

```sql
-- Superset dataset SQL 示例
SELECT *, '{{filter_values('env')[0]}}' AS env
FROM
    {% if "staging" in filter_values('env') -%}
        analytics_staging.dwm_smart_popup_funnel_hi
    {% else -%}
        analytics.dwm_smart_popup_funnel_hi
    {% endif -%}
```

好处：一份 dataset 定义 + 用户在 dashboard filter 切环境，sync 脚本 / skill 都不做任何模板展开。

**避免：sync 时 substitute**。生成 staging/prod 两份 YAML 或两次 skill register = 维护两套，极易漂移。

## CI-sync 路径实施细节（选择了 D3 Git-SSOT 才适用）

### CI trigger 拓扑

| 情景 | Trigger 设计 |
|---|---|
| 多实例（如 GrowthBook us-test + us-prod） | 分支 × 实例矩阵：`staging 分支 → us-test`，`master 分支 → us-prod` |
| 单实例（罕见）| 单 job 在某条分支触发（通常 `staging`），接受无缓冲 |
| 全环境 shared dataset | 永远 staging 分支触发，prod 不独立 sync（staging → master 是串行 fast-forward） |

**无缓冲警示**：单实例 + staging 触发 = staging merge 立即落生产，无"先试跑一周"空间。守门靠 MR review + AI review + validate dry-run job——三层缺一不可。

### Service user + Vault

**每项目一个 service user**，不复用全公司账号：

| 好处 | 复用的坏处 |
|---|---|
| 审计清晰（"这个 dataset 是 cc_sync_bot 改的"）| "某项目 ops 账号误改别项目 dataset"说不清 |
| 权限最小化（按项目圈）| 全公司账号必然权限宽 |
| 人事解耦 | 跨项目协调改权限 |

**Vault 约定**：`secret/<service>/<env>/<category>`，如 `secret/customer-care-growthbook-sync/shared/ci_bot`（`shared` = 单实例跨环境共用）。

**SSO 场景**：工具对人走 SSO，对 service user 走 **DB-auth provider** 或 **OAuth M2M**。service user 必须禁 MFA。

### CI AI review（advisory）

Git-SSOT 路径下，YAML 变更经 CI MR，可挂 AI review job 做语义审查：

**CI template**（可 include）：[scripts/metrics-review.gitlab-ci.yml](../scripts/metrics-review.gitlab-ci.yml)

```yaml
include:
  - project: engineering/skills
    file: skills/observability-design/scripts/metrics-review.gitlab-ci.yml
    ref: main
```

**advisory vs blocking**：AI review 默认 **advisory**（allow_failure: true）——真正门禁由 deterministic lint 承担（set parity + schema validation）。减少 AI 假阳性卡 MR。

## Skill-driven 路径实施细节（选择了 D3 Tool-native SSOT 才适用）

### 设计意图 docs 作为 human-readable reference

Skill-driven 路径没有 Git YAML，但**必须有设计意图 docs**——否则没有 review gate，完全 free-form 漂移不可控。

**标准位置**：`docs/architecture/<system>/observability/link-*.md` 里的 Metric Catalog 章节（按 Metric Layer 分组 + 按 fact_table 分组）。

**docs 应当包含**：
- Fact Table / Grain / Dimensions / Measures 描述（人读）
- Filter 复用目录（命名 filter + SQL 含义）
- Metric Catalog 表：name / fact_table / metric_type / layer / numerator / denominator / status
- **示例查询**（2-3 个代表性的 SQL snippet 展示 metric 怎么 rollup）

docs 改动 = MR review gate。

### Skill 读取协议

`/<tool>` skill（如 `/superset`）在 register 时约定：
1. 读取相关 design doc section（按 user 指明的 scope 或用户交互探明）
2. 把 catalog 翻译成工具 API 的 register payload
3. 注册同时打 `is_managed_externally=true` + `external_url` 指回 docs blob（带 commit sha）
4. 记录一次 "skill session transcript" 作为实施审计（可选存到 docs/ops-log/ 或 notion 等）

**设计/实施 diff**：
- Skill 每次 register 前先 diff（tool API 拉当前 state vs docs 设计态）
- 展示给 user 确认：新增 X 个 metric、修改 Y 个、删除 Z 个
- user 确认后执行

### UI 保护

Skill 注册的 entity **必须打 `is_managed_externally`**（见 D5）——否则 UI 绕改发生漂移，设计意图 docs 和 tool state 不一致，后续 skill 再 register 又会"把别人改的覆盖掉"，冲突。

### Review 机制

- **口径 review**：docs 改动走 MR review（人工 + `/code-review` skill 帮忙做一致性检查）
- **实施 review**：每次 skill register 时，skill 打印 diff 给 user 确认；user 是 reviewer
- **漂移检测**：定期（比如每月）run 一次 "tool state vs docs catalog diff"，把漂移项暴露（通常 = 有人绕过 `is_managed_externally` 改了 UI，或 docs 更新了但没跑 register）

### 反模式

- ❌ **不写设计意图 docs 就直接 skill register**：等于无 SSOT，漂移立即开始
- ❌ **skill 不打 `is_managed_externally`**：失去 UI 防御，和"没 skill 直接 UI 建"一样
- ❌ **docs 更新了但不跑 skill register**：docs vs tool state 静悄悄漂移；建议 docs MR 的 checklist 强制"run skill register + 贴结果"
- ❌ **chart / dashboard "写好了就不验"**：见下节 TDD。

### Dashboard 开发 TDD 流程（Skill-driven 路径尤其强调）

Skill-driven 路径下 dataset/metric 注册完不是终点——下游 chart 和 dashboard 必须 **TDD 到"真能渲染成功"为止**。不验证 = 上线后 PM 打开 dashboard 空白或 500 才知道。

**Superset 完整链路**：

```
1. Dataset register (skill, 注册 dataset + metrics + columns)
     ├─ Validation: SQL Lab 跑 dataset SQL verify 能返数据
     └─ 打 is_managed_externally=true + external_url → docs blob

2. Chart 开发 (skill 或 UI)
     ├─ 选 dataset, 选 metric(s), 选 dimension(s), 选 viz_type
     ├─ ⚠️ **Filter 配置** 是 chart 一等公民, 不是 optional:
     │    - env filter (staging / prod schema 切换, D6)
     │    - time range filter (通常绑 dvce_created_tstamp)
     │    - 业务维度 filter (scene_id / variation_key / ...)
     └─ 保存前 preview rendering 确认有数据

3. Dashboard 组装 (UI)
     ├─ 拖 chart 到 dashboard
     ├─ ⚠️ **Dashboard-level filter** (native filter) 覆盖所有 chart:
     │    - env filter (强制必选)
     │    - time range filter (强制必选, default "last 7 days")
     │    - AB variation filter (AB 场景强制)
     └─ 配置 cross-filter (chart 之间联动)

4. TDD 验证 (跑到真能渲染为止)
     ├─ T1 冒烟: 打开 dashboard URL, 全部 chart 无 error, 都有数据
     ├─ T2 env 切换: filter 切 staging/prod, 数据都更新且合理
     ├─ T3 time 切换: 切不同时间范围, 数据响应正确 (不是 cached)
     ├─ T4 业务维度: scene_id / variation_key filter 切换各值, chart 数据对应变化
     ├─ T5 空数据: 选一个已知无数据的 time range, chart 显示 "No data" 不 crash
     └─ T6 权限: 用不同权限账号登录 check (e.g. reader-only 账号打开无 500)
```

**T1-T6 的自动化程度**：
- Superset API 允许 POST `/api/v1/chart/<id>/data` 拉 chart 数据——可以写**冒烟脚本**验证 T1/T5
- T2-T4 跨 filter 组合的验证目前靠人工 + skill 辅助（未来可做自动化）
- T6 需要一个 reader-only 测试账号，可保留在 service user 库

**Why TDD？**
- Dashboard 是 **终端用户产品**，比 dataset / metric 更接近 consumer
- 上游 metric 对了不代表下游 chart 能正确聚合（viz_type 和 metric type 不匹配时 Superset 可能 silent fail）
- Filter 是 dashboard UX 的核心——没配好 = PM 看到错误数据做错决策
- `is_managed_externally` 锁 dataset 但 **chart/dashboard 没锁**（Superset 3.x 的 chart 也支持 externally_managed，但通常 project 允许 UI 编辑 chart layout），所以 chart 层是最容易漂的

**Dashboard-as-Code 路径的补充**：如果项目选 **dashboard-as-code**（dashboard JSON 入 git + CI provisioning，Grafana 常用），TDD 可以自动化为 CI assertion（见 `prom-grafana-dev` skill 的 `verify.sh` 4 类断言 + panel PromQL round-trip）——委派给 `prom-grafana-dev` 落地。Superset 不走 dashboard-as-code 时，TDD 靠手工 + skill 辅助。

## 历史：为什么原 D6-D8 被 restructure

原版 D6 CI trigger / D7 Service user / D8 CI AI review 讨论的是 **CI-sync 路径的实施模式**，但被误放成"通用决策"，导致看起来"所有 config-as-code 都必须 CI sync + service user"。实际上这些只对 Git-SSOT 路径有意义。本次 restructure 后：

- **通用决策**（D1-D6）：范围、消费者数、SSOT 定位、一致性、UI 锁、环境切换——**所有路径都要决**
- **路径专属实施**（两个独立 subsection）：CI-sync / Skill-driven 各自展开，按 D3 选择的路径看对应 subsection

customer-care 是 real-world mixed example：GrowthBook 走 CI-sync + YAML SSOT；Superset 走 skill-driven + docs SSOT；Grafana 走 dashboard-as-code（provisioning 中间态）。一个项目里两种路径可以并存，只要**每个工具自己选定一条**，不混搭。

## AI Review 审查 prompt（本 skill 承载）

当 reviewer（AI 或人）审查一个 MR diff 触及 `**/metrics.yml` 或 `**/datasets/*.yml` 或任何 BI config 类文件时，必须完成：

### Check 1：Set parity
- 列出 MR 改动前后两份 YAML 的业务指标名集合
- 新增 metric 时，对方 YAML 必须有对应定义 / 或 `parity.<tool>: null` + rationale
- 缺口 → 直接 block，标红

### Check 2：口径 SQL 语义等价
对每一对 parity 映射的 metric：
- Superset `expression` 的 `CASE WHEN ... THEN 1 END` 聚合条件
- GrowthBook `factTableFilter.value` 的 WHERE 片段
必须语义等价。

**常见漂移模式**（AI 必须重点盯）：
- `IN ('a','b')` vs `IN ('a')` —— 列表长度不一致
- 布尔比较显式 vs 隐式：`shown = true` vs `shown`
- NULL 处理：`COUNT(CASE WHEN X THEN 1 END)` 和 `COUNT_IF(X)` 在 NULL 上行为差异
- 分母类型：proportion（分母 = 全部行）vs ratio（分母 = 某 filter 子集）——两边必须用同类型

### Check 3：字段完整性
- 每个 dataset：`name`, `database`, `sql`, `columns`, `metrics` 齐全
- `columns[*].is_dttm` 至少标一个时间列
- Jinja env filter：SQL 包含 `filter_values('env')` + `_staging` 分支（D5 规范）
- `metric.parity.<tool>`: 字段存在，值为 string 或 null；null 时必须有 `rationale`
- Sync 注入字段：`is_managed_externally` 不在 YAML 里（sync 时注入），`external_url` 同理

### 输出格式
- 发 inline comment 指向问题行
- MR 整体结论（一条总结 comment）：PASS / CONCERN (LOW|MED|HIGH) / FAIL
- FAIL 时建议补救动作（比如"在 observability/growthbook/metrics.yml 增加对应 factMetric"）

## Monorepo 目录约定

引入 BI 配置即代码时，所有 BI 工具统一放在仓库根的 `observability/` 伞下，而不是散在根目录：

```
<repo-root>/
├── observability/
│   ├── grafana/        ← dashboard JSON + alert YAML
│   ├── growthbook/     ← factTables + factMetrics YAML + sync 脚本
│   ├── superset/       ← datasets + metrics YAML + sync 脚本
│   └── sla/            ← (适用时) SLA 指标配置
│
├── scripts/
│   └── observability/  ← 本地 dev tooling (prometheus.yml.tpl, render-alerts.py...)
│                         — 归 scripts/, 不归 observability/
│
└── k8s/
    └── **/prometheusrule.yaml  ← K8s CRDs, 归 k8s/, 不归 observability/
```

**为什么 `observability/` 伞**：
- 语义内聚：所有 BI 消费端配置在一处，onboarding 工程师一眼看明白"这个仓的 observability 都在 `observability/` 下"
- CI 规则简化：`changes: observability/**` 一条 glob 覆盖所有 BI config 类改动
- 扩展自然：下次加 SLA / SaaS BI 工具，`observability/<new-tool>/` 直接落位，不需要重新决定放哪

**为什么 `scripts/observability/` 和 `k8s/**/prometheusrule.yaml` 不进 `observability/` 伞**：
- `scripts/observability/` 是**本地 dev tooling**（generate templates, glue code），不是部署源。归 `scripts/` 与仓里其它脚本保持一致
- `k8s/**/prometheusrule.yaml` 是 **K8s CRD**（Kubernetes 部署清单，恰好管 Prometheus alert），归 `k8s/` 与其它 manifest 一致

**原则**：按"这个文件在谁的生命周期里（CI / K8s / 本地 dev / ...）"分类，而不是按"这个文件在描述什么主题"分类。否则每个文件都要在多维度下做归属决策，目录结构会开始打架。

**从零做 vs 既有项目**：
- 从零做新项目 → 直接按 `observability/<tool>/` 结构建，避免以后搬家
- 既有项目已有 `grafana/`、`growthbook/` 等根目录 → 推荐迁移到 umbrella 下（减少散落），但要注意一次性改完所有 path 引用（CI rules / sync scripts / docs / README）防止漂移

## Observability 设计文档拆模块约定

当系统的 observability 设计文档超过 ~500 行（三链路每条都有实质内容需要展开），**应该拆成文件夹结构**而不是单个 .md 文件。按 `/architect` skill 规范，文件夹入口文件命名为 **`overview.md`**（不是 `README.md`）。

**推荐结构**：

```
docs/architecture/<system>/observability/
├── overview.md                   ← 入口（/architect 规范）
│                                   业务目标 / 决策问题 / 术语表 / 三链路总图 / 模块索引
├── link-1-business-metrics.md    ← 链路① 业务指标（dwm / Metric Catalog / AB / Superset）
├── link-2-errors.md              ← 链路② 错误（Sentry / PagerDuty / triage SOP）
├── link-3-system.md              ← 链路③ 系统（OTel / Prometheus / Grafana / alert rules）
├── guardrails.md                 ← 安全护栏（频次 / 极值 / 降级判定）
└── local-contract.md             ← 本地契约（verify.sh / dbt schema test）
```

**为什么 `overview.md` 不是 `README.md`**：
- `README.md` 语义是"仓库 / 目录的上手说明"，对 code 目录合适
- `overview.md` 语义是"这个设计文档文件夹的业务总览"，是 architect 层概念
- GitHub/GitLab 的 README.md 渲染是 default（对库级入口有价值），但对**文档子目录**没有特殊意义，用 overview.md 更符合 information architecture

**参考实现**：customer-care 仓 `docs/architecture/smart_popup/observability/` 就是这个结构（MR !225，6 个文件共 ~1060 行，从 1025 行单文件拆分而来）。

## 参考实现

- **customer-care 仓**：`observability/growthbook/` + `observability/superset/` + `scripts/lint-metrics-parity.mjs`（lint）+ `.gitlab-ci.yml` 的 validate/lint/sync 三 job
- **设计文档模板**：见 [design-doc-template.md](design-doc-template.md) 的 "Config-as-Code" 章节（TODO：等 customer-care 合并后补入模板）
- **MR 参考**：customer-care!222 —— 本参考的第一个落地实现

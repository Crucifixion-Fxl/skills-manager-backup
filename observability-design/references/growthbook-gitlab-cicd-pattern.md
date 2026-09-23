# GrowthBook Config-as-Code on GitLab CI

AB 平台（GrowthBook）上定义 fact table 和 metric 的标准做法是 UI 点创建 —— 但 UI 操作易漂移（没 review、没历史、跨环境复制靠手动）。本 reference 给出一个 **git = SSOT、CI 同步到 GrowthBook** 的 config-as-code 模式，适配 GitLab（GrowthBook 官方只给了 GitHub Actions 样例）。

## 为什么要 config-as-code

UI 创建 metric 的典型痛点：

- **没 review** — PM 点一下就上线，口径错误发布后才发现
- **没历史** — UI 改过了就改了，回滚要靠记忆
- **跨环境漂移** — staging 建一份、prod 再点一份，两边字段定义慢慢不一致
- **上下游 SQL 改动无法联动** — dwm 表列改名，GrowthBook fact table SQL 不跟着改 → metric 静默坏掉

config-as-code 把 fact table / metric 定义放 git，merge 到 master 自动 sync 到 GrowthBook，并把 sync 过来的对象标记成 **"Official"**，UI 不可改 → 强制 git 是唯一来源。

## GrowthBook 的 GitHub Metrics 集成原理

GrowthBook 官方文档：<https://docs.growthbook.io/integrations/github-metrics>

核心是一个 REST 端点：

```
POST https://<growthbook-host>/api/v1/bulk-import/facts
Authorization: Bearer <admin_api_token>
Content-Type: application/json

Body: { factTables: [...], factMetrics: [...] }
```

**行为**：

- 按 `id` upsert：已存在就更新、不存在就创建
- 同步过来的对象自动打 **Official** 标 → UI 锁定编辑
- 不是幂等覆盖：**不会删除**没在 payload 里的 metric（要删必须手动）
- 官方 GitHub Action 只是薄包装：yaml parse → fetch → POST

GitHub Action 本身没什么魔法，把 fetch 放到 GitLab CI 里效果完全一样。

## 目录结构

```
<repo>/growthbook/
├── metrics.yml       # 唯一真源：fact tables + metrics 的 YAML 声明
├── sync.mjs          # 20 行 Node 脚本，读 YAML → POST 到 GrowthBook
├── package.json      # yaml 依赖 + npm run sync
└── README.md         # 本地测试说明 + link 到文档
```

目录名 `growthbook/` 约定放在仓库根 — CI 的 `changes:` 路径过滤直接 match 到这层。

## metrics.yml 模板

**三顶级键**：`factTables` + `factTableFilters` + `factMetrics`。`factTableFilters` 是**可复用过滤器注册表**——同一个 filter（如 `shown=true`、`action='never_remind'`）定义一次，被多个 metric 引用，口径单一不漂移。

```yaml
# 口径 SSOT: docs/architecture/<system>/observability/link-1-business-metrics.md §3（指标定义） + §4（口径）
# Filter wiring contract（GrowthBook v3 postBulkImportFacts）：
#   * 顶层 factTableFilters 定义 {factTableId, id, data:{name, description?, value}}，value = SQL WHERE 片段（不含 WHERE 关键字）
#   * factMetrics[*].data.numerator.filters 和 .denominator.filters 接受的是 filter id 数组（不是 inline SQL）
#   * Proportion 分母隐式 = "fact table 全部行"，只有 numerator 需要 filter
#   * Ratio 分母显式 = 某 filter 子集（例：D7 留存分母是 `device_active_d7 IS NOT NULL` 排除未到期样本）
factTables:
  - id: <fact_table_id>           # 稳定 id，不要改（改了 = 新建）
    data:
      name: <Display Name>
      description: <human-readable>
      datasource: <ds_xxx>        # GrowthBook 里已配好的数据源 ID（或 logical name，见 D6）
      projects: ["<project_slug>"]  # GrowthBook project 多租户隔离
      sql: |
        SELECT
          event_id,
          user_id,
          dt,
          dvce_created_tstamp AS timestamp,
          <dim columns>,
          <fact columns>
        FROM <schema>.<dwm_table>
      userIdTypes:
        - user_id

factTableFilters:
  - factTableId: <fact_table_id>
    id: <filter_id>               # 稳定 id，metric 引用靠它
    data:
      name: <Display Name>
      description: <WHERE 的业务含义>
      value: "<SQL WHERE 片段，不含 WHERE 关键字>"  # 例: "action IN ('support_call','support_feedback')"
  - factTableId: <fact_table_id>
    id: <another_filter_id>
    data: { ... }

factMetrics:
  - id: <metric_id>
    data:
      name: <Display Name>
      description: <多行描述，含分子/分母口径>
      projects: ["<project_slug>"]
      metricType: proportion       # proportion | mean | count | ratio（见下）
      numerator:
        factTableId: <fact_table_id>
        column: $$count            # 特殊变量：表示计数（或具体列名用于 mean/sum）
        filters: [<filter_id>]     # 引用 factTableFilters 里的 id 数组
      # denominator: 仅当 metricType=ratio 时需要（见下）
      tags: ["<project>", "<category>"]
```

**`metricType` 选择（proportion vs ratio 决策规则）**：

| 类型 | 计算 | 需要 denominator? | 典型用途 |
|------|------|-------------------|---------|
| `proportion` | filter 命中行数 / fact table 全部行数 | ❌ 隐式 = 全部 | 转化率、CTR、show_rate、never_remind_rate |
| `ratio` | filter A 行数 / filter B 行数 | ✅ 显式 filter 子集 | **D7/D30 留存**（分母必须 `X IS NOT NULL` 排除未到期样本）、任何"分母不是全部"的比率 |
| `mean` | SUM(column) / COUNT(column) | ❌ | ARPU、平均时长 |
| `count` | SUM(column) 或 COUNT(*) | ❌ | 总收入、总曝光数（小心 experiment 解读） |

**决策硬规则**：
- 如果分母是"fact table 全部行" → `proportion`
- 如果分母是"某 filter 子集"（尤其含 `IS NOT NULL` 过滤未到期样本）→ `ratio`
- 错用 `proportion` 做 D7 留存 = 把"还没到 D7 的行"算成"D7 不活跃" → 远端留存假性偏低

**Ratio 模板（D7 留存示例）**：

```yaml
factTableFilters:
  - factTableId: smart_popup_funnel
    id: device_active_d7_true
    data: { name: Device Active D7 = true, value: "device_active_d7 = true" }
  - factTableId: smart_popup_funnel
    id: device_active_d7_known
    data: { name: Device Active D7 Known, value: "device_active_d7 IS NOT NULL" }

factMetrics:
  - id: smart_popup_device_d7_retention
    data:
      metricType: ratio
      numerator:   { factTableId: smart_popup_funnel, column: $$count, filters: [device_active_d7_true] }
      denominator: { factTableId: smart_popup_funnel, column: $$count, filters: [device_active_d7_known] }
```

## sync.mjs（完整脚本，20 行）

```javascript
import { parse } from "yaml";
import fs from "fs";

const FILE_NAME = process.env.METRICS_FILE || 'metrics.yml';
const API_HOST = process.env.GROWTHBOOK_API_URL;
const GB_API_KEY = process.env.GROWTHBOOK_API_TOKEN;

if (!API_HOST || !GB_API_KEY) {
  console.error("Missing GROWTHBOOK_API_URL or GROWTHBOOK_API_TOKEN");
  process.exit(1);
}

const file = fs.readFileSync(FILE_NAME, 'utf8');
const json = parse(file);

console.log(`Syncing ${json.factTables?.length || 0} fact tables and ${json.factMetrics?.length || 0} metrics to ${API_HOST}...`);

const res = await fetch(`${API_HOST}/api/v1/bulk-import/facts`, {
  method: "POST",
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${GB_API_KEY}`
  },
  body: JSON.stringify(json)
});

const resJson = await res.json();
if (!res.ok) {
  console.error("Sync failed:", resJson);
  throw new Error(resJson?.message || "Error syncing");
}
console.log("Success!", JSON.stringify(resJson, null, 2));
```

搭配 `package.json`：

```json
{
  "name": "<project>-growthbook-config",
  "type": "module",
  "private": true,
  "scripts": { "sync": "node sync.mjs" },
  "dependencies": { "yaml": "^2.5.0" }
}
```

无需任何框架/构建步骤，Node 20+ 内置 fetch 即可。

## GitLab CI 配置

两个 job：staging（master 自动同步）+ prod（tag 手动触发）。

```yaml
# ===========================================================================
# Sync GrowthBook metrics (config-as-code)
# ===========================================================================
# Definitions live in growthbook/metrics.yml. On master, auto-sync to the
# test environment. On tagged releases, a manual job syncs to production.
#
# Adapted from GrowthBook's GitHub Metrics integration pattern:
#   https://docs.growthbook.io/integrations/github-metrics
#
# Required CI/CD variables (masked + protected):
#   GROWTHBOOK_API_TOKEN_STAGING — admin API key for staging GrowthBook
#   GROWTHBOOK_API_TOKEN_PROD    — admin API key for prod GrowthBook

.growthbook-changes: &growthbook-changes
  - changes:
      - growthbook/metrics.yml
      - growthbook/sync.mjs
      - growthbook/package.json
    if: '$CI_COMMIT_BRANCH == "master"'

sync:growthbook-metrics:staging:
  stage: deploy
  image: node:20-alpine
  rules: *growthbook-changes
  needs: []
  variables:
    GROWTHBOOK_API_URL: "https://<staging-host>"
    GROWTHBOOK_API_TOKEN: $GROWTHBOOK_API_TOKEN_STAGING
  script:
    - cd growthbook
    - npm install --silent
    - npm run sync
  timeout: 5m
  allow_failure: false

sync:growthbook-metrics:prod:
  stage: deploy
  image: node:20-alpine
  rules:
    - if: '$CI_COMMIT_TAG'
      changes:
        - growthbook/metrics.yml
        - growthbook/sync.mjs
        - growthbook/package.json
      when: manual
  needs: []
  variables:
    GROWTHBOOK_API_URL: "https://<prod-host>"
    GROWTHBOOK_API_TOKEN: $GROWTHBOOK_API_TOKEN_PROD
  script:
    - cd growthbook
    - npm install --silent
    - npm run sync
  timeout: 5m
  allow_failure: false
```

**关键点**：

- `changes:` 路径过滤 — 只有 `growthbook/**` 变更才跑（不拖慢其他 pipeline）
- `rules: - if: master` — staging 自动；prod 必须 tag + manual
- `allow_failure: false` — sync 失败 pipeline 红，强制开发人关注
- `needs: []` — 不依赖其他 stage，并行跑最快
- `image: node:20-alpine` — 极小镜像，拉起只要几秒

## 环境变量 / Token 管理

**GrowthBook Admin API Token**：

1. 登录 GrowthBook UI → Settings → API Keys → 创建 admin key（不是 SDK key）
2. Staging 和 Prod 环境**各创一个** token（绑定各自环境的 account）
3. 进 GitLab → 项目 Settings → CI/CD → Variables
4. 添加 2 个 variable：
   - `GROWTHBOOK_API_TOKEN_STAGING` — Masked + Protected + 不带 prefix
   - `GROWTHBOOK_API_TOKEN_PROD` — Masked + Protected + Protected branches only（master/tags）

**为什么 masked + protected**：

- Masked — CI log 里显示 `[MASKED]`，即使 script 不小心 echo 也不泄露
- Protected — 只在 protected branches / tags 上可见，feature 分支 pipeline 看不到 prod token

**`GROWTHBOOK_API_URL`**：

- 可以写死在 CI job 的 `variables:` 里（非敏感）
- 生产 URL 通常也是配在 k8s configmap 让 admin 后端用（参考 `k8s/admin/overlays/*/configmap.yaml` 里的 `GROWTHBOOK_API_URL`）

## 多项目共存

多仓库共享同一个 GrowthBook instance 靠两个机制隔离：

1. **`projects` 字段** — `factTables[*].data.projects: ["<project_slug>"]` 限制对象归属。UI 按 project 过滤。
2. **命名前缀** — `factTable.id: smart_popup_funnel`、`metric.id: smart_popup_show_rate` — `<domain>_<name>` 格式避免撞名

两个仓库各自维护自己的 `metrics.yml`，CI 只 sync 自己的；GrowthBook bulk-import 是 upsert、不删除其他对象 → 互不干扰。

## 已知踩坑

### 1. Filter 用 factTableFilters 顶层注册，不要 inline SQL 或 UI 回填

`factMetrics[*].data.numerator.filters: [...]` 接受的是 **filter id 数组**，不是 `"WHERE action='click_primary'"` 这种字符串。

**正确做法（GrowthBook v3 `postBulkImportFacts`）**：同一份 YAML 里用顶层 `factTableFilters` 声明 filter，然后在 metric 里引用 id（见 §"metrics.yml 模板"的 ratio 示例）。一次 sync 搞定，不用 UI 回填。

**历史坑（已解决）**：早期 SmartPopup `metrics.yml` 把 `filters: []` 留空走"先 CI sync，再 UI 建 filter 拿 id，再发第二个 MR 回填"的路径——现在已废弃。customer-care `growthbook/metrics.yml` 已经迁到顶层 `factTableFilters` 模式，是本 reference 的 canonical 示例。

**proportion vs ratio 决策**：见 §"metrics.yml 模板"的硬规则——D7/D30 留存之类"分母需排 NULL"的 metric 必须用 `ratio + denominator filter`，否则口径偏低。

### 2. Bulk-import 是 upsert，不是全量覆盖

payload 里没的 metric / fact table **不会被删除**。要删的话：

- UI 手动删
- 或者调单独的 DELETE endpoint（API docs: `/api/v1/metrics/{id}`）

所以 git 删一个 metric 并不会让 GrowthBook 上消失 → 有组织的方案是建个 deprecation 流程。

### 3. UI 已存在的 metric 怎么处理

已存在的 UI-created metric **不受 bulk-import 影响**（它们不是 Official）。可以渐进迁移：

- UI 上的保留
- 新 metric 一律写 yml 用 sync 创建（Official）
- UI metric 逐个删掉 → 在 yml 里用相同名字 + 不同 id 重建

### 4. Token 权限

用 **Admin API key**（不是 SDK key、不是 User API key）。权限不足会在 sync 脚本里收到 401/403，错误信息不是很清晰（"Unauthorized"），别误以为是 token 字面拼错。

### 5. `datasource` 和 `userIdTypes` 必须预先在 GrowthBook 建好

`datasource: ds_xxx` 的 ID 要去 UI 的 Data Sources 页面拿。`userIdTypes: [user_id]` 里的 `user_id` 必须是 GrowthBook 已注册的 identifier type。YAML sync 不创建这两者，只引用。

### 6. `projects` 字段的 slug 不是 ID

`projects: ["smart-popup"]` 用的是 project 的**slug**（创建时指定的），不是 `prj_xxx` 格式的 project ID。两者都能用但混了容易错。Slug 人读友好、URL-safe、推荐统一用 slug。

## 真实示例：SmartPopup（5 metrics + 1 fact table + 7 复用 filters）

基于 customer-care 的 `growthbook/metrics.yml`（当前 master，已切到顶层 `factTableFilters` 模式）：

```yaml
factTables:
  - id: smart_popup_funnel
    data:
      name: SmartPopup Funnel
      description: SmartPopup business metrics wide table at scene_entry grain
      datasource: ds_405gzm1nlqc3zcjh
      projects: ["smart-popup"]
      sql: |
        SELECT
          event_id, user_id, dt,
          dvce_created_tstamp AS timestamp,
          serial_number, scene_id, recipe_id, rules_version, engine_version,
          experiment_id, variation_key,
          shown, condition_met, fatigue_passed, fatigue_blocked_by,
          action,
          device_active_d7, device_active_d30
        FROM analytics.dwm_smart_popup_funnel_hi
      userIdTypes: [user_id]

factTableFilters:
  - factTableId: smart_popup_funnel
    id: ticket_submitted
    data: { name: Ticket Submitted, value: "action IN ('support_call', 'support_feedback')" }
  - factTableId: smart_popup_funnel
    id: popup_shown
    data: { name: Popup Shown, value: "shown = true" }
  - factTableId: smart_popup_funnel
    id: never_remind_clicked
    data: { name: Never Remind Clicked, value: "action = 'never_remind'" }
  - factTableId: smart_popup_funnel
    id: device_active_d7_true
    data: { name: Device Active D7 = true, value: "device_active_d7 = true" }
  - factTableId: smart_popup_funnel
    id: device_active_d7_known
    data: { name: Device Active D7 Known, value: "device_active_d7 IS NOT NULL" }
  # ... d30_true / d30_known 同理

factMetrics:
  - id: smart_popup_ticket_submission_rate        # 近端北极星
    data:
      name: Ticket Submission Rate
      description: |
        近端北极星指标 — 弹窗驱动用户求助的转化率
        分子：action IN ('support_call','support_feedback') 的 scene_entry 数
        分母：所有 scene_entry 数
      projects: ["smart-popup"]
      metricType: proportion
      numerator:
        factTableId: smart_popup_funnel
        column: $$count
        filters: [ticket_submitted]
      tags: ["smart-popup", "north-star"]

  - id: smart_popup_show_rate                     # 漏斗效率
    data:
      name: Show Rate
      metricType: proportion
      numerator: { factTableId: smart_popup_funnel, column: $$count, filters: [popup_shown] }
      tags: ["smart-popup", "funnel"]

  - id: smart_popup_never_remind_rate             # 护栏
    data:
      name: Never Remind Rate
      metricType: proportion
      numerator: { factTableId: smart_popup_funnel, column: $$count, filters: [never_remind_clicked] }
      tags: ["smart-popup", "guardrail"]

  - id: smart_popup_device_d7_retention           # 远端北极星（用 ratio 排除未到期样本）
    data:
      name: Device D7 Retention
      metricType: ratio
      numerator:   { factTableId: smart_popup_funnel, column: $$count, filters: [device_active_d7_true] }
      denominator: { factTableId: smart_popup_funnel, column: $$count, filters: [device_active_d7_known] }
      tags: ["smart-popup", "north-star", "retention"]
```

5 个 metric 覆盖 AB 四层（近端北极星、远端北极星、漏斗效率、护栏），全部指向同一张 `dwm_smart_popup_funnel_hi`（见 [dwm-wide-table-pattern.md](dwm-wide-table-pattern.md)），不需要复杂 JOIN。D7/D30 留存用 `ratio` 而不是 `proportion`，分母带 `IS NOT NULL` 过滤未到期样本——这是配 retention 指标的硬规则。

`.gitlab-ci.yml` 对应 job 定义在同一个仓库，两个环境的 API URL：

- staging：`https://us-test-ab-management-api.addx.live`
- prod：`https://us-ab-management-api.addx.live`

## 检查清单（引入到新项目）

- [ ] GrowthBook 里已建好 datasource（指向数仓 Athena/BigQuery）
- [ ] GrowthBook 里已建好 project（拿到 slug）
- [ ] GrowthBook 里已注册 userIdType（通常是 `user_id`）
- [ ] 创建了 staging + prod 两个 Admin API token
- [ ] GitLab CI/CD Variables：`GROWTHBOOK_API_TOKEN_STAGING/PROD` 设为 Masked + Protected
- [ ] 仓库根下 `growthbook/` 目录：metrics.yml / sync.mjs / package.json / README.md
- [ ] `.gitlab-ci.yml` 加两个 sync job（参考上面模板）
- [ ] 第一次手动 `npm run sync` 本地跑通（用 staging token）
- [ ] merge 到 master 后在 GrowthBook UI 确认对象出现且标为 **Official**
- [ ] 所有 metric 标记 tags + projects，便于筛选
- [ ] `.yml` 里每个 metric 的 description 写清楚分子/分母 SQL 口径（给非写 SQL 的 PM 看）

## 参考

- GrowthBook GitHub Metrics 集成：<https://docs.growthbook.io/integrations/github-metrics>
- GrowthBook Bulk Import API：`POST /api/v1/bulk-import/facts`
- 实现参考：`customer-care/growthbook/`（metrics.yml + sync.mjs + package.json + README.md）
- CI 参考：`customer-care/.gitlab-ci.yml` 搜索 `sync:growthbook-metrics`
- 配套 dwm 表：见 [dwm-wide-table-pattern.md](dwm-wide-table-pattern.md)
- `growthbook` skill — 查询实验结果、flag 开关等运行时操作（非 config）

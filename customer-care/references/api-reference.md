# Customer Care Admin — API Reference

**来源**：`gitlab.addx.ai/services/customer-care` → `admin/src/app/api/**/route.ts`（`staging` 分支）。

**Base URL**（Staging）：`https://customer-care-admin-staging.addx.live`

**认证**：当前无前置鉴权（Pending ADR #9 → 飞书 SSO + SpiceDB）。

**Tenant**：几乎全部路由硬编码 `tenant_id="default"`。

---

## 1. Facts — `smart_popup_fact`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/facts?tenant_id=default` | 列出所有 fact，按 `name` 升序；返回含 `changeStatus`（对比 prod snapshot） |
| POST | `/api/facts` | 创建 fact |
| GET | `/api/facts/{id}` | 单条 |
| PUT | `/api/facts/{id}` | 更新可变字段（real-time save） |
| DELETE | `/api/facts/{id}` | 删除；若被 recipe.condition 或 scene.entry_event.where 引用则 409 |

**POST /api/facts 请求体**（Zod `createFactSchema`）：
```json
{
  "name": "bind_fail_count",
  "operator": "EventCounter",
  "events_json": { "increment_event_key": "device_bind_fail" },
  "source_json": { "type": "payload", "path": "reason" },
  "isolation_field": "device_sn",
  "where_json": { "==": [{ "var": "reason" }, "timeout"] },
  "params_json": { "window_seconds": 3600 },
  "tenant_id": "default",
  "created_by": "operator@a4x.local"
}
```

**`source_json` 4 种形态**(Snowplow-native 2026-04-15 起必填,2026-04-19 加 profile + local):

```jsonc
// event_derived: payload 取值
{ "type": "payload", "path": "reason" }

// event_derived: Snowplow context 取值(vendor + name 匹配 context,path 是 data 内路径)
{ "type": "context", "vendor": "ai.addx", "name": "device_ctx", "path": "sn" }

// backend_resolved: Go 后端 resolve-response `profile_facts` 下发(ProfileValue 专用)
{ "type": "profile", "entity": "user", "group": "bind_stats", "field": "fail_count_7d" }

// local_evaluated: SDK 本地上下文(LocalContextValue 专用,field 必须是 LOCAL_CONTEXT_FIELDS 预设 7 个之一)
{ "type": "local", "field": "is_weekend" }
```

- `path` 是点号路径,**不带** `event.payload.` / `event.context.<v>.<n>.` 前缀(Go parser `adapter/rules_parser.go` 会自己拼)
- **Cross-field 硬约束**:`operator=ProfileValue ⇔ source.type=profile`、`operator=LocalContextValue ⇔ source.type=local`(create + update 双端 Zod 拦)
- 参考文档:`docs/plans/2026-04-15-smart-popup-snowplow-event-interface-design.md §4.2`、`docs/architecture/smart_popup/profile-fact.md`、`docs/plans/2026-04-19-smart-popup-local-context-facts.md §3`

**合法 operator**(`VALID_OPERATORS`,共 5 个):`EventCounter` / `BucketedWindowCounter` / `LatestValue` / `ProfileValue` / `LocalContextValue`。
**用户可创建 operator**(`CREATABLE_OPERATORS`,共 4 个):上面去掉 `LocalContextValue` — 7 个预设 local 字段由 `snapshot-assembler` 发布时自动注入,用户直接在 recipe condition 里写 `facts.is_weekend` 即可(issue #79)。
**Host-contributed 类**(ProfileValue + LocalContextValue)**禁止**订阅事件:`events_json` 必须为 `{}`、`where_json` 不传,否则 Zod 400。
**`BucketedWindowCounter.params.window_seconds`** 必须在 `[60, 86400]`。

**Fact Category(3-way,`admin/src/types/fact-category.ts`)**:

| Category | operator | 数据流向 |
|---|---|---|
| `event_derived` | EventCounter / BucketedWindowCounter / LatestValue | SDK 订阅事件流,本地累计 |
| `backend_resolved` | ProfileValue | Go 后端 `resolve-response.profile_facts` 单次下发 |
| `local_evaluated` | LocalContextValue | SDK dispatch 时从 `LocalContextProvider` 即时取 |

**LocalContextValue 7 个预设字段**(`admin/src/lib/local-context-fields.ts`,和 engine + Dart SDK 三边锁步):

| field | type | self_correcting |
|---|---|---|
| `time_hour_of_day` | number | ✅ |
| `time_minute_of_hour` | number | ✅ |
| `day_of_week` | number (ISO: Mon=1) | ❌ |
| `is_weekend` | boolean | ❌ |
| `timezone_offset_minutes` | number | ❌ |
| `language` | string | ❌ |
| `region` | string | ❌ |

`self_correcting=true` 的字段免 MaxImpressions fatigue(时间自然越界会停触发);`false` 字段和 ProfileValue 一样必须配 MaxImpressions,否则 recipe-linter 告警。

---

## 2. Scenes — `smart_popup_scene`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scenes` | 列出所有 scene（全局）；返回带 `featureEnabled`（GrowthBook 同步状态）和 `changeStatus` |
| POST | `/api/scenes` | 创建 scene（Saga：DB → GrowthBook feature → GB Experiment） |
| GET | `/api/scenes/{id}` | 单条 |
| PATCH | `/api/scenes/{id}` | 只能改 `description / owner / entry_event_key / entry_event_where`，**不能改 `scene_id`** |
| DELETE | `/api/scenes/{id}` | 归档策略（保留 GB feature 映射） |

**POST /api/scenes 请求体**（Zod `createSceneSchema`）：
```json
{
  "tenant_id": "default",
  "bundle_id": "",
  "scene_id": "device_bind_fail",
  "entry_event": {
    "event": "device_bind_fail",
    "where": { "==": [{ "var": "reason" }, "timeout"] }
  },
  "description": "设备绑定失败弹窗",
  "owner": "ops@a4x.local",
  "created_by": "operator@a4x.local"
}
```

**副作用**：
1. 创建 GrowthBook feature `scene.<scene_id>`（string, default `"control"`, tags `["smart_popup"]`）
2. 创建 GrowthBook Experiment（trackingKey=`scene.<scene_id>`, 只 `control` 变体）
3. GrowthBook 失败 → 补偿删除 DB 行 → 502

**响应**：返回 `scene` 对象 + `gb_feature_id` + `next_step` 提示（引导跳 GrowthBook UI 配 variant）。

---

## 3. Recipes — `smart_popup_recipe`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/recipes?scene_id=X` | 列出，按 `sceneId` + `sortOrder` 排序 |
| POST | `/api/recipes` | 创建；`(scene_id, recipe_id)` 唯一；best-effort 同步到 GB Experiment variations |
| GET | `/api/recipes/{id}` | 单条 |
| PUT | `/api/recipes/{id}` | 更新（草稿容忍，publish 时做语义校验） |
| DELETE | `/api/recipes/{id}` | 物理删除 |

**POST /api/recipes 请求体**：
```json
{
  "bundle_id": "",
  "scene_id": "device_bind_fail",
  "recipe_id": "bind_fail_gte_3",
  "condition_json": { ">=": [{ "var": "facts.bind_fail_count" }, 3] },
  "fatigues_json": [
    { "func": "MaxImpressions", "params": { "max": 1, "window": "rolling", "window_days": 1 } },
    { "func": "MinInterval",   "params": { "interval_sec": 3600, "scope": "scene" } }
  ],
  "cms_id": "popup_bind_help",
  "description": "连续失败 ≥3 次",
  "sort_order": 0,
  "created_by": "operator@a4x.local"
}
```

**Fatigue `window`**:`session | rolling | lifetime`(2026-04-19 起;legacy `"day"` 已废弃,publish-time 主动拒绝)。`"rolling"` 必须配套 `window_days ∈ [1, 90]`,其他 window 传 `window_days` 会 400。
**Fatigue `func`**(常用):`MaxImpressions`、`MinInterval`。
**UI 默认新增 fatigue**:`{func:"MinInterval", params:{interval_sec:172800, scope:"scene"}}`(2026-04 harden v40:从老默认 60 秒调到 2 天;原因是 60s 实际等于不节流,而再往前的 `MaxImpressions/session/max:1` 又会把 recipe 静默限到仅触发一次)。

---

## 4. Publish Orders — `smart_popup_publish_order`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/publish-orders?status=...` | 列出最近 50 条，按 createdAt 倒序 |
| POST | `/api/publish-orders` | 创建 + 自动发 staging（组装 + 校验 + 上 S3 + 落 `rules_package`） |
| GET | `/api/publish-orders/{id}` | 单条 |
| POST | `/api/publish-orders/{id}/approve-prod` | 触发飞书审批（或 dev auto-approve） |
| GET | `/api/publish-orders/{id}/diff` | 与当前 prod 的 diff |
| GET | `/api/publish-orders/{id}/snapshot` | 拿原始 snapshot JSON |
| POST | `/api/publish-orders/preview` | 不落库，返回组装后的 snapshot 供预览 |

**Order 级状态机**（`smart_popup_publish_order.status`，源码 `types/rule.ts` `PublishOrderStatus`）：
```
pending_staging → staging_ok → pending_approval → approving → published
                                    │
                                    └→ rejected
```

**Package 级状态机**（`smart_popup_rules_package.approval_state`，2026-04-16 PR-05 新增）：
```
approving → s3_uploading → s3_uploaded → db_committed → notified
                               │
                               └→ rolled_back / failed   （retry ≥ 3 或补偿终态）
```
新增字段：`approval_state`（VARCHAR+CHECK） / `idempotency_key`（UNIQUE） / `last_state_transition_at` / `retry_count`；`publish_order` 同时新增 `current_package_id` 指向正在推进的 package。

**POST /api/publish-orders 请求体**：
```json
{
  "selections": [
    { "scene_id": "device_bind_fail", "recipe_ids": ["bind_fail_gte_3"] }
  ],
  "created_by": "operator@a4x.local",
  "tenant_id": "default"
}
```

**响应包含 `progress[]`**（每个发布步骤 `{name,status:"ok"|"fail",elapsedMs,detail}`）—— 失败时前端依此渲染进度对话框，定位哪一步挂了。

**Publish 步骤**：
1. `load_data`：加载选中 scenes + 对应 recipes + 全部 facts
2. `assemble_snapshot`：组装成 `RulesConfig`（v3）
3. `validate_rules`：结构化校验（JSON Schema + 内部约束）
4. `validate_growthbook`：检查 `scene.<id>` feature 存在
5. `lookup_version`：下一个 staging version
6. `s3_publish`：fan-out 写 US/EU/CN bucket（`smart_popup/rules/<v>/rules.json` + `latest.json`）
7. `db_transaction`：INSERT `publish_order` + `rules_package(env=staging)`，状态 → `staging_ok`
8. `audit_log`：best-effort

**POST /approve-prod**（2026-04-16 PR-05 改为异步 + 幂等）：
- 请求头 `Idempotency-Key`（可选，服务端不传时用 `randomUUID()` 兜底）；同 key 重试返回同一 package，由 `rules_package.uk_idempotency_key` 唯一约束兜底
- Feishu 配置了 → 创建审批实例，`order.status=pending_approval`，`approvalInstanceId` 落库
- Feishu 未配置 → MySQL advisory lock 串行化并发调用 → 事务内：预留 prod version（max+1）、建 `rules_package(approval_state=approving)`、`order.status=approving` 并写 `current_package_id` → 释放锁 → HTTP 立即返回 → `advanceApprovalState(packageId)` fire-and-forget 异步推进到 `notified`
- 后台 worker（`admin/src/lib/approval-worker.ts`）周期扫 `idx_approval_state_stuck`：stuck > 5 分钟或 `retry_count > 3` → 翻 `failed / rolled_back`，同时清 S3 孤儿

---

## 5. Dry Run

### Mode A — `POST /api/dryrun/simulate`

同步 Node VM 执行（快速验证单 recipe）。

```jsonc
{
  "config_json": "active",         // 或完整 RulesConfig
  "scene_id": "device_bind_fail",
  "recipe_ids": ["bind_fail_gte_3"],
  // events[] 是 Snowplow-native SmartPopupEvent 形状（2026-04-15 后）
  "events": [
    {
      "event_name": "device_bind_fail",
      "event_vendor": "ai.addx",
      "timestamp": "2026-04-15T10:00:00Z",
      "payload": { "reason": "timeout" },
      "contexts": [
        {
          "schema": "iglu:ai.addx/device_ctx/jsonschema/1-0-0",
          "data": { "sn": "KB-0001" }
        }
      ]
    }
  ]
}
```

**`SmartPopupEvent` 字段**（`admin/src/types/rule.ts`）：
- `event_name` + `event_vendor`：事件复合主键，**两者都是 required**
- `timestamp`：ISO8601（对应 Snowplow `dvce_created_tstamp`）
- `event_id`：可选（对应 Snowplow `event_id`）
- `payload`：self-describing event 的 `unstruct_event.data.data`
- `contexts[]`：`[{ schema, data }]`，engine 按 `(vendor, name)` 版本无关匹配

**校验点**：
- `scene_id` 不存在 → 400
- `recipe_ids` 含未知 id → 400（errmsg 列出缺失 id）
- condition 引用的 fact 未定义 → 400（`Recipe conditions reference undefined facts: ...`）
- event 缺 `event_name` / `event_vendor` → 400（`events[n].event_name is required`）

**响应**：`{ rows[], events[] (tracker 事件), errors[] }`。

### Mode B — `POST /api/dryrun/batch` + `GET /api/dryrun/batch/{runId}` + `GET /api/dryrun/tasks`

异步:触发 Dagster job(PyMiniRacer 执行),从 Athena 拉历史事件,按 `(scene_id, trigger_id)` 聚合。**任务持久化**到表 `smart_popup_dryrun_task`,每次 `GET /api/dryrun/batch/{runId}` 会 best-effort 同步状态回 DB。

- `POST /api/dryrun/batch` → 返回 `{ run_id, task_id }`,落一行 `status=QUEUED` 的任务
- `GET /api/dryrun/batch/{runId}` → 查 Dagster 实时状态 + 同步回 DB
- `GET /api/dryrun/tasks?scene_id=&recipe_id=&limit=20` → 列历史任务(默认 20 条,上限 100),按 `createdAt desc`

**UI 入口**(2026-04-19):Scene 页 `/scenes/[sceneId]` 内嵌 `BatchDryRunPanel`,可传 draft `config_json` 测试**未发布**规则;结果行附 Superset dashboard 深链(通过 `dryrun_task_id` 预过滤 `preselect_filters`)。

---

## 6. 运行时查询

### `GET /api/rules/bundle?bundle_id=com.kb.kiwibit&env=staging|prod`

返回当前 S3 latest 指向的完整 `RulesConfig`（v3）。**排障第一站**。

### `GET /api/audit?...`

操作审计日志（`smart_popup_audit_log`），字段：`operator / action / bundleId / env / kind / targetVersion / detail / createdAt`。

典型 action：`publish_order_create`、`publish_order_approve`、`publish_order_publish`。

### `GET /api/health`

健康检查。

### `GET /api/config/external-links`

GrowthBook feature URL / CMS URL 模板(前端拼 deep link 用)。模板来自 runtime env(`GROWTHBOOK_FEATURE_URL_TEMPLATE` / `CMS_CONTENT_URL_TEMPLATE`),不用 `NEXT_PUBLIC_*` 以避免每环境重打镜像。

### `GET /api/cms-contents?locale=&limit=`

服务端代理到 Payload Marketing CMS(RecipeEditor 的 `cms_id` 下拉选择器用,issue #7)。规避两类问题:CORS(上游 allow-list 不含 admin 域)、网络(in-cluster 可通 CMS,公网 DNS 可能被办公室 VLAN 墙)。**失败降级**:上游异常返回 `{docs:[], degraded:true}` + `Cache-Control: no-store`,客户端 fallback 到手输。`limit` 上限 500。

### `GET /api/personalization-engine-schema[?force=1]`

ProfileValue 编辑器的 `(group, field)` 目录,解析自 personalization-engine `schema.yaml`。5 分钟 TTL;`?force=1` 绕过缓存。**降级**:上游挂时返回 last-good 缓存 + `X-PE-Schema-Stale: 1` 头,全丢才 503。

---

## 7. Webhook

### `POST /api/feishu/approval-callback`

飞书审批回调，流转 publish_order 状态：`pending_approval` → `approved` → 自动走 prod 发布逻辑 → `published`；或 `rejected`。

---

## 8. 数据模型（Prisma → MySQL）

7 张表，全部前缀 `smart_popup_`：

| 模型 | 表 | 关键字段 / 唯一约束 |
|---|---|---|
| `SmartPopupFact` | `smart_popup_fact` | `events_json` + **`source_json`**（2026-04-15 新增）+ `where_json` + `params_json`；唯一 `(tenant_id, name)` |
| `SmartPopupScene` | `smart_popup_scene` | `gb_feature_id` + `entry_event_key` + `entry_event_where`；唯一 `(bundle_id, scene_id)` |
| `SmartPopupRecipe` | `smart_popup_recipe` | `condition_json` + `fatigues_json` + `cms_id`；唯一 `(bundle_id, scene_id, recipe_id)` |
| `SmartPopupRulesPackage` | `smart_popup_rules_package` | `file_url` + `checksum` + `rules_json` + **`approval_state` / `idempotency_key` / `retry_count` / `last_state_transition_at`**（2026-04-16 PR-05）；唯一 `(bundle_id, env, version)` + **`(idempotency_key)`**；索引 `(approval_state, last_state_transition_at)` 供 worker 扫 stuck |
| `SmartPopupPublishOrder` | `smart_popup_publish_order` | `rules_json` + `status` + `staging_version` + `prod_version` + `approval_instance_id` + **`current_package_id`**（指向 approving 中的 package） |
| `SmartPopupTicket` | `smart_popup_ticket` | `hit_id` + `scene_id` + `recipe_id` + `user_id` + `zendesk_ticket_id`（弹窗转工单） |
| `SmartPopupAuditLog` | `smart_popup_audit_log` | `operator` + `action` + `target_version` + `detail` |
| `SmartPopupDryRunTask` | `smart_popup_dryrun_task` | `run_id`(UNIQUE)+ `status` + `start_date/end_date/sample_rate/config_json` + `scene_id/recipe_id/rules_version`;索引 `(scene_id, recipe_id)` / `(status)` / `(created_at)` |

**表结构 SSOT 在 Go 后端 `migrations/`**,Admin 通过 `prisma db pull` 同步,不要单边改。

---

## 9. 环境变量（部署/排障参考）

| 变量 | 作用 |
|---|---|
| `DATABASE_URL` | MySQL 连接串（Prisma） |
| `FEISHU_APPROVAL_CODE` | 飞书审批模板 code；不设时 approve-prod 自动通过（dev 模式） |
| `NEXT_PUBLIC_GROWTHBOOK_FEATURE_URL_TEMPLATE` | GrowthBook feature deep-link 模板 |
| S3 / region 相关 | `publishRulesSnapshotToS3` fan-out 到 US/EU/CN 各自 bucket |

---

## 10. 已知限制(截至 staging 分支 2026-04-20)

- 无前置鉴权（ADR #9，飞书 SSO + SpiceDB 待接入）
- 单租户（`tenant_id=default` 硬编码）
- `bundle_id` 字段在 Fact/Scene/Recipe 已废弃，仅 `rules_package` 和 `rules/bundle` 查询保留
- S3 已写 + DB 写失败 → 孤儿版本需人工清理
- Feishu 审批链路：Feishu 不可用时 `approve-prod` 会返回 502
- Admin 首页其他模块（保修 / 评分 / 工单）标记为 `coming-soon`，未提供实际 API

---

## 11. 近期接口/字段演进（读懂历史必看）

| 日期 | 变更 | 影响 |
|---|---|---|
| 2026-04-15 | Fact 新增 `source_json` 必填字段（Snowplow-native `FactSource`） | 旧的 POST /api/facts 调用缺字段会 400 |
| 2026-04-15 | Dry Run simulate events[] 改为 `SmartPopupEvent` 形状 | 旧的 `{event_key, data}` 形状不再接受 |
| 2026-04-15 | Global feature 重命名：`smart_popup_global_switch` → `global.switch`；`smart_popup_global_fatigue` → `global.fatigue` | Backend / SDK 按新名读取，硬编码旧名会切断链路 |
| 2026-04-16 | JsonLogic 可视化支持 `multipleOf`（% 取余糖） | "每 N 次触发"类规则现在可在 UI 上配置，运行时 JSON 仍是 `%` |
| 2026-04-16 | Approve-prod 异步化（PR-05）：Order + Package 双状态机、`Idempotency-Key` header、后台 worker 补偿 | 客户端重试需显式传 `Idempotency-Key`；排障要看 `rules_package.approval_state / retry_count / last_state_transition_at` |
| 2026-04-19 | Fatigue `window="day"` 废弃，改为 `rolling` + `window_days ∈ [1,90]`（issue #61） | 老规则 publish-time 报错需迁移；UI 添加 fatigue 的默认从 `MaxImpressions/session/max:1` 换成 `MinInterval/60s/scene` |
| 2026-04-19 | Fact operator 白名单新增 `LatestValue` | `VALID_OPERATORS` 共 3 个(当时);engine 仍通过 `fact_registry` 支持更多 |
| 2026-04-19 | Scene 页嵌 `BatchDryRunPanel`,DryRun 结果联 Superset dashboard(`preselect_filters` 预过滤 `dryrun_task_id`) | Mode B 现在有 UI 入口;draft config 可测未发布规则 |
| 2026-04-16 | FactEditor UI 重构为 "increment / clear" 两段 | 现有 API 不变;只影响编辑体验 |
| 2026-04-19 | **3-category fact 分类**(`event_derived` / `backend_resolved` / `local_evaluated`,SSOT = `admin/src/types/fact-category.ts`) | Fact 编辑器按 category 分岔渲染;recipe-linter 针对 host-contributed fact 加 termination 约束 |
| 2026-04-19 | Fact operator 新增 `ProfileValue`(backend_resolved)+ `LocalContextValue`(local_evaluated) | `VALID_OPERATORS` 扩到 5 个;但 `CREATABLE_OPERATORS` 只 4 个 — `LocalContextValue` 禁止用户创建,由 snapshot-assembler 注入 7 个预设(issue #79) |
| 2026-04-19 | `factSourceSchema` 扩到 4 变体(`payload` / `context` / `profile` / `local`),新增 operator↔source cross-field 校验 | PUT 时改 operator 不改 source 会被拒;create 侧也强校验,堵 smuggle 路径 |
| 2026-04-19 | `LOCAL_CONTEXT_FIELDS` v1 固化 7 个(time_hour_of_day / time_minute_of_hour / day_of_week / is_weekend / timezone_offset_minutes / language / region) | 字段表是 admin + engine + Dart SDK 三边 SSOT,改动必须三端同步;`day_of_week` 用 ISO 8601(Mon=1) |
| 2026-04-20 前后 | 新增 `/api/cms-contents` + `/api/personalization-engine-schema` + `/api/config/external-links` | RecipeEditor 的 cms_id 下拉、ProfileValue 的 group/field 下拉、前端外链拼接都走这几个代理 |
| 2026-04-20 前后 | Batch DryRun 落表 `smart_popup_dryrun_task` + 新路由 `/api/dryrun/tasks` | Mode B 可列历史、批量状态可复查;老的 fire-and-forget 行为被替代 |
| 2026-04 harden v40 | Recipe 新 fatigue 默认从 `MinInterval/60s` 调到 `MinInterval/172800s(2d)` | UI "添加疲劳度" 按钮默认值变了;旧 scripts 若假设 60s 会不一致 |

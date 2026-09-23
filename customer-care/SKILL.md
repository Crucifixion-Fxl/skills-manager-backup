---
name: customer-care
description: Customer Care 管理后台（SmartPopup 智能弹窗规则配置）操作指南。当用户需要查询/编辑 Fact / Scene / Recipe、发起发布单、做 Dry Run 验证、排查弹窗没触发、或查看线上生效规则时触发。
---

# customer-care

## Description

Customer Care 管理后台是 **SmartPopup 智能弹窗引擎的运营配置中枢**，基于 Next.js（App Router）全栈开发。运营在此直接编辑 `Fact / Scene / Recipe`，打包成 `rules_package` snapshot 发布到 S3；Go Backend 在运行时从 S3 读取并 resolve per-user 配置返回给 App。

**访问地址**：
- Staging: `https://customer-care-admin-staging.addx.live`
- Prod: 未知（推断：基于 staging 域名规律，可能为 `https://customer-care-admin.addx.live`，需实际验证）

**代码位置**：`gitlab.addx.ai/services/customer-care` → `admin/` 目录（默认 `staging` 分支）。参照 `staging` 分支的 Next.js App Router 结构：
- 首页模块导航：`admin/src/app/page.tsx`（智能弹窗 live，保修/评分/工单 coming-soon，qiankun 微前端壳）
- 页面：`admin/src/app/{facts,scenes,publish,audit,smart-popup/global}/page.tsx` + `scenes/[sceneId]/page.tsx` + `publish/[id]/page.tsx`
- API：`admin/src/app/api/**/route.ts`（20 条 route.ts）
- Prisma 模型：`admin/prisma/schema.prisma`（MySQL，7 张表）
- 输入校验 Zod：`admin/src/lib/schemas.ts`
- 领域类型（运行时 JSON 形状）：`admin/src/types/rule.ts`（含 `SmartPopupEvent` / `FactSource` / `RulesConfig`）

**使用者**：运营（配置规则 + 发布）、开发（排障 + 查线上配置）。

**完整 API 清单 / 请求样例 / 状态机** → [`references/api-reference.md`](references/api-reference.md)

## Rules

### 1. 认证

**当前 Admin 没有前置鉴权**（`docs/architecture/smart_popup/admin.md` §8 明确记录的 Pending ADR #9），任何能访问内网的人都可操作。本地账号示例：`admin@a4x.local`。

- **Why**：内部工具，早期版本未接入 SSO；`/api/feishu/approval-callback` 已存在但仅用于发布审批回调，不等于登录鉴权
- **How to apply**：写脚本调 API 时直接访问即可，无需携带 token；预留飞书 SSO 接入点，任何"当前认证方式"相关实现必须注明"待迁移到飞书 SSO + SpiceDB"

### 2. 数据通道与 SSOT

- **编辑态 SSOT = MySQL**（`smart_popup_fact / smart_popup_scene / smart_popup_recipe`）
- **运行态 SSOT = S3 rules snapshot**（`smart_popup/rules/<version>/rules.json` + `latest.json`）
- 发布流水线才是 DB → S3 的唯一通路；**禁止绕过 Admin 直接改 S3**
- Go Backend 只读本地 region 的 S3 bucket，不回读 DB

### 3. 三个核心概念（v3 schema）

| 概念 | 关键字段 | 全局/租户隔离 |
|---|---|---|
| **Fact** | `name` / `operator` / `events_json` / `source_json` / `where_json` / `params_json` / `isolation_field` | 全局（bundleId 字段废弃，写空字符串） |
| **Scene** | `scene_id` / `gb_feature_id` / `entry_event_key` / `entry_event_where` / `owner` | 全局 |
| **Recipe** | `recipe_id` / `scene_id` / `condition_json`（JsonLogic）/ `fatigues_json` / `cms_id` | 全局，按 scene 分组 |

### 4. Fact 3-Category 分类 + `source_json`（2026-04-19 v3）

Fact 按 **值的来源** 分三类(`admin/src/types/fact-category.ts` `OPERATOR_TO_CATEGORY` 为 SSOT):

| Category | operator | 值来源 | events/where 要求 |
|---|---|---|---|
| `event_derived` | `EventCounter` / `BucketedWindowCounter` / `LatestValue` | 设备端从事件流累计/取最新 | 必须至少 1 个 event |
| `backend_resolved` | `ProfileValue` | Go 后端 resolve-response 的 `profile_facts` 一次性下发 | **禁止** events/where |
| `local_evaluated` | `LocalContextValue` | SDK 在 dispatch 时从 `LocalContextProvider` 取 | **禁止** events/where |

**`source_json` 4 变体**(`factSourceSchema`,POST/PUT `/api/facts` 时必填):

```json
{ "type": "payload", "path": "reason" }                                // event_derived
{ "type": "context", "vendor": "ai.addx", "name": "device_ctx", "path": "sn" }  // event_derived
{ "type": "profile", "entity": "user", "group": "bind_stats", "field": "fail_count_7d" }  // backend_resolved
{ "type": "local",   "field": "is_weekend" }                           // local_evaluated
```

- **Cross-field 硬校验**: `operator=ProfileValue ⇔ source.type=profile`、`operator=LocalContextValue ⇔ source.type=local`,否则 Zod 400(PUT 侧同样校验,堵住"先 EventCounter 后改 ProfileValue 但保留 events_json"的 smuggle 路径)
- `path` 点号路径,**不要带** `event.payload.` / `event.context.<v>.<n>.` 前缀
- UI 里通过 `SchemaPicker`(event_derived)/ PE schema 下拉(profile)/ LOCAL_CONTEXT_FIELDS 下拉(local)选择
- 设计文档:`docs/plans/2026-04-15-smart-popup-snowplow-event-interface-design.md`、`docs/architecture/smart_popup/profile-fact.md`、`docs/plans/2026-04-19-smart-popup-local-context-facts.md`

### 4b. LocalContextValue 预设 7 字段(用户不可创建)

`admin/src/lib/local-context-fields.ts` 是 SSOT(和 engine `operators/local_context_value.js` + SDK `local_context_provider.dart` 三边锁步):

| field | type | self_correcting | 说明 |
|---|---|---|---|
| `time_hour_of_day` | number | ✅ | 0-23,每小时推进 |
| `time_minute_of_hour` | number | ✅ | 0-59 |
| `day_of_week` | number | ❌ | ISO 8601(周一=1,周日=7),**4 个子系统对齐** |
| `is_weekend` | boolean | ❌ | |
| `timezone_offset_minutes` | number | ❌ | |
| `language` | string | ❌ | |
| `region` | string | ❌ | |

- **CREATABLE_OPERATORS 只有 4 个**(`EventCounter` / `BucketedWindowCounter` / `LatestValue` / `ProfileValue`)— `LocalContextValue` 用户**创建会被 Zod 拒**;7 个预设由 `snapshot-assembler` 发布时自动注入 prod snapshot,历史遗留用户创建的 LocalContextValue 行会被 hard-delete(issue #79)
- **`self_correcting=true` 的字段引用时 recipe 可免 MaxImpressions fatigue**(时间自然越过阈值会停);`false` 的字段(language/region/day_of_week 等)必须带 MaxImpressions,否则 recipe-linter 告警 — 同 ProfileValue 的 termination 约束

### 5. 命名约束(硬性)

- `fact.name` / `scene_id` / `recipe_id` / `entry_event.event` 必须 **snake_case**(正则 `^[a-z][a-z0-9_]*$`),API 层 Zod 校验会拒绝其他
- **VALID_OPERATORS 共 5 个**:`EventCounter` / `BucketedWindowCounter` / `LatestValue` / `ProfileValue` / `LocalContextValue`(`admin/src/lib/schemas.ts`)— 其中 **CREATABLE_OPERATORS 只有 4 个**,`LocalContextValue` 不接受用户创建(见 §4b);运行时 engine 还支持 `FirstEventHappened` 等,但 Admin 白名单就这 5 个
- `BucketedWindowCounter` 的 `params.window_seconds ∈ [60, 86400]`
- Fatigue `window` 支持 `session | rolling | lifetime`(2026-04-19 起,issue #61)— **legacy `"day"` 已废弃,publish-time 会主动拒绝**(防止直接写 DB / S3 绕过 UI 的迁移漏洗);`"rolling"` 模式必须配 `window_days ∈ [1, 90]`,其他 window 传 `window_days` 也会报错
- Recipe 新增 fatigue 的默认模板(UI "添加疲劳度" 按钮)为 **`{func:"MinInterval", params:{interval_sec:172800, scope:"scene"}}`**(2026-04 "harden v40" 调整 — 冷却 2 天而不是之前的 60 秒;老默认过短几乎等于不节流,老老默认 `MaxImpressions/session/max:1` 又会把 recipe 静默限到只触发一次)
- **JsonLogic 可视化支持 `multipleOf`**(% 取余糖,2026-04-16 引入)— 用于"每 N 次触发"类场景,底层展开成 `{ "==": [{ "%": [x, N] }, 0] }` 并在发布/运行时双向 round-trip

### 6. 不可变字段（改了就是故障）

- **`scene_id` 永不 rename** — 会破坏埋点历史和 AB 基线
- **`entry_event` 永不修改** — 同上
- 需要"改名"时，**创建新 scene + deprecate 旧的**，不要原地改

### 7. 删除约束

- **Fact 被任何 recipe condition 或 scene entry_event.where 引用时，DELETE 返回 409**（源码 `admin/src/app/api/facts/[id]/route.ts` 扫描 `"facts.<name>"` 文本引用）
- **Fact 出现在 prod snapshot 里也无法物理删**（`prod_snapshot` 预检会拒绝，保证线上引擎不会遇到缺 fact）
- 删 Fact 前必须先清空所有引用
- Scene 删除策略是**归档而非物理删除**（保留 GrowthBook feature 映射）；Recipe 和 Fact 可物理删（前提：不在 prod snapshot 里、无引用）

### 8. Publish 流水线 + 状态机

**Order 级状态机**（`smart_popup_publish_order.status`）：

```
pending_staging ─→ staging_ok ─→ pending_approval ─→ approving ─→ published
                                     │
                                     └─→ rejected
```

**Package 级状态机**（`smart_popup_rules_package.approval_state`，2026-04-16 PR-05 新增，VARCHAR+CHECK 而非 ENUM 便于未来加状态）：

```
approving → s3_uploading → s3_uploaded → db_committed → notified
                              │
                              └→ rolled_back / failed  （retry ≥ 3 或补偿成功的终态）
```

- `POST /api/publish-orders` 走 staging 同步：组装 snapshot → JSON Schema 校验 → GrowthBook 同步校验 → SHA-256 checksum → fan-out 写 US/EU/CN S3 → INSERT `rules_package(env=staging)` → 状态 `staging_ok`
- 发 prod 走 `POST /api/publish-orders/[id]/approve-prod`（**2026-04-16 起改为异步 + 幂等**，PR-05）：
  - 请求可带 `Idempotency-Key` header（不传自动生成 UUID）；**同 key 重试返回同一 package**，由 `rules_package.uk_idempotency_key` 唯一约束兜底
  - **Feishu 配置了**（`FEISHU_APPROVAL_CODE`）→ 创建审批实例，order 进入 `pending_approval`，等飞书回调
  - **Feishu 未配置**（dev / staging） → 事务内：预留 prod version（max+1）、建 `rules_package(approval_state=approving)`、order 置 `approving` 并写 `current_package_id`；HTTP 立即返回；`advanceApprovalState()` fire-and-forget 把 package 推到 `notified`
- 后台 worker（`admin/src/lib/approval-worker.ts`）周期扫 `(approval_state, last_state_transition_at)` 索引：stuck 5 分钟以上或 `retry_count` 超 3 → 翻 `failed / rolled_back`；顺带清 S3 孤儿（S3 已上传但 DB 没 package 行的 version）
- **排障看 `publish_order.status` 不够**：卡在 `approving` 时必须看对应 `rules_package.approval_state / retry_count / last_state_transition_at`；`status=NULL` 的 package 是 PR-05 之前的 legacy 行，worker 不碰

### 9. 发布购物车模式（Release Cart）

前端发布页用 `localStorage` key `smart-popup.release-cart` 暂存用户勾选的 `{ scene_id, recipe_ids[] }[]`（`admin/src/lib/release-cart.ts`）。

- 用户在 `/scenes/[sceneId]` 勾选 recipe → 加入 cart → 跳 `/publish`
- `/publish` 页面读 cart → 展示预览 diff → 用户点"发布"才真正 `POST /api/publish-orders`
- Cart 自动去重（同 scene_id 的 recipe_id 合并为 Set + 排序）
- 发布成功后 cart 不自动清空，需用户手动清除

### 10. Scene 创建是 Saga

`POST /api/scenes` 会做三步：
1. INSERT `smart_popup_scene`（DB 唯一索引挡并发）
2. 调 GrowthBook `POST /api/v1/features` 创建 `scene.<scene_id>` feature（string 类型，default `"control"`）
3. 如果 GrowthBook 失败 → **补偿删除** Admin 行，返回 502
4. 成功后再 best-effort 创建 GrowthBook Experiment（失败不回滚）

**含义**：Scene 创建必须保证 GrowthBook 可达；否则会收到 502。Recipe 的 variation 会在 `POST /api/recipes` 时 best-effort 同步到 GrowthBook Experiment。

### 11. Dry Run 使用场景

| Mode | 接口 | 执行环境 | 适用 |
|---|---|---|---|
| **A(simulate)** | `POST /api/dryrun/simulate` | Node VM 同步 | 单 scene+recipe 即时验证(事件序列手写) |
| **B(batch)** | `POST /api/dryrun/batch` → 返回 `run_id` → 轮询 `GET /api/dryrun/batch/[runId]` | Dagster + PyMiniRacer 异步 | 批量回溯(事件从 Athena 拉);Scene 页嵌 `BatchDryRunPanel`(2026-04-19),支持 draft `config_json` 测试**未发布**规则,结果附 Superset dashboard 深链(`dryrun_task_id` 预过滤) |

- **Mode A 不要跑大量事件**(同步阻塞 Node process);批量验证走 Mode B
- 可传 `config_json: "active"` 用当前生效规则,或传完整 `RulesConfig` 用草稿版本
- **events[] 使用 Snowplow-native 形状**(`event_name` + `event_vendor` + `timestamp` + `payload` + `contexts[]`),不再是旧的 `{event_key, data}`;详见 `SmartPopupEvent` 接口
- **Batch 任务已持久化**(2026-04 起,表 `smart_popup_dryrun_task`)— `GET /api/dryrun/tasks?scene_id=&recipe_id=&limit=` 可列历史,状态在 batch 状态查询时被同步回 DB

### 12. 查线上真实生效配置

`GET /api/rules/bundle?bundle_id=com.kb.kiwibit&env=staging|prod` — 返回**当前 S3 latest.json 指向的 rules**。排查"App 为何没弹"第一步就查这个，和 DB 里编辑态对比。

### 13. 租户与 bundle

- 几乎所有路由硬编码 `tenantId = "default"`，目前是单租户；多租户改造是后续工作
- Facts/Scenes/Recipes 的 `bundleId` 字段已废弃（写 `""`），**bundle 定向通过 GrowthBook targeting 实现**，不要再往 bundleId 里塞业务意义
- `rules/bundle` 查询例外：`bundle_id` 默认 `com.kb.kiwibit`，多产品读规则时需传

### 13b. 辅助只读接口(排障/UI 组合常用)

- `GET /api/cms-contents?locale=&limit=` — 服务端代理到 Payload Marketing CMS(RecipeEditor `cms_id` 下拉用,issue #7);**降级静默**:上游失败返回 `{docs:[], degraded:true}`,UI 回退到手输
- `GET /api/personalization-engine-schema[?force=1]` — ProfileValue 编辑器的 `(group, field)` 目录,5 分钟缓存;上游挂了返回 last-good + `X-PE-Schema-Stale: 1` 头,全丢才 503
- `GET /api/config/external-links` — 返回 `{growthbook_feature_template, cms_content_template}`,客户端拼外链用(模板走 runtime env,不用 `NEXT_PUBLIC_*` 以避免每环境重打镜像)
- `GET /api/publish-orders/[id]/diff?env=staging|prod` — 该 order 的 `rules_json` vs 目标 env 当前 active 包的结构化 diff;**env 拼写错(如 `production`)返回 400**(旧版本会静默空 diff)
- `GET /api/publish-orders/[id]/snapshot?type=staging|prod&region=US|EU|CN` — 拉该 order 产出的历史 snapshot 原文 + S3 URI

### 14. GrowthBook feature 命名(bootstrap + 使用)

Admin 启动时（`instrumentation.ts`）自动 bootstrap 全局 feature：

| 用途 | feature id | 类型 / default |
|---|---|---|
| Scene feature（每个 scene 一个，创建时同步） | `scene.<scene_id>` | string，default `"control"` |
| 全局开关 | `global.switch` | boolean，default `true` |
| 全局疲劳度 | `global.fatigue` | json，default `{ min_interval_sec: 0 }` |

> ⚠️ `global.switch` / `global.fatigue` 的命名 2026-04 调整过（原 `smart_popup_global_switch` / `smart_popup_global_fatigue`），Backend / SDK 依这两个 feature id 读取，改名会切断生效链路。Helper: `admin/src/lib/growthbook-features.ts` 的 `sceneFeatureId` / `globalSwitchFeatureId` / `globalFatigueFeatureId`。

### 15. 禁止行为

- 禁止绕过 Admin 直接写 `smart_popup_rules_package` 或 S3
- 禁止在生产库上直接改 scene_id / entry_event
- 禁止对 prod 跑 Mode A dry run 大批量事件
- 禁止把 `created_by` 硬填成真实账号以外的值（审计追责依赖此字段）

## Examples

### Bad

```
用户："把 fact `bind_fail_count` 删了"
AI：直接 DELETE /api/facts/<id> → 返回 409（被 recipe 引用）→ 用户困惑
```
问题：没先扫描引用。

```
用户："把 scene `device_bind_fail` 改名为 `bind_failed`"
AI：PATCH /api/scenes/<id> 传 scene_id
```
问题：`scene_id` 不可变，会破坏埋点历史 + AB 基线。PATCH 接口也不支持此字段。

```
用户："帮我预演一下 5 万条事件下的命中率"
AI：POST /api/dryrun/simulate + 5 万条 events 数组
```
问题：Mode A 是同步 Node VM，会阻塞 Admin 进程。批量必须走 Mode B。

```
用户："新建一个 fact `bind_fail_cnt` 统计 device_bind_fail 次数"
AI：POST /api/facts 只传 events_json / where_json / params_json，没传 source_json
```
问题：2026-04-15 起 `source_json` 是必填字段（Zod 会 400）。需要明确取值来源是 `event.payload` 还是某个 Snowplow context。

```
用户："加个用户维度 fact,从后端 profile 里取 `bind_stats.fail_count_7d`"
AI：POST /api/facts 传 operator=ProfileValue + source={type:profile,...} + events_json={device_bind_fail:increment}
```
问题:ProfileValue 是 backend_resolved 类,**禁止订阅事件和 where 过滤**(Zod cross-field 校验拒绝)。events_json 必须留空 `{}`、where_json 必须不传。LocalContextValue 同此约束。

```
用户："建个 fact 记录是不是周末,用 is_weekend"
AI：POST /api/facts operator=LocalContextValue + source={type:local,field:is_weekend}
```
问题:`LocalContextValue` 不在 `CREATABLE_OPERATORS` 白名单内(Zod 400)。7 个预设 local 字段由 snapshot-assembler 自动注入,**直接在 recipe condition 里用 `facts.is_weekend` 即可**,不需要建 fact 行。

```
用户："把规则同步到 prod"
AI：直接写 S3 prod bucket
```
问题：绕过了 Publish 流水线 + Feishu 审批 + 审计 + checksum 校验。必须走 `POST /api/publish-orders/[id]/approve-prod`。

### Good

```
用户："把 fact `bind_fail_count` 删了"
AI：1) GET /api/recipes 扫 conditionJson 里 "facts.bind_fail_count"
    2) GET /api/scenes 扫 entryEventWhere 里同样 token
    3) 列出引用点给用户，问是否先改 recipe / 否则不能删
    4) 确认无引用后 DELETE /api/facts/<id>
```

```
用户："App 上 `device_bind_fail` 场景的弹窗不显示了"
AI：
  1) GET /api/rules/bundle?env=prod → 找 scene 节点和其 recipes
  2) GET /api/scenes 看 Admin DB 里的 entryEvent / changeStatus（是否有未发布改动）
  3) 检查 GrowthBook feature `scene.device_bind_fail` 是否 enabled（响应中 featureEnabled 字段）
  4) 若 rules 一致、feature 开启 → 进下一层排查 SDK 缓存 / fatigue / CMS
```

```
用户："发一个新版本到 prod"
AI：
  1) POST /api/publish-orders/preview 预组装看 diff / warnings
  2) POST /api/dryrun/simulate 用关键事件序列本地验一下
  3) POST /api/publish-orders（自动到 staging）
  4) 在 staging 环境业务侧验证
  5) POST /api/publish-orders/<id>/approve-prod 触发飞书审批
  6) 审批通过 → 飞书回调触发 prod 发布
```

## References

- [`references/api-reference.md`](references/api-reference.md) — 完整 API 清单、状态机、请求样例
- 源码仓库：`https://gitlab.addx.ai/services/customer-care/-/tree/staging/admin`
- 架构文档：`docs/architecture/smart_popup/admin.md`（仓库 `staging` 分支）
- Prisma schema（字段权威）：`admin/prisma/schema.prisma`
- Zod 输入校验（写操作字段权威）：`admin/src/lib/schemas.ts`
- 领域类型（运行时 JSON 形状）：`admin/src/types/rule.ts`

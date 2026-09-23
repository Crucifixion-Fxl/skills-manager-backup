# GrowthBook 集成方法论

专门总结 ops console ↔ GrowthBook（下文 GB）的所有可复用模式。内容
一半来自 engagement admin（`admin/src/lib/growthbook-api.ts`、
`admin/src/lib/gb-drift.ts`、`admin/src/app/api/growthbook/features/[slug]/route.ts`），
一半来自 customer-care admin（`customer-care/admin/src/lib/growthbook-api.ts`、
`growthbook-bootstrap.ts`、`api/scenes/route.ts`、`api/recipes/route.ts`）。
这两边几乎所有东西都踩过、试过、改过一轮，凡是写在这里的，都能
在源码里找到锚点。

## 1. 适用范围

Ops console 跟 GB 有两个方向的交互，缺一条都不行：

- **Read 路径** — admin 需要渲染 GB 当前状态：feature 是否 archived、
  experiment 在哪些 environment 开了、rules 里有哪些 variation value、
  default value 是什么。这些决定了 list 页的 badge、variant 行的
  "GB 已路由 / 未路由" 提示、publish 按钮是 block 还是 go。
- **Write 路径** — admin 创建 touchpoint / scene / recipe 时要在 GB
  里建出 feature 骨架和 experiment，让 operator 在 GB UI 打开就能
  直接搜到；而不是让他先手建 feature 再回 admin 绑 key。

两条路径的共同约束：
- GB API token 绝不能进浏览器 bundle。
- GB REST API 不是 JSON Merge Patch，`PATCH /features/{id}`（和 scenes
  创建用到的 `POST /features/{id}`）都是**整份替换**语义。写之前
  不先 GET 就直接覆盖，操作员在 GB UI 手改的字段会瞬间被抹掉。
- 没有"新增单个 variation"的原子 endpoint。每次同步都必须整份推。
- GB 的 Admin REST API 偶尔会 5xx / 429，不是稳态依赖；admin 侧必
  须有重试 + 超时 + fallback。

凡是做触点系统（engagement）、弹窗系统（customer-care SmartPopup）、
付费墙投放系统，都会完整走这两条路径；少数纯"编辑内容、runtime 自
己查 GB"的场景可以只走 read 路径，但仍然要踩到本章大部分坑。

## 2. Read 路径：admin 做 server-side proxy

**架构**：前端不直调 GB；admin 开一个 `/api/growthbook/features/[slug]`
路由做服务端代理，带 30s 内存缓存 + 统一错误映射 + 反向 deep-link。

```
browser → admin Next.js route → getFeature(featureId) → GB REST API
                                      ↓
                               30s Map cache
```

四个非协商点：

1. **Token 不进浏览器**。`GROWTHBOOK_API_TOKEN` 只在 server action /
   API route 里读。browser bundle 只能看到最终渲染好的 `variationValues`
   和 `deepLink`。
2. **30s in-process 缓存**。list 页一次渲染 100 个触点 = 如果每
   个都直调 GB 就是 100 次 round-trip；30s TTL 把成本压到可接受。
   engagement 的 `featureCache` 就是一个 `Map<string, {result, fetchedAt}>`，
   简单到不需要 Redis。
3. **404 → `null`，不抛**。`getFeature` 对 404 返回 `null`，让调用
   方区分"这个 feature 还没建"（正常，刚创建没 write-through）和
   "GB API 挂了"（错误）。customer-care 还额外把 400 + body 含
   `"Could not find a feature with that key"` 也映射成 `null`——因
   为老版 GB 对不存在的 key 不一定给 404。
4. **一次 round-trip 给齐渲染数据**。proxy route 返回
   `{feature, variationValues, drift, deepLink}` 四件套：
   - `feature`: 原始 GB 对象（前端可能要显示 archived / valueType）
   - `variationValues`: `extractVariationValues(feature)` 拉平后的
     variation 数组（experiment rule + force rule 都扫）
   - `drift`: `detectDrift(adminVariants, gbSummary)` 的结果
   - `deepLink`: `GROWTHBOOK_UI_URL` 拼好的 GB feature 页 URL

锚点：engagement `admin/src/app/api/growthbook/features/[slug]/route.ts`
整文件。

## 3. Write 路径：GET → merge → PATCH 强制顺序

GB 的 update endpoint 是**整份替换**，不是 JSON Merge Patch。以下
代码看起来无害，但会毁天灭地：

```ts
// ❌ DANGEROUS — 覆盖 operator 在 GB UI 刚加的所有字段
await req(`/api/v1/features/${id}`, {
  method: "POST",
  body: JSON.stringify({
    defaultValue: "treatment_a",  // 只想改这一个字段
  }),
});
// 结果: description / tags / owner / environmentSettings 全丢
```

正确的写回必须是 `GET → 本地 merge → 整份 POST`：

```ts
// ✅ SAFE
const current = await getFeature(id);
if (!current) throw new Error("feature missing");
await req(`/api/v1/features/${id}`, {
  method: "POST",
  body: JSON.stringify({
    ...current,                // 保留 operator 改过的所有字段
    defaultValue: "treatment_a",
  }),
});
```

engagement `updateFeatureEnabled(id, false)` 目前是个特例——它只写
`{defaultValue: "", archived: true}` 两个字段，依赖的是"这种情况下
我们主动想丢弃其他 rules"的语义（touchpoint 被 archive 了，其他
字段本来也该清）。但凡是"operator 在 GB 里还在改"的 feature，写
回前必须先 GET。

**环境层级陷阱**：`environmentSettings` 是
`{[envName]: {enabled, rules}}` 的 map。想在 `prod` 加一条 rule，必
须先读出整个 `environmentSettings`，只改 `environmentSettings.prod.rules`，
其他 env 原封保留。否则 `staging` 环境下的 rules 全丢。

## 4. Experiment vs feature rule 两种 variations shape

GB 里"有 variation 这个概念"的地方其实是两个不同 API：

### (a) 独立 experiment：`POST /api/v1/experiments`

```ts
{
  datasourceId, assignmentQueryId, trackingKey, name,
  project, hashAttribute: "userId", hashVersion: 2,
  variations: [
    { key: "control",     name: "Control",     description: "" },
    { key: "treatment_a", name: "Treatment A", description: "" },
  ],
  phases: [],  // 无流量，operator 在 UI 里配
}
```

variation 里能塞 `description`、`screenshots`（图片 URL）——这两个
字段是 admin 侧把"人话 / 预览图"挂回 GB UI 的唯一通道，值得写。
变更只能通过 `POST /api/v1/experiments/{id}` 整份替换 `variations`。

### (b) feature rule 内嵌：`PATCH /api/v1/features/{id}` 的
`environmentSettings.{env}.rules[]`

```ts
{
  environmentSettings: {
    prod: {
      enabled: true,
      rules: [
        {
          type: "experiment",
          trackingKey: "engagement_summer_banner_experience",
          variations: [
            { value: "control",     weight: 0.5 },
            { value: "treatment_a", weight: 0.5 },
          ],
        },
      ],
    },
  },
}
```

这里的 variation **只有 `value` + `weight`**，没有 name / description /
screenshots——rule 嵌的是最小形态。

**什么时候用哪种**：
- 想让 GB 跑 stats engine、算 uplift、看报告 → 用 (a) 独立 experiment。
  engagement 和 customer-care 都是这条。
- 只想在某个 feature 上分流、不关心统计分析 → 用 (b) feature rule。
  runtime-feature-flag 心智，不推荐承载正式 A/B。

engagement `createExperiment` + `syncRecipeVariations` 走 (a)。
customer-care `createExperiment` 也走 (a) + `updateExperimentVariations`
也是 (a) 的整份替换。

## 5. No-atomic-add：admin 必须是 source of truth

GB 没有 `POST /api/v1/experiments/{id}/variations` 这种"加一个 variation"
的 endpoint。想加必须整份推。这个 API 约束决定了一件战略事情：

**admin 的 DB 是所有 variation 的 source of truth，GB 只是 mirror。**

每次触点 / scene 里增删改 variant，admin 都：
1. 本地 DB 先写（`UNIQUE(touchpointId, variantKey)` 保护幂等）
2. 从 DB 拉出**完整的 variation 清单**（不是 diff）
3. 调 `syncRecipeVariations` / `updateExperimentVariations` 整份推给 GB

推反了（让 GB 当 source of truth、admin 只读）的后果：operator 在
GB UI 手加一个 variation，admin 本地没有对应 content，下次同步 admin
把整份推上来，GB 里手加的那条被抹掉。用户已经在 GB 配了流量到那
个 variation，流量直接变成 404。

这也是为什么 drift detection 存在：admin 本地状态推给 GB 这条单向
链路不能保证"operator 没有偷偷在 GB 里改东西"，所以额外需要一个
纯 diff 函数（`detectDrift`）在 publish 前做 reconciliation check。

## 6. `syncRecipeVariations` find-or-create 模式

这个模式源自 customer-care（`POST /api/recipes` 写完 DB 后，best-effort
同步到 GB experiment），engagement 复用同一套逻辑在
`syncRecipeVariations`。流程：

```ts
async function syncRecipeVariations({ trackingKey, name, variations }) {
  const existing = await findExperimentByTrackingKey(trackingKey);
  if (existing) {
    await updateExperimentVariations(existing.id, variations);  // POST 整份替换
    return { id: existing.id };
  }
  return createExperiment({ trackingKey, name, variations });    // POST 新建
}
```

三个关键属性：

- **幂等**。相同入参调 N 次结果一样；不存在就建，存在就更新 variations。
  retry 安全。
- **trackingKey 作为主键**。`findExperimentByTrackingKey` 走 GB 的
  `GET /api/v1/experiments?trackingKey=...&limit=1`。这个 key 必须
  admin 侧稳定生成（engagement 用 `engagement_{slug}_{dimension}`；
  customer-care 用 `scene.{sceneId}`）；换了就等于新开一个 experiment，
  老的流量和数据断掉。
- **Best-effort**，不阻断主流程。customer-care 的 recipes `POST`
  route 在 `try / catch` 里调这个同步逻辑，失败只 `console.warn`。
  因为 admin DB 已经写入了，GB 写失败不应该让用户以为整个操作失
  败——operator 可以事后在 "reconcile" 按钮里手工重试。

锚点：engagement `growthbook-api.ts:367-395`；customer-care
`api/recipes/route.ts:116-137` + `api/recipes/[id]/route.ts:130+`。

## 7. 错误分类与重试

两边 `growthbook-api.ts` 都用同一套错误 + req wrapper（一字不差，
说明这套被 battle-tested 过）：

```ts
class FeatureAlreadyExistsError extends Error { featureId }
class GrowthBookHttpError extends Error { status, body }
```

### req wrapper 行为

- **Timeout**：`AbortSignal.timeout(10_000)`。10s 是上限；超过就当作
  transient 失败走重试分支。
- **Retry 预算**：`MAX_RETRY_ATTEMPTS = 3`。
- **Backoff**：`250 * 2^attempt` → 250ms / 500ms / 1s。
- **429 特殊处理**：优先读 `Retry-After` header（秒），capped 到 10s；
  读不到 fallback 到 backoff。
- **5xx 重试**，4xx（除 409/429 之外）**不重试**直接抛。
  rationale: 4xx 是 admin 侧 payload 错，重试只会继续错。
- **409 在 `createFeature` 层映射**成 `FeatureAlreadyExistsError`；
  调用方可以用 `instanceof` 判断"已存在"做幂等处理。
- 最后抛出的类型只有两种：`GrowthBookHttpError`（非 2xx 耗尽重试）
  和 `FeatureAlreadyExistsError`（409 from createFeature）。调用
  方的 `try / catch` 写起来清爽。

### 调用方怎么分发

customer-care `POST /api/scenes` 的做法是典范：
- `FeatureAlreadyExistsError` → 返回 409 + 提示"换一个 id"
- `GrowthBookHttpError` → 返回 502 + "GB API rejected" + compensation
  删除刚写的 admin 行（详见第 8 节）
- 其他错误 → 返回 500

engagement proxy route 反过来，对 `GrowthBookHttpError` 不抛 5xx，
而是把 `gbReached = false` 塞进 drift result 回前端——因为 read 路
径不能让 GB 故障导致 admin 列表页挂掉。

## 8. Feature skeleton 与 disable pattern

### 创建时同步建 GB feature

触点 / scene 创建的瞬间就调 `createFeature`，让 operator 在 GB 里
一打开就能搜到。customer-care `api/scenes/route.ts` 的流程：

```
Step 1: 写 admin DB row
Step 2: 开事务
Step 3: createSmartPopupFeature (GB)
Step 4: 若 Step 3 失败 → 删 admin DB 行（compensation）
Step 3b: best-effort createExperiment（失败只 warn，不 compensate）
```

Step 3 失败就 compensate 是因为"admin 有行、GB 没 feature"是最糟
的状态——operator 在 admin 看到触点以为已经上线，流量实际永远没
路由过来。删掉 admin 行让 operator 看到失败、重试即可。

Step 3b（auto-create experiment）不 compensate，因为 experiment 缺
失只影响"GB UI 里没 experiment 对象"，runtime 仍然通过 feature 的
default value / rules 工作；experiment 可以事后补。

### Disable / archive 用翻 flag 而不是删 feature

```ts
updateFeatureEnabled(id, false)
// 写 { archived: true, defaultValue: "" }
```

不用 `DELETE`。rationale：
- GB 删 feature 不可逆，若 admin 只是 soft-delete 触点、之后要
  revive，feature 没了 experiment 也跟着废。
- `archived: true` 在 GB UI 里还能看见（历史可查）但不进 SDK
  evaluation → 等价"关"。
- `defaultValue: ""` 让任何没走到 rule 的用户拿到空字符串，runtime
  侧的 content lookup miss 自然走降级路径。

### 幂等 bootstrap

customer-care `ensureGlobalFeatures` 的模式值得抄：admin 启动时调
一次，409 静默跳过、其他错误 `console.warn` 不 crash。这保证了
"无论从哪台新机器起 admin、无论 GB 里缺什么全局 feature，总能自
愈到预期状态"。engagement 虽然还没全局 feature 需求，但这个模式
在将来加"全局 kill switch / 全局 fatigue"时直接复用即可。

## 9. 反向 nav 升级路径（Chrome 端，分四层）

Read/write 都做好之后最后一个问题：operator 经常在 GB UI 里反查
"这个 feature 对应 admin 里哪个触点"。解法分四层，**不要一开始就
做最高级**。

### Level 0：admin → GB deep-link（零成本）

每个 variant 行 / feature id 旁边一个 "Open in GB" 小图标。
`GROWTHBOOK_UI_URL` 环境变量 + 简单字符串拼接就行，engagement
的 `buildDeepLink` 就是这个。customer-care 进一步抽成
`external-links.ts` 里的 `growthbookFeatureUrl(key)` + 可通过
runtime env `GROWTHBOOK_FEATURE_URL_TEMPLATE` 覆盖模板（`{key}` 占位），
这样同一份镜像在 staging / prod 指不同 GB 实例。

- Cost: 半天。
- Benefit: admin → GB 单向跳转全覆盖。
- **先做这个**，绝大多数场景 Level 0 就够用。

### Level 1：GB → admin bookmarklet（20 行 JS）

书签栏里塞一段 `javascript:` URL：

```js
javascript:(function(){
  const m = location.pathname.match(/\/features\/(engagement_[\w-]+)/);
  if (!m) return alert("不是 engagement feature 页");
  window.open("https://admin.engagement.internal/touchpoints/" +
    m[1].replace(/^engagement_/, "").replace(/_(experience|fatigue)$/, ""));
})();
```

- Cost: 几小时写 + 每个 operator 拖一次书签。
- Benefit: GB → admin 反向跳转，零安装、跨 Chrome / Firefox /
  Safari / Edge。
- 升级条件：**operator 日均反查次数 > 3 且抱怨需要手复制 feature id**。
  小于这个量级 Level 0 + Cmd+F 就够了。

### Level 2：Tampermonkey 用户脚本

在 GB 页面注入一个 "Open in Admin" 按钮到 feature 详情页的 header。
比 bookmarklet 稳（不用每次点书签）、能注入 overlay / tooltip。

- Cost: 1-2 天写 + 每个 operator 装 Tampermonkey + 装脚本。
- Benefit: 看起来"集成了"；可以在 GB 页面上显示 admin 侧的 drift
  状态 overlay。
- 升级条件：**operator 日均反查 > 10 且要在 GB 页面看 admin 元数据**。

### Level 3：Chrome extension

MV3 extension，正经的 side panel 或 content script，开发者工具
级别的集成。

- Cost: 1-2 周写 + Chrome Web Store 审核 or 内部签名分发 + 长期
  MV3 breaking change 维护。
- Benefit: 真正的双向集成，能做侧栏、inline overlay、bulk 操作。
- 升级条件：**Level 2 脚本已经承载了多个关键 workflow，operator
  抱怨装 Tampermonkey 麻烦 / Chrome 更新破坏脚本**。这个阶段通常
  说明 ops console 本身应该优先做更深的内部 nav 集成，extension
  只是补丁。

**不要跳级**。见过团队上来就做 extension 结果审核卡两周、没人装
的惨剧。Level 0 做到，再按量化 signal 往上爬。

## 10. Drift detection（跨引用）

Write 路径只保证"admin 推给 GB 的 API call 不报错"。不保证"推完
之后 admin 和 GB 真的对齐"——中间可能有 operator 在 GB UI 动手
改、admin 写成功 GB 写失败、或者 GB 的 variation value 打错一个
字母。

这个对齐由 `detectDrift(adminVariants, gbFeature)` 负责，在
`admin/src/lib/gb-drift.ts`：
- `missingContent`: GB 路由到了 variantKey 但 admin 没 content →
  BLOCK publish
- `orphanVariants`: admin 有 content 但 GB 不引用 → WARN
- `gbUnreachable`: GB API 挂了 → BLOCK publish

详见 `cross-system-variant-key.md` 的 "drift detection" 章节。本
章只负责 API shape 正确；"两边实际对齐"是 publish preflight 的事，
两者是正交的 concern。

## 11. 反模式（都踩过）

1. **直接 PATCH 新字段不先 GET**。看起来 JSON Merge Patch 的代码
   在 GB 上是整份替换，你以为只改了 defaultValue，结果
   environmentSettings 全被你抹了。见第 3 节。

2. **把完整 content JSON 塞进 variation value**。`value:
   "{\"cmsSlug\":\"...\",\"cta\":{...}}"` 看似把一切都搬到 GB 上
   了，实际：(a) GB UI 的 variation 编辑框是单行 input，读不了；
   (b) snapshot 的原子性、content validation、catalog 检查全没了；
   (c) 任何 content 改动都要走 GB API write，权限和审计全错位。
   正解：variation value 只放 `variantKey` 这个字符串 ID，content
   留在 admin，见 `cross-system-variant-key.md`。

3. **admin write 不做 idempotency**。第一次 `createFeature` 超时
   但 GB 侧其实写成功了，admin 侧 retry 就撞 409
   `FeatureAlreadyExistsError`，没 catch 就把整个触点创建流程炸
   掉。正解：把 409 视为"幂等成功"处理，继续后续步骤；或者用
   `ensureFeature(id)` 的语义（存在就跳过）——customer-care
   `ensureGlobalFeatures` 就是这个思路。

4. **依赖 GB 做 runtime 保护**。"没关系，就算 admin snapshot 里
   有垃圾 variant，GB 不路由到它就没事"——这个思路害死人。GB
   SDK 和 admin snapshot 是两个独立的同步通道，时序不保证。
   operator 改 admin、改 GB，前端缓存、CDN、SDK evaluation cache
   各自独立过期。**永远假设 admin snapshot 自洽可用**：publish
   preflight 必须用 `detectDrift` 截停、snapshot 本身必须幂等可
   读、runtime 拿到未知 variantKey 必须能降级。GB 是 routing，
   不是 safety net。

## 源码锚点速查

| 能力 | engagement | customer-care |
|---|---|---|
| req wrapper + retry | `admin/src/lib/growthbook-api.ts:63-116` | `admin/src/lib/growthbook-api.ts:91-143` |
| FeatureAlreadyExistsError / GrowthBookHttpError | 同上 L20-37 | 同上 L23-40 |
| createFeature | L128-159 | L264-293 |
| getFeature + 30s cache | L212-232 | — |
| extractVariationValues | L239-254 | — |
| updateFeatureEnabled (archive) | L261-272 | — |
| createExperiment | L286-320 | L338-373 |
| findExperimentByTrackingKey | L328-356 | L406-434 |
| syncRecipeVariations / updateExperimentVariations | L367-395 | L383-397 |
| Proxy route + deepLink | `api/growthbook/features/[slug]/route.ts` | `lib/external-links.ts` |
| drift detection | `lib/gb-drift.ts` | `lib/__tests__/growthbook-sync-validator.test.ts` |
| Create-with-compensation | — | `api/scenes/route.ts:70-200` |
| Best-effort sync on recipe write | — | `api/recipes/route.ts:116-137` |
| ensureGlobalFeatures bootstrap | — | `lib/growthbook-bootstrap.ts` |

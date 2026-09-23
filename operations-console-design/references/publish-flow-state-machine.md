# Publish Flow State Machine

本文件给 `operations-console-design` 提供一份把"配置草稿 → staging 快照 → 生产发布 → 回滚 → 归档"整条链路抽象成状态机的方法论。目标不是规定表结构，而是回答：**有哪些状态、谁能触发转换、快照怎么保证一致、审计怎么还原现场、哪些反模式会让后台在某个深夜凌晨把操作员坑住**。

真实样例见 `/home/jchen/engagement/admin/src/app/api/publish-orders/` 下的路由实现（POST 装配、`[id]/approve-prod`、`[id]/cancel`、`[id]/rollback`）以及 `src/lib/snapshot-assembler.ts`、`src/lib/audit.ts`、`src/app/api/touchpoints/[slug]/route.ts`。每一节给出的方法论声明都能在这些源文件里找到对应锚点。

## 1. 适用范围

这套方法论适用于满足以下全部条件的 ops console：

- 后台有一类"受控发布动作"对象（`PublishOrder` / `ReleaseOrder` / `Deployment`），不是直接改一张表就生效。
- 运行时消费的不是数据库实时 join 结果，而是一份**快照**/ 规则包 / rules package —— 通常落在一个独立表（`rules_package`）或对象存储，而配置表只作为草稿域。
- 至少有两个发布环境（staging、prod），且 prod 的推送需要额外审批或等待窗口。
- 配置对象有历史引用语义 —— 一个 slug / id 一旦进过某个历史快照，就不能被物理删除，否则历史 replay / 审计 / rollback 会在引用缺失处崩塌。

如果后台只是 CRUD 一些配置然后马上生效（比如改数据库开关立即生效），不需要这套模型 —— 直接用 audit log + optimistic lock 就够了。以下内容是为"草稿态到线上态之间必须有明确闸门"的场景准备的。

**不适用**的典型场景：

- 外部系统主权的对象（Dittofeed `user journey`、GrowthBook experiment）—— 投放执行对象本来就由外部系统自身 publish，后台只做绑定展示，本地只要维护 binding 版本号，publish flow 应让给外部系统的审批链。
- 真正的实时开关（kill switch、feature flag 一键关）—— 这类场景的需求是"立即生效"而不是"受控发布"，强套 preflight + 审批会把事故响应时间拖长几倍。
- 只读聚合视图、无写路径的 admin —— 没有 mutation 也就没有 state 需要 machine 化。

所以先明确：这是**一个状态机方法论**，不是"所有配置变更都应走审批"的政策主张。

### 为什么要抽成状态机

把发布流程写成状态机而不是一串 boolean flag（`staged` / `approved` / `rolled_back`）有四个具体好处：

- **穷举可审视**：六个 status 值出现在 enum 里，任何查询 / Dashboard / 告警都能完整枚举；boolean flag 空间组合爆炸，`(staged, approved) = (true, false, unknown)` 时系统到底在哪不说清楚。
- **转换规则可文档化**：状态 × 动作 × 目标态是有限的表，可以画在 readme 里，review 时一眼看出"approve-prod 能从哪些状态触发"。boolean 版本只能翻源码。
- **终态可识别**：`prod_ok` / `cancelled` 是"不再往下走"的显式信号，告警侧 / UI 侧基于它显示不同图标和按钮。boolean 版本必须靠"staged==true && approved==true && !rolled_back"凑出终态，漏一个条件就错。
- **幂等点可推理**：handler 拿到一个 order 时第一件事是读 status，根据当前状态决定 "短路 / 推进 / 拒绝"。boolean 版本的 handler 要 && 一堆条件，容易遗漏某个组合。

方法论因此要求：**在任何时候都能用一句话回答 "这个 order 当前在哪个状态、合法的下一步是什么"**。做不到，状态机就 over-engineered；做得到，就是这套 schema 的最小必要体量。

## 2. 状态枚举与转换图

推荐六态模型。四态是"进行态"，两态是"终态"：

| 状态 | 类型 | 含义 | 是否可以再转换 |
|------|------|------|----------------|
| `pending_staging` | 进行态 | 已创建 order，还未装配出 staging 快照 | 是 |
| `staging_ok` | 进行态 | staging 快照已写入 `rules_package(env=staging)` | 是 |
| `pending_prod_approval` | 进行态 | 已发起 prod 审批，等待外部 approval 回调 | 是 |
| `prod_ok` | 终态 | prod 快照已写入 `rules_package(env=prod)`，对外生效 | 否 |
| `failed` | 非终态 | 装配 / 校验 / 审批任一环节失败；允许 cancel 后重发 | 是 |
| `cancelled` | 终态 | 人工撤销 —— `rules_package` 不清理，order 不再参与任何推进 | 否 |

转换图：

```text
         create()
            │
            ▼
    ┌───────────────┐    assembleSnapshot() blocking
    │pending_staging│──────────────────────────────► failed
    └───────┬───────┘
            │ assembleSnapshot() ok
            ▼
      ┌──────────┐   approveProd() stub kicks feishu
      │staging_ok│──────────────► pending_prod_approval
      └────┬─────┘                      │
           │                            │ approval callback
           │                            ▼
           │                      ┌──────────┐
           └─── approveProd() ───►│ prod_ok  │◄── rollback() (new order)
                  (直通)           └──────────┘

  任一进行态 ── cancel() ──► cancelled   （拒绝 cancel prod_ok）
  failed    ── cancel() ──► cancelled
```

几条具体约束（对应源码见 `approve-prod/route.ts` 与 `cancel/route.ts`）：

- `prod_ok` 与 `cancelled` 是终态。任何 handler 必须对它们短路成 `noop: true`（见 §7）或拒 409，不能静默做第二次 mutation。`approve-prod/route.ts` 对已是 `prod_ok` 的 order 显式返回 `{ order, noop: true }`；非 `staging_ok` / `pending_prod_approval` 的其他状态一律 409 `INVALID_STATE`。
- `prod_ok` 不允许 cancel —— 上过生产的东西不能被一个"撤销按钮"消除，要走 rollback 新开 order（§5）。`cancel/route.ts` 显式 `INVALID_STATE: "cannot cancel a prod_ok order — use rollback to retire it"`。
- `pending_prod_approval` 在当前实现里作为"已发起飞书审批但未 callback"的中间态保留；`approve-prod/route.ts` 把它和 `staging_ok` 一起当作合法的入口状态，为真正接入飞书 callback 时留扩展空间。
- `failed` **不是**终态 —— 它允许操作员 cancel 后重新开单，或者系统修复依赖后重新装配。`cancel/route.ts` 显式把 `failed` 列进 "safe to cancel" 名单。故障态不应永久污染 order 空间。

关于"是否需要把 `pending_staging` 单独列出"：当前实现其实直接从 `null → staging_ok` 一步到位（因为装配是纯函数、同事务内完成），但枚举里仍然保留 `pending_staging` 这个值，理由是**异步装配**是未来可能的演进：当 touchpoint 规模增大或者需要接入外部 CMS 回拉时，装配可能被推进一个 job queue。届时 order 的创建和快照写入会分成两步，`pending_staging` 作为"已接单、未装配"的状态被自然使用，不需要做一次带迁移的 schema 变更。把未来的状态位置**先占住**是状态机设计的一个便宜做法 —— 加状态要改 UI / 告警 / 查询，占住不花成本。

## 3. 发布 preflight 的作用

POST `/api/publish-orders` 不是单纯"插一行 order"，它承担四件事（全部在源码里清晰分层）：

1. 从当前 DB **装配快照**（`assembleSnapshot({ touchpoints })`，`src/lib/snapshot-assembler.ts`）—— 把所有 `active` touchpoint、三类 variant、condition 聚成一棵 canonical tree。装配函数是纯函数：给定相同输入返回相同输出，不副作用、不读外部时钟之外的东西。
2. **运行校验**，把 issues 分成 blocking 和 warning 两类。blocking code 集合写死在路由里：`NO_VISUAL_VARIANT` / `NO_CTA_VARIANT` / `NO_FATIGUE_VARIANT_ON_PROACTIVE` / `UNEXPECTED_FATIGUE_ON_FEATURE_GATE` / `EMPTY_CMS_SLUG` / `INVALID_ACTION_CONFIG`。blocking 集合**在路由层而非 assembler 层**写死，原因是 "哪些 issue 应当阻拦发布"是政策判断，政策未来可能随回归阈值调整；装配器只负责 classify，路由层负责 gate。
3. **blocking → 422 SNAPSHOT_VALIDATION_FAILED**，带上完整 issue 列表返回前端。UI 可以按 slug 聚合、点击跳到对应编辑页。非 blocking warning 放进 201 响应里随快照一起返回，让操作员知道"这次发了但下次要收拾"。
4. **校验通过后**才在单个 `prisma.$transaction` 里做三件事：`rulesPackage.create`、`publishOrder.create`、`logAudit`。三者任一失败整笔回滚，绝不出现"快照落表了但没有 order"的残片。

**关键设计原则：校验发生在 POST 入口，不发生在 commit 之后。** 对比"先写快照再审核再决定是否激活"的模式，本方法论坚持 preflight 把坏数据挡在快照表之外 —— 一旦进了 `rules_package`，就会被未来的 rollback / diff / replay 动作默认为曾经合法的锚点，污染快照表的成本是长期的。

另一个约束是**并发版本号分配**。路由用 `UNIQUE(env, version)` 索引 + P2002 重试循环处理"两个人同时点发布"的竞争：

```ts
for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
  const latest = await prisma.rulesPackage.findFirst({ where: { env: "staging" }, orderBy: { version: "desc" } });
  const stagingVersion = (latest?.version ?? 0) + 1;
  try { /* transaction */ break; }
  catch (err) {
    if (err.code === "P2002" && attempt < MAX_RETRIES) continue;
    throw err;
  }
}
```

失败的那个读到新的 `latest + 1` 再试，最多 5 次，耗尽返回 503 `PUBLISH_RETRY_EXHAUSTED`。不要用 `SELECT ... FOR UPDATE` + 应用层锁，那会把并发点变成排队点；数据库唯一索引 + 重试是更 honest 的方式 —— 任何分布式部署下自然扩展。audit 里专门记了 `attempt` 字段，排查"为什么这单 create 延迟了"时能直接看到并发碰撞深度。

preflight 的一个具体例子：当前实现在 `assembleSnapshot` 里用 `validateActionConfig` 校验 CTA 的 action config 是否与 actionType 构成 discriminated-union 合法组合（`OPEN_PAYWALL` 必须带 `paywallId: string`，`DEEP_LINK` 必须带 `/` 开头的 `routePath`）。这类跨字段约束是 DB CHECK 很难表达的（MySQL 直到最近版本才有功能有限的 CHECK 约束），放在 preflight 里反而更清晰：每条 CTA 单独校验，issue 列表按 slug + variantKey 定位问题点，前端可以"点这条错误 → 跳到对应 variant 编辑抽屉"。

把 preflight 想成"发布日当晚为了睡好觉愿意付出的静态分析成本"会比较容易对齐：能在入口静态捕获的错误，永远不要推迟到运行时动态触发。

## 4. 快照完整性：checksum 链

每个 `rules_package` 行必须带一个 `checksum` 字段 —— SHA-256 of canonical JSON（`snapshot-assembler.ts` 里的 `snapshotChecksum`）：

```ts
export async function snapshotChecksum(snap: Snapshot): Promise<string> {
  const canonical = JSON.stringify(snap);
  const buf = new TextEncoder().encode(canonical);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}
```

checksum 不是给后端做内容寻址用的（那是 S3 sidecar 的职责），它的目的是**让环境间的快照继承关系可验证**。三处使用场景在源码里一一可见：

- **staging 创建时**：POST 路由把 `checksum` 连同 `rulesJson` 一并写入 `rules_package(env=staging)`，并放进 audit `detail.checksum`。
- **staging → prod 晋升时**：`approve-prod/route.ts` 从 staging 行读出 `rulesJson` + `checksum`，**原样复制**到新的 `rules_package(env=prod)` 行（`rulesJson: stagingPkg.rulesJson, checksum: stagingPkg.checksum`）。不重新 hash、不重新装配、不重新校验。任何 hash 不一致就是 bug。
- **rollback 时**：`rollback/route.ts` 从源 `prod_ok` order 的 prod package 读出 `rulesJson` + `checksum`，原样写到新 prod package。新旧 prod package 内容相同、checksum 相同，但 version 递增；可以用 SQL "找出所有 checksum=X 的 prod package 行"快速枚举"这条内容出现过几次、分别是谁推上去的"。

这条 checksum 链消除的是同一类问题："staging 上审批的到底是不是真的跑到了 prod"、"rollback 真的回到了那次历史快照吗"、"这个运行时版本到底对应哪条配置"。不带 checksum 的发布系统在 pager 响起时必然要人肉 diff 两列 JSON，这是不能接受的。

一个进一步的实践：让运行时 backend 在启动 / 拉包时也把 checksum 上报 metric，dashboard 上就能直观看到"当前线上跑的 checksum"与"最新 prod package checksum"是否一致，排查漂移时不需要翻日志。

canonical JSON 的"canonical"当前实现用的是 `JSON.stringify` 默认序列化 —— 依赖 v8 保持插入顺序的行为。如果未来 snapshot 结构变得足够复杂、出现了嵌套 map、或者跨语言产生者，需要升级为显式按 key 排序的序列化器，否则 hash 会出现假阳性差异。这是升级到 v6 / v7 snapshot schema 时必须一起考虑的事情。

还有一个隐含约束：**`generatedAt` 字段被包含在 canonical JSON 里**（`assembleSnapshot` 返回的 `snapshot.generatedAt = new Date().toISOString()`）。这意味着同一组输入在两次调用之间 hash 不同 —— 这是刻意的，同一次发布只 hash 一次，环境间复制的是已 freeze 的 hash；如果需要"内容相等性检测"（比如 dry-run diff 是否跟上次 prod 一致），应该比较的是**去掉 `generatedAt` 后的子树 hash**，而不是 `checksum` 本身。这条区分在 dry-run 路由上线时务必要明确写进代码注释。

## 5. Rollback 作为新 order，不变更历史

Rollback 的正确形状是**创建一个新的 `publish_order`，指向一个新的 `rules_package(env=prod)` 行，内容等于目标老版本**。不要回改老 order 的 `prodVersion`，也不要在 order 表加 `rolled_back_at` 字段反向污染。

具体步骤（`rollback/route.ts` 一一对应）：

1. 校验 source order `status == 'prod_ok'` 且 `prodVersion != null`；否则 409。rollback 的锚点必须是"真的在生产上跑过的"版本，不能回滚到一个 staging / cancelled 状态。
2. 如果 source 已经是当前最新 prod version，拒绝 rollback（`INVALID_STATE: "order #N is already the latest prod version"`）—— 前端应当 re-publish 而不是"回滚到自己"；这条 guard 防止操作员在 panic 状态下连点 rollback。
3. 读取源 `rules_package(env=prod, version=source.prodVersion)`；如果找不到返回 500 `INCONSISTENT_STATE`（理论上不可能发生，因为 prod_ok 的充要条件是对应 package 行存在）。
4. 在一个事务里：新建 `rules_package(env=prod, version=latestProd+1, rulesJson=同源, checksum=同源)`；新建 `publish_order(status=prod_ok, stagingVersion=null, prodVersion=new, rulesJson=同源)`；写 `publish_order_rollback` audit 记录 `{ sourceOrderId, fromVersion, toVersion, checksum, rulesPackageId }`。

这样做的好处：

- **审计可读**：history 表查出的就是"发了 v3 → 回滚到 v2（作为 v4）"的线性序列，不用特殊图元渲染。
- **对账可做**：任何一个 prod_ok order 对应唯一一个 prod package row，1:1。
- **history 不可篡改**：源 order 的行永远长一个样子，没有"现在这行的含义跟昨天不同了"的歧义。
- **checksum 验身份**：source 和 rollback target 的 checksum 相等这件事本身就是 rollback 正确性的证据，下游任何基于 checksum 做幂等 / 缓存的消费者都会自然受益。
- **`stagingVersion=null`** 是显式设计：rollback 产生的 order 并不绑定任何 staging 快照（它来源于一个历史 prod 版本），保留这个字段的 null 状态让未来的统计"多少单 order 跳过了 staging"可直接 count。

反例在 §9 第二条。

一个常见提问是"rollback 后源 order 的 status 要不要改？"答案是**不改**。源 order 仍然是 `prod_ok`，它曾经真的在 prod 跑过，这个历史事实不变；新的 rollback order 也是 `prod_ok`，它现在在 prod 跑着；查询"当前线上是哪一版"的语义由 `rules_package(env=prod) ORDER BY version DESC LIMIT 1` 回答，而不是由 `publish_order.status` 回答。这个职责切分让 order 表变成**发布事件日志**而不是**运行时状态镜像**，两者语义不要混在一张表里。

## 6. Archive vs Hard Delete

配置对象（Touchpoint、Variant、Condition 等）永远**不能 hard delete**。理由在 `touchpoints/[slug]/route.ts` 的 DELETE 注释里写得很清楚：**"A touchpoint slug is baked into historical snapshots and may still be referenced by running SDKs; hard deletion would silently break replay / audit"**。

约定：

- DELETE 动词做成 archive —— 把 `status` 翻到 `archived`，**不动其他任何字段**、不动 variant 子表、不动历史 audit。
- 默认列表视图过滤掉 `archived`；在明确的 "archived" tab 下才展示；装配 snapshot 时也过滤掉 archived。
- **幂等**：对已经 archived 的行再次 DELETE，返回 `200 { archived: true, noop: true }`，**不写新的 audit**（否则同一个撤销按钮的重复点击会造出一串无意义 audit 行）。对应源码 `if (existing.status === "archived") { return NextResponse.json({ ..., archived: true, noop: true }); }`。
- 404 仍然是 404：查不到 slug 返回 NOT_FOUND，不要伪造"archive 成功"。
- 反向操作叫 **unarchive**（status 翻回 `active`），单独写一条 audit `touchpoint_unarchive`，**不合并到 update**。

反过来，`rules_package` 行也不应随着 order cancel 被清理 —— `cancel/route.ts` 的注释里明确写："Intentionally does NOT delete the `rules_package` rows. Those may be referenced by other orders or by the prod environment"。历史表的"只进不出"原则是配置系统可回滚性的底。

一个常被忽略的细节是**子资源随主资源一起 archive 的语义**。当前实现选择的做法是：touchpoint archive 时**不级联**去 archive 其 variant 子表，子表保持原状。理由是 variant 本身也可能被历史 snapshot 引用（通过 cmsSlug），级联翻 status 会让 `active variant whose parent is archived` 这个组合消失，反而丢掉排查信息。默认列表视图在父行 archived 时就不再展开子行，UI 层面不把操作员暴露给这些半悬挂的子对象即可；DB 层面则保留完整历史。

推广规则：**任何进过历史快照的对象，都只能 archive，不能 delete**。这包括 variant、condition、feature flag 绑定。表上建议统一加 `status` 枚举字段 + default `active`，archive 只翻 status，物理删除只作为"从未发布过的彻底草稿"才允许的特权操作 —— 且应在 handler 里强制校验"slug 从未出现在任何 `rules_package.rules_json` 里"。

## 7. Audit before/after 约定

每一次 write 路径都写 `audit_log`，且 `detail` 字段严格遵守以下形状（`src/lib/audit.ts` + 各 mutation 路由）：

- **普通更新类**（touchpoint PATCH / variant upsert）：`detail: { before: { ...selectedFields }, after: { ...patchData } }`。在 `touchpoints/[slug]/route.ts` PATCH 里就是 `{ before: { featureKey, condition, description, status }, after: data }`。只记被 patch 的字段 —— 把整行对象塞进 before/after 会让 audit 行迅速膨胀到几 KB 且 diff 起来反而不清晰。
- **状态机转换类**（cancel、approve-prod、rollback）：额外记 `previousStatus` / `fromVersion` / `toVersion` / `checksum` / `feishuInstanceId`，让排查人一条记录就能还原 "谁从哪个状态改到了哪个状态、指向哪个快照、审批走的哪个 instance"。`cancel/route.ts` 的 audit detail 就显式带 `previousStatus: order.status`。
- **创建类**：`detail: { orderId, packageId, checksum, touchpointCount, warnings, attempt }`。把并发重试次数也记下来，排查"为什么这个单子延迟了"时能看到；把非 blocking warning 也记下来，未来想追溯"为什么 v42 发上去时还有 N 个 warning"直接翻 audit。
- **幂等 noop 不写 audit**：cancel 一个已经 cancelled 的 order、archive 一个已经 archived 的 touchpoint、approve-prod 一个已经 prod_ok 的 order，handler 返回 `noop: true` 但**不写 audit 行**。这条是让"事情真的发生了"和"事情被尝试了"两件事在 audit 表上清晰分离。

audit 写入**必须和它描述的 mutation 在同一个 `prisma.$transaction` 里**。`logAudit(tx, ...)` 的 `tx` 参数就是为此留的 —— 如果 mutation 成功了而 audit 没写进去，后台就开始失明。参见 `src/lib/audit.ts` 的 `AuditClient = typeof prisma | Prisma.TransactionClient` 联合类型。

一个补充约定：**action 名字不走 enum，走 varchar + 字符串字面量联合**（`AuditAction = "touchpoint_create" | "touchpoint_update" | ...`）。这样加一个新变更类型不需要跑 migration；只在 TS 侧扩一个联合，历史 audit 字符串原样落库。代价是拼写错误不会被 DB 层拦截，只能靠 TS —— 但相比 enum migration 的心智成本，这笔交易划算。`AuditActionLike = AuditAction | (string & {})` 这个技巧（`src/lib/audit.ts`）允许 TS 默认在已知 action 上自动补全，同时对"新变更类型还没进联合"的调用点仍然接受字符串，不会在 call site 丢 type error。

一条额外经验：**audit 的 `touchpointSlug` 字段**是状态机之外的索引。它让"这个 slug 过去一个月被改过哪些字段"这种 Dashboard 查询可以直接 `WHERE touchpointSlug = ? ORDER BY created_at DESC` 拉出来，而不必把 detail JSON 里的字段提到顶层列。发布类 action（publish_order_*）不带 slug（它们是跨 touchpoint 的），这件事要在查询侧明确：按 slug 过滤会漏掉发布动作，需要按 `targetVersion` 或 `action LIKE 'publish_%'` 另走一条路径。

## 8. External Approval Workflow Stub 边界

生产审批往往要串飞书 / Slack / Jira approval。完整实现没落地前，保留一个 stub 层，但**不要对操作员撒谎**。

`approve-prod/route.ts` 当前的做法：

- `createApprovalInstance({ approvalCode: process.env.FEISHU_APPROVAL_CODE ?? "MOCK_CODE", ... })` 返回 `{ instanceId, mock }`。
- stub 模式下 `instanceId` 以 `MOCK-` 前缀开头，`mock: true`。
- 响应体把 `approvalMock: true` 透传给前端；UI 侧在审批链接旁打一个 "mock" badge，让操作员看得见"这次不是真飞书批的"。
- audit detail 把 `feishuInstanceId` + `feishuMock` 都记下；未来真正接入后，任何"发布日这条记录是不是真审批过"的问题都能翻出来。

**原则：未配 `FEISHU_APPROVAL_CODE` 就退化到 mock 这件事，不能被静默处理。** 操作员不应该在事后才发现某次发布其实没走过审批。把 mock 状态抬到三个位置冗余暴露：

1. HTTP 响应顶层字段（`approvalMock: true`）。
2. UI badge（列表 / 详情页都要有）。
3. audit detail（`feishuMock`）。

同样的原则推广到任何外部依赖（GrowthBook feature auto-create、CMS slug 校验、metric registry 上报）：**降级路径必须可见，降级结果必须可审计。** 后台给的每一条反馈都应该如实反映底层到底做了什么，哪怕"什么也没做"。

当真正接入审批回调时，合理的演进路径是：

- 新增 `approval-callback` 路由作为 webhook 接收方，校验签名 → 更新 order status 从 `pending_prod_approval` 跳到 `prod_ok` 或 `failed`。
- approve-prod 路由本身拆成两段：一段只负责 "create approval instance → 状态进入 `pending_prod_approval`"；实际写 prod rules_package 的那段移到 callback 里。
- audit 补一条 `publish_order_approval_callback`，记 `{ instanceId, outcome, comment }`。

这个演进在当前 stub 模式下**不需要立刻做**，但状态机 schema 已经把位置留好了 —— `pending_prod_approval` 这一态之所以从第一天就写进 enum，就是为了给未来的回调留空间；否则 status 升级本身会是一次带 schema migration 的硬切换。

另一个边界是**审批拒绝**：真实场景下飞书审批可能被拒绝。schema 上建议把"拒绝"映射到 `failed`，而不是再开一个新 status，理由是"拒绝"后的合法动作就是 cancel 或重新修 touchpoint 后发起新 order，这和装配失败的走向一致。把状态态数量保持在六个以内，认知负担才可控。

## 9. 反模式清单

把以下四条当作 code review 时的强制红线：

1. **硬删 touchpoint / variant / condition**。DELETE 动作是 archive，不是 hard delete。硬删会导致一个月前的历史快照里仍然引用的 slug 在 replay / diff / rollback 时"消失"，审计链就此断裂。典型症状：排查 "v37 为什么跟 v38 diff 不出来"，发现两边都在引用一个早已 404 的 slug，整套回溯失效。`touchpoints/[slug]/route.ts` DELETE 的正解：`update({ data: { status: "archived" } })` + `noop: true` 短路。

2. **Rollback 直接改源 order 的 `prodVersion`**。这会让 `publish_orders` 表变成可篡改历史 —— 同一行 order 今天指 v3、明天指 v2，任何下游基于 `createdAt + prodVersion` 做的对账都错；审计 timeline 里一个 order 的含义会"穿越"。正确做法是新建 order + 新建 rules_package（§5，`rollback/route.ts` 里一整个事务都在 `create`，从不 `update` 源行）。

3. **approve-prod / cancel / archive 不做 idempotent short-circuit**。用户网络抖动点了两下"批准上线"按钮，第二次请求落到一个已经 `prod_ok` 的 order 上 —— 如果 handler 不返回 `{ noop: true }`，要么抛 409 让用户以为出事，要么真跑一遍生成 v(n+1) 又多出一个冗余 prod package。所有幂等动作必须对重复调用安全，且"真的发生了 vs. 只是被重试了"要在响应字段 / audit 上分得清。参照 `approve-prod/route.ts` 对 `prod_ok` 的短路、`cancel/route.ts` 对 `cancelled` 的短路、touchpoint DELETE 对 `archived` 的短路，这三处是同一模式。

4. **跳过 preflight 校验让坏数据进 snapshot**。常见错误形式："校验逻辑太重，先发再说，后端会拒"。一旦坏数据进了 `rules_package` 表，rollback 到这个版本就会把坏数据再装填一次；diff 视图会把错误当作 baseline；未来的 schema 升级脚本要绕着它走。preflight blocking 不是柔性建议，是 422 + 拒绝入表。把"放行坏快照"当成一次需要专门文档化的决策，而不是"顺手绕过的小动作"。

补充几条二级红线（严重程度略低，但仍建议避免）：

- **把 checksum 当成可选字段**。checksum 是 NOT NULL，且在 staging / prod 之间必须字面相等。任何 "checksum 暂时不算"的开关都是埋雷。
- **在装配器里混入 IO**。`assembleSnapshot` 是纯函数；任何在装配过程里顺手 fetch 外部系统的冲动，都应被拦回到装配**前**的数据准备阶段。否则快照就不再是"这个时点的 DB 状态"，而是"这个时点的 DB 状态 + 外部系统当时的响应"，不可 reproducible。
- **audit 不走事务**。见 §7，`logAudit(tx)` 必须用同一事务；否则 mutation 成功 + audit 失败会让后台局部失忆，且这种 bug 通常在最需要追溯的事故日才会暴露。
- **blocking issue code 下沉到 assembler**。blocking 集合是政策，要留在路由层；assembler 只做 classify。把 blocking 列表硬编码进 assembler 会让"调一次阈值"变成"改一次核心纯函数"，回归风险放大。

## 10. 三句话收尾

1. **状态机要明示终态**。`prod_ok` 与 `cancelled` 不再转换，所有 handler 对终态短路成 `noop: true`。
2. **快照要带 checksum**。staging → prod 复制 checksum、rollback 复制 checksum；不重算、不重装配、不重校验。
3. **历史不可篡改**。配置 archive 不 delete，rollback 开新 order 不改旧 order，audit detail 同事务写入且带 before/after。

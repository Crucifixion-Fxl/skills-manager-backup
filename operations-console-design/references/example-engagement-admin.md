# Example: Engagement Admin（v1 → v2 复盘）

本文件只做一件事：说明抽象领域模型如何在 `engagement-admin` 的 v1 (2026-04-11) 与 v2 (2026-04-22) 里落地，并把 v1 的三个错误假设以及 v2 的三个决策讲清楚。

阅读顺序固定为两层：
- 先看抽象概念：`Domain / DecisionPoint / Strategy / *Binding / PublishOrder / AuditLog / ExecutableTarget`
- 再看 engagement-admin 里的具体术语：`Touchpoint / ExperienceVariant / FatigueVariant / variantKey / cmsSlug / PublishOrder`

## 1. 一句话定义

Engagement Admin 是**付费转化触点治理台**：运营在此完成触点（Touchpoint / marketing-service 里叫 Slot）的 CRUD、A/B 变体管理、snapshot 发布审批；发布产物以 S3 snapshot JSON 驱动 Go runtime，分流规则以 GrowthBook feature 承载。

Engagement Admin 不是通用模型本身，而是下面这组抽象概念的一种具体实现：

```text
Domain (Engagement)
  └── DecisionPoint (Touchpoint / slot)
        ├── Strategy (ExperienceVariant)
        ├── ExposurePolicy (FatigueVariant, PROACTIVE only)
        ├── ExperimentBinding (variantKey → GrowthBook feature)
        ├── ContentBinding (cmsSlug → Payload CMS)
        ├── EligibilityRule (GB force rule, 外部主权)
        ├── PublishOrder (staging → 飞书审批 → prod)
        └── AuditLog
```

注意：**没有** DeliveryBinding，因为 runtime 是 in-process Go 服务读 S3 snapshot，没有独立渠道系统。

## 2. v1 里的三个错误假设

v1 在 2026-04-11 上线，同月 22 日做架构评审时，三个设计假设被推翻：

| # | v1 假设 | 为何错 |
|---|---------|--------|
| A1 | **3-way 正交 A/B**：Visual × Fatigue × CTA，每维独立一个 GB feature（`engagement_<slug>_visual` / `_fatigue` / `_cta`） | 粒度过细。Visual 内容与 paywall-route（CTA）在设计上通常成对出现、很少独立迭代；纯 CTA 实验应在 paywall 定价工具里做，不在触点层 |
| A2 | **Eligibility 住在 `touchpoint.condition Json?`（JsonLogic）**：admin 负责编辑、snapshot 携带、runtime 在本地 eval | Eligibility 与 targeting 本质是同一个问题（按用户属性决定"谁能看到"），分两地表达会重复且易漂移。应该合并到一个系统里 |
| A3 | **Admin 与 GrowthBook 是平级系统**：admin 管 variant 内容（cmsSlug, actionConfig），GB 管 variant 选择，彼此完全解耦、无双向感知 | 完全解耦听起来干净，但运营看不到"admin 里写的 variantKey 在 GB 是否存在"，线上频繁出现"GB 路由到 treatment_b 但 admin 里没有这个变体"的空渲染事故 |

## 3. v2 的三个决策

2026-04-22 Review 之后确定：

### D1: Eligibility → GrowthBook force rules

撤掉 `touchpoint.condition` 列；snapshot v5 不再携带 condition；Go runtime 的 `TouchpointEvaluationService` 不再调 JsonLogic evaluator，改为直接用 GB SDK 对 `engagement_<slug>_experience` feature 求值，force rule 决定是否展示、以及路由到哪个 variantKey。Admin 侧把 Condition 编辑器降级为"只读 Eligibility 卡片"，从 GB 读并展示。

### D2: 3-way → 2-way A/B（Experience + Fatigue）

合并 `VisualVariant` + `CtaVariant` 为 `ExperienceVariant`，一个 variant 同时绑定 `cmsSlug` + `actionType` + `actionConfig`；每个 Touchpoint 的 GB feature 数从 3 个降到 2 个（PROACTIVE：`_experience` + `_fatigue`）或 1 个（FEATURE_GATE：`_experience`）。Wizard 从 4 步变 3 步，Configure tab 从 3 张表变 2 张。

### D3: Admin ↔ GrowthBook 漂移检测混合

**Admin owns content, GB owns rollout.** 不做双向同步，只做"admin 从 GB 读 + 发布时 drift 校验"：
- 触点创建时通过 GB API 创建 feature 骨架（skeleton）。
- 变体编辑时 admin 把 `variantKey` 字段做成 combobox：建议来自 GB 当前 variations，允许自由输入，写入了 GB 里没有的 key 会内联告警。
- 发布 preflight 调 `detectDrift(variants[], gbFeature)`，返回 `{ok, missingContent[], orphanVariants[], gbUnreachable}`；`missingContent` 或 `gbUnreachable` 都 **硬阻塞发布**（`409 DRIFT_DETECTED`），`orphanVariants` 只是警告。
- Admin 不写 GB 的 targeting / rollout，只写 feature 骨架与 variation 描述。

## 4. 抽象概念与 v2 实际概念对照

| 抽象概念 | Engagement Admin v2 落地 | 在 v2 里回答什么问题 |
|---------|--------------------------|---------------------|
| `Domain` | Engagement（付费转化触达） | 这套规则属于哪个业务域 |
| `DecisionPoint` | `Touchpoint`（slot） | 在哪个触达时点做一次决策 |
| `Strategy` | `ExperienceVariant` | 命中后用哪份内容 + 哪个 action |
| `ExperimentBinding` | `variantKey → GrowthBook feature (_experience / _fatigue)` | 这个方案如何进入实验分流 |
| `ContentBinding` | `ExperienceVariant.cmsSlug → Payload CMS` | 这个方案最终展示哪份内容 |
| `DeliveryBinding` | **不存在** | Runtime 是 in-process Go，不跨渠道 |
| `EligibilityRule` | **外部**：GrowthBook force rule | 谁看得见这个触点 |
| `ExposurePolicy` | `FatigueVariant.fatigueJson`（PROACTIVE only） | 同一用户多久能再看到 |
| `PublishOrder` | `publish_order` 表 + 状态机 | 这次变更如何冻结、审批、发布、回滚 |
| `AuditLog` | `audit_log` 表 | 谁改了什么、何时、为何 |
| `ExecutableTarget` | Snapshot JSON v5 + Go runtime 组装的 `TouchpointView` | App 最终消费的配置 |

## 5. v1 与 v2 的 Binding 形态差异

v1 把 eligibility 放在 admin 自有字段里、把 Visual/CTA 拆成两类 binding；v2 把 eligibility 外推到 GB、把 Experience 合并。两者 binding 面的差异：

| Binding | v1 落地 | v2 落地 |
|---------|---------|---------|
| ContentBinding | `VisualVariant.cmsSlug` + `CtaVariant.actionConfig`（一个 variant 只含 content 或 action，不同时持有）| `ExperienceVariant.cmsSlug` + `ExperienceVariant.actionConfig`（同一行同时持有）|
| ExperimentBinding | 3 个 GB feature，每维 1 个 | 1-2 个 GB feature（`_experience` 必有，`_fatigue` 仅 PROACTIVE）|
| EligibilityRule | admin-owned JsonLogic (`touchpoint.condition`) | GB force rule，admin 只读 |
| ExposurePolicy | `FatigueVariant.fatigueJson`（同 v2）| `FatigueVariant.fatigueJson`（未变）|

## 6. 发布闭环与状态机

PublishOrder 是一个独立 Aggregate，状态机固化在 `src/app/api/publish-orders/[id]/*/route.ts`：

```text
[*] --> pending_staging
       : POST /api/publish-orders （preflight 通过后）
pending_staging --> staging_ok
       : 组装 snapshot v5 + 校验 + S3 upload staging/vN.json
pending_staging --> failed
       : 校验失败 / S3 失败 / DRIFT_DETECTED
staging_ok --> pending_prod_approval
       : POST /approve-prod，创建飞书审批单
pending_prod_approval --> prod_ok
       : 飞书回调 approved，写 rules_package(env=prod, version=N+1)
pending_prod_approval --> failed
       : 飞书回调 rejected
非终态 --> cancelled
       : POST /cancel（pending_staging / staging_ok / pending_prod_approval / failed 都可取消）
prod_ok --> 新 publish_order(prod_ok)
       : POST /rollback —— 回滚不是状态回退，而是一条新 order 指向旧 snapshot
```

关键不变式：
- **Rollback 不修改源 order**：`/[id]/rollback/route.ts` 读取 `source.prodVersion` 对应的 `rules_package`，分配新 `prodVersion = latestProd + 1`，插入 **新** rules_package + **新** publish_order(`status=prod_ok`, `stagingVersion=null`)。`/releases` 历史永远只追加、不回退。
- **Cancel 拒绝 prod_ok**：已发布到 prod 的 order 只能 rollback，不能 cancel（`cancel/route.ts` 返回 409）。
- **approve-prod 幂等**：已在 `prod_ok` 的 order 重复调用返回 `{ noop: true }`；非 `staging_ok` / `pending_prod_approval` 状态返回 409 `INVALID_STATE`。
- **Snapshot v4 → v5 是 additive bump**：`snapshot-assembler.ts` 里 `SNAPSHOT_VERSION = 5`，v5 在原 `visuals[]` + `ctas[]` 之外新增 `experiences[]`，旧 backend 读 v5 仍能走 v4 字段过渡，直到后端切到 v5-only 才在未来 v6 删除旧字段。schema 迁移不再像 v1 那样"换表不升版"。

## 7. 踩过的坑（选 4 个）

### 7.1 "严格下拉"会强制 GB-first 流程，但团队一般先写内容

v1 想过把 `variantKey` 做成纯 dropdown（选项只能来自 GB variations 列表）。实际操作是：运营通常先在 admin 里写 cmsSlug + actionConfig，等内容稳定了再去 GB 配置 variation。强制 dropdown 会让"先写内容"的流程卡在"GB 里还没有这个 key"。

v2 的解法是 **combobox**：建议来自 GB，自由输入允许，内联告警提示"此 key 尚未在 GB 存在"。硬门是发布时 `detectDrift` preflight，不是 UI 的 dropdown 纪律。

### 7.2 把 JSON 塞进 GB variation value 会破坏 snapshot 原子性

D3 review 时讨论过"把完整 variant 内容（cmsSlug + actionConfig）作为 GB variation 的 value JSON"。拒绝原因：
- GB variation 编辑器是**单行文本**，嵌套 JSON 不可读；
- Zod discriminated union 校验 + CMS catalog 检查在 GB 里无处落脚；
- 发布 snapshot 原本是 DB 一次性打包、checksum 校验、S3 原子 upload；如果内容在 GB，发布变成"同时从 admin DB + GB API 两个源拼装"，任一抖动都会让 snapshot 不一致。

v2 定论：**GB value 只放 variantKey 短字符串**（例如 `summer_banner_v2`），所有内容留在 admin DB。跨界只传"名字"，不传 payload。

### 7.3 Admin 持有 Condition 与 GB targeting 是重复表达

v1 的 L1 Condition（JsonLogic）和 GB 的 L2 targeting 本质都在回答"哪些用户能看到"。两处各写一份，实际运营场景立刻出现漂移：admin 里的 condition 写"vip_tier >= 2"，GB 里的 targeting 写"entitlement in [gold, platinum]"，两者在边界用户上不等价。v2 直接把 eligibility 合到 GB force rule，admin 侧只读展示，消除这组同义词。

### 7.4 前端页面里残留的 dead button

v1 上线后一段时间，触点详情页仍保留"同步到 GrowthBook"按钮，但 D3 定了 admin 不做写穿透（skeleton only）后，这个按钮永远返回 501。应当在决策落地的同一发布里删掉 UI 入口，而不是留着"技术上还连着路由"的 dead button——否则运营会反复点击 + 提工单。

## 8. 不做项（explicit non-goals）

下面这几条在 v2 里是明确的**不做**，不是"以后再说"：

1. **Paywall 内部 A/B 不迁入 engagement-admin**。定价文案、paywall 页内跳转等仍归 paywall 服务自己的 CMS + 实验工具；engagement-admin 只管"触点 CTA 指向哪个 paywallId"。
2. **Runtime 不实时调 GrowthBook 做内容查询**。Go backend 消费 S3 snapshot v5 的 `experiences[]`，GB SDK 只用来做 variant 选择（返回 variantKey），内容 lookup 永远走 snapshot。snapshot 是运行时 SSOT。
3. **Admin 不写穿透到 GB 的规则编辑面**。Admin 只创建 feature 骨架 + 读 variations/rules 做漂移检测；targeting、rollout percentage、prerequisites、variation weights 全部由 PM 在 GB UI 操作。Admin 侧仅提供"在 GrowthBook 中打开"深链。
4. **历史 v4 snapshot 不做回填迁移**。Phase 2 只做"前向 emit v5"，runtime 同时接受 v4/v5；老的 rules_package 行不会被 rewrite。

## 9. 这个例子真正可复用的部分

engagement-admin 的 v1 → v2 可复用的不是 `Touchpoint / ExperienceVariant / fatigueJson` 这几个词本身，而是下面这组决策模式：

- **Eligibility 不要在 admin 自己再做一层**——如果已经有 rollout 系统（GB 等），eligibility 应合进去。
- **A/B 维度划分要跟随产品变更的耦合度**，不要一开始就按"技术上正交"最大化拆分。Visual 与 CTA 看起来正交，但产品侧一起改，就不应拆成两个 feature。
- **Admin ↔ 外部系统集成优先用"读 + 漂移检测"，不用"写穿透同步"**。前者是一个 pure function + preflight gate，后者要处理分布式事务、字段所有权、覆盖冲突。
- **PublishOrder 状态机加 cancel + rollback 是必要的 MVP**，不是 nice-to-have。Rollback 必须是"新 order 指向旧 snapshot"而不是"把状态回退到历史行"，否则 `/releases` 历史就不可追溯。
- **Schema 版本号是发布契约的一部分**，变更表结构而不升 snapshot 版本号是 v1 最大的教训之一。v4→v5 的 additive bump 保证了后端可以分阶段切换。

## 10. 哪些词不要直接拿去泛化

- `Touchpoint` 只是 Engagement 域里 `DecisionPoint` 的落地名——在 marketing-service 里叫 `Slot`，在 SmartPopup 里叫 `Scene`。
- `ExperienceVariant` 是 engagement-admin v2 里 `Strategy` 的落地名；v1 里被拆成 `VisualVariant` + `CtaVariant` 两个名字，那是设计错误的遗留。
- `variantKey` 是跨 admin ↔ GB 边界的唯一标识，不是通用"变体 id"——它必须与 GB variation 的 value 字节一致。
- `fatigueJson` 是 PROACTIVE 触点的 ExposurePolicy 落地字段，不要作为通用"疲劳度"接口挪用。

因此，写方案时应优先写抽象概念；只有在落到 engagement-admin 这个例子时，才写 `Touchpoint / ExperienceVariant / variantKey / fatigueJson`。

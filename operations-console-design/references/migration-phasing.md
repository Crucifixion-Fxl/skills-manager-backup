# 迁移分期方法论

## 1. 适用场景

Admin 已在生产至少跑过一个周期，你发现领域模型本身错了——表分得不对、某个抽象应该合并、eligibility 应该换家。线上还在跑发布过的快照、后端还在消费它，运营也已经对旧 UI 形成肌肉记忆。

错误答案是「一次性重写」。正确答案是一条分期迁移路径，**每一期都能独立上线，并且前一期 N 之前的老系统在第 N 期完成前都能继续工作**。

## 2. 标准五阶段模板

### Phase 0 — 决策文档（零代码）

先写一份 `docs/plans/YYYY-MM-DD-<domain>-architecture-v2.md`，里面至少回答：

- **Context**：v1 的假设是什么，现在哪里错了。
- **Decisions** (D1, D2, D3…)：每条一句话，附 rationale。
- **Target state**：v2 的领域模型、schema、外部集成、admin UI 要长成什么样。
- **Phase breakdown**：每个 phase 交付什么、失败时回滚到什么状态、进入下一 phase 的 gate 是什么。
- **Open questions**：会影响计划的问题。永远不要拿猜测去写代码，先答完再进入那个 phase。

**为什么 Phase 0 单独成一期**：你要把计划本身拎出来让 review 发声；reviewer 在纸上发现「你没处理这种 case」比在一周代码之后发现便宜得多。再者，这份文档是团队的共识锚点，每一期的 commit 都会引用它。

### Phase 1 — 只加读路径（不动 schema）

写 v2 计划需要的新纯函数、新只读 API proxy、新客户端 lib。它们和现有代码并排存在，由 unit test 驱动，但 admin UI 的任何页面还不调用它们。

例子：一个 `detectDrift()` 纯函数 + 单测 + 一个 `GET /api/growthbook/features/:slug` proxy 路由。admin UI 没有一条路径依赖它；回滚就是 `rm -rf` 这批新文件。

**进入 Phase 2 的 gate**：新代码单测覆盖率 ≥ 95%，lint / tsc 干净，生产行为未变。

### Phase 2 — 并行 schema（双写）

加新表或新列。写一个 backfill 脚本把老数据搬过来，**同时保留遗留表**。写路径改成双写（dual-write），让新增的行从现在开始两边都有。

幂等是生死线：这个 backfill 脚本必须可以在生产上安全多跑几次。配一个行数健全性检查收尾——`actual >= expected`（允许新写入带来的盈余，但不允许赤字）。

例子（engagement admin Phase 2）：

```
Before: visual_variant + cta_variant（两张表，用 variantKey 配对）
After:  + experience_variant（列合并后的新表），从 join 结果 backfill
Write:  POST /touchpoints 同时写 Visual + CTA + Experience（dual-write）
Read:   detail response 返回全部四个数组（legacy 三件套 + experience）
```

**进入 Phase 3 的 gate**：backfill 在生产数据上验证过，两条读路径对所有现存行产生一致结果。

### Phase 3 — 切换消费方

把原来读旧 shape 的地方翻到读新 shape。admin UI 里就是 Drawer / Form 组件换一套；后端 loader 就是一条新 code path，能吃 v5 snapshot 同时还容忍 v4 输入。

**这一阶段顺便把 snapshot / event schema 的版本升上去**：payload version 从 v4 升到 v5，但不丢 v4 shape。新消费方两个版本都能读；老消费方照旧吃 v4。

回滚 = 把消费方翻回老路径。遗留表还原封不动放着。

**进入 Phase 4 的 gate**：新消费方在生产上跑满 ≥ 7 天，期间 fallback 到旧路径的次数为零。

### Phase 4 — 写透 / 跨系统同步（如果需要）

到这一步才考虑跨系统 write-through（保存时调外部 API）。如果 Phase 1-3 不需要，这一期整个跳过。

保持窄：admin 不能顺势变成外部系统的 UI。合理范围：touchpoint 创建时，admin 调 GB API 建一个 feature skeleton。**不能**：admin 通过自己的 UI 编辑 GB 的 rollout 规则。

**进入 Phase 5 的 gate**：这条写透路径藏在一个 feature flag 后面，随时可以 kill-switch 回「admin 不写外部系统」，并且已在 staging 演练过一次。

### Phase 5 — 砍掉遗留 shape

删旧表、旧列、旧 code path、旧 UI 组件。这是**不可逆**的一期。只有在满足以下全部条件后才上：

- Canary 窗口内 fallback 调用数为零。
- 新消费方已经稳定运行足够周期。
- 所有外部消费方（其它服务、分析管道等）都已切换完成。

删表的 migration 单独写一条 commit，让回滚在最初的几个小时内仍然可能。

## 3. 每一期的回滚语义

| 阶段 | 如何回滚 |
|------|---------|
| 0 | 回退计划文档。没有代码要撤。 |
| 1 | `rm` 新加的文件。没有人在消费它们。 |
| 2 | 保留遗留表，禁用新表的写入。backfill 留在库里无害。 |
| 3 | 切消费方回旧路径；payload version 退回 v4。遗留表数据仍在。 |
| 4 | 把写透 feature flag 关掉。外部状态可能只写了一半，但至少不比 Phase 4 之前更糟。 |
| 5 | **不可回滚** —— 正是因为如此，它前面的 gate 才那么重要。 |

## 4. 常见失败模式

- **跳过 Phase 0**：一边实现一边抛方案出来 review。reviewer 在一周代码之后才发现计划问题，一旦需要改路线代码就得推翻重来。永远先写计划文档。
- **把 Phase 2 和 Phase 3 合并**：「反正我改着 schema 呢，顺手把 UI 也翻了吧」。结果一旦要回滚，就要同时回退 schema 和 UI 两套改动。把两期拆成独立 commit 甚至独立 PR。
- **backfill 脚本不幂等**：运维跑两次（或者 staging 一次、prod 一次），数据翻倍，花几小时清理。backfill 必须 upsert-on-unique-key，永远不用裸 `INSERT`。
- **过早砍掉遗留 schema**：在消费方还没稳之前就把逃生通道炸了。Phase 5 的 gate 条件是有原因的——即使项目进度压力再大也要守住。

## 5. Snapshot / event payload 的版本号

如果 admin 产出某种 payload（snapshot JSON、event、config blob）给后端或运行时消费，一定要版本化，而且过渡期内要**累加式升级**：

- v4 是 `{visuals: [], ctas: []}`，pairing 靠约定。
- v5 加上 `{experiences: [{cmsSlug, actionType, actionConfig}]}`，**同时**保留 v4 的字段。payload version 升到 5，但 v4 消费方依旧能找到自己需要的字段。
- v6（未来）才删 v4 数组。只在所有消费方都确认接好 v5 之后才做。

这样就没有 flag day。消费方按自己的节奏切换。

## 6. 没有 migration 工具时怎么办

很多 admin 用 `prisma db push` 这类「no-migrations」流派。开发很顺，但生产 schema 演进风险大——丢历史、丢反向操作。

Phase 2 的 schema 变更，即使工具不要求，也要手写一条 SQL migration 文件并 commit。它能给你：

- 人类可读的变更记录，知道什么时候改了什么。
- 一份回滚脚本（哪怕只能手动跑）。
- Backfill 脚本的锚点（「此脚本需在 2026-04-22-v2 migration 之后执行」）。

## 7. 计划文档的 checklist

开始写 Phase 1 代码之前，计划文档必须回答：

- [ ] target state 的领域模型是什么（图或 bullet list）？
- [ ] target state 的 schema（表 + 列 + 索引）是什么？
- [ ] 每一期交付什么？
- [ ] 每一期回滚做什么？
- [ ] 每一期之间的 gate 是什么（可度量，不是「感觉差不多」）？
- [ ] 有哪些 open questions 专门阻塞某个 phase——标注清楚阻塞点是那一期，而不是笼统说「方案还有疑点」。

任何一条没答清，计划文档就还没准备好。把含糊之处主动丢给 reviewer 再讨论一轮，不要带着不确定性进代码阶段。

## 8. 相关参考

- `references/domain-model.md` —— 迁移目的地的抽象模型
- `references/deployment-architecture.md` —— 迁移过渡期要尊重的 DC / runtime 边界

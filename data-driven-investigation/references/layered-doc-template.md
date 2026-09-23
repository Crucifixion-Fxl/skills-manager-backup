# Layered Doc Template — 调查文档分层结构

> SKILL.md 主体提及的文档分层结构详细模板。**搭配 [ssot-and-glossary-discipline.md](ssot-and-glossary-discipline.md) 使用**（后者给纪律，本文给模板）。

## 目录结构

```
docs/scenarios/<feature>/<topic>/
├── README.md              薄索引 · 指向 04 / glossary，不陈述结论
├── 01-background.md       稳定：业务问题 + 范围 + 目标
├── 02-data-sources.md     稳定：原始字段 + 数据缺口（派生字段进 glossary）
├── 03-analysis/           证据账本 · append-only · 每 round 一个文件冻结
│   ├── README.md          链路索引 + 每轮摘要
│   ├── 01-round1-*.md
│   ├── 02-round2-*.md
│   └── 0N-roundN-*.md
├── 04-findings.md         ⭐ LIVING SSOT · 现行结论（带 cross-ref 指向 03）
├── 05-algorithm.md        LIVING · 算法设计（引用 04 + glossary，不重复）
├── 06-deployment.md       LIVING · 部署 / 灰度（引用 05，不重复）
├── glossary.md            ⭐ SSOT · 所有变量 / 参数 / 分段 / 业务术语定义
└── ARCHIVE.md             撤回 / 修订结论映射（原 X → 修订 Y → round N）
```

### 文件角色对照

| 角色 | 文件 | 可变性 | 引用规则 |
|---|---|---|---|
| **SSOT 结论** | `04-findings.md` | LIVING（每轮可修订）| 所有其他文档**只引用不复制** |
| **SSOT 定义** | `glossary.md` | LIVING（新变量 / 定义漂移时补）| 其他文档的术语首次出现必须 cross-link |
| 证据账本 | `03-analysis/0N-*.md` | **冻结**（append-only，旧 round 不改）| `04-findings` 的 cross-ref 目标 |
| 稳定元信息 | `01`, `02` | 稳定（很少变）| 派生字段定义放 glossary，不放 02 |
| LIVING 设计 | `05`, `06` | LIVING | 只引用 04 / glossary，不重述规则 |
| 历史追溯 | `ARCHIVE.md` | append-only | 每次从 04 撤下结论时记录 |
| 薄索引 | `README.md` | 稀少变 | 不陈述结论，只给文件结构 + 跳转 |

## README.md 模板（薄索引 · 不陈述结论）

```markdown
# <场景名称>

> **入口索引**。当前结论全部在 [04-findings.md](04-findings.md)（LIVING SSOT），本文档不陈述结论。

- GitLab issue: <project>#<iid>
- Dev 分支: `<branch>`
- Dashboard: [链接]

## 📑 文档结构

| 类型 | 文件 | 说明 |
|---|---|---|
| 稳定 | [01-background.md](01-background.md) | 业务问题 / 范围 / 目标 |
| 稳定 | [02-data-sources.md](02-data-sources.md) | 原始字段 / 数据缺口 |
| **SSOT** | [glossary.md](glossary.md) | 所有变量 / 参数 / 业务术语定义 |
| 证据账本 | [03-analysis/](03-analysis/) | 每轮 EDA 证据，按 round 冻结 |
| **LIVING SSOT** | [04-findings.md](04-findings.md) | 现行结论 |
| LIVING | [05-algorithm.md](05-algorithm.md) | 算法设计 |
| LIVING | [06-deployment.md](06-deployment.md) | 部署 / 灰度 |
| 历史 | [ARCHIVE.md](ARCHIVE.md) | 撤回 / 修订结论追溯 |

## 🎯 SSOT 原则

1. **每条结论只在 04-findings 说一次**，其他文档只引用不复述
2. **03-analysis/0N-*.md 是证据账本**，append-only，旧 round 不改
3. **撤回结论进 [ARCHIVE.md](ARCHIVE.md)**，带 "原 → 修订 → round" 映射
4. **变量定义用 [glossary.md](glossary.md)**，看到陌生变量先查这里
5. **跨文档引用用相对链接 + anchor**，不复制内容

## 📂 相关资产

- **GitLab issue**: [链接]
- **dbt MR**: [链接]
- **Dashboard**: [链接]
```

**不要在 README 写 TL;DR 结论** —— 会和 04-findings 不一致。用户要"最新结论"直接点 04 的链接。

## 01-background.md 模板

```markdown
# 01 · 背景与范围

## 1.1 业务问题

<一段业务 context，2-5 句话>

**上游讨论**：<链接或引用>

## 1.2 业务目标

| 层次 | 目的 | 阶段 |
|---|---|---|
| A | ... | v0.1 |
| B | ... | v0.1 |
| C | ... | v1.0 |

## 1.3 核心决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 主信号源 | <X> | <why> |
| 目标人群 | <Y> | <why> |
| ... | ... | ... |

## 1.4 假设验证状态（随 Round 更新）

| 假设 | 状态 | 依据 |
|---|---|---|
| <H1> | ✅ / ❌ / ⏸ | [03-analysis/0N-xxx.md#q...] |
```

## 02-data-sources.md 模板

```markdown
# 02 · 数据源

> 本章只列**原始字段** + **数据缺口**。派生字段（rolling avg / ratio / cohort suspect 等）+ 阈值参数都在 [glossary.md](glossary.md)。

## 2.1 主信号源

### <表名 1>
<一段描述：链路、上报时机、字段清单（核心字段表）、填充率情况>
<字段如需派生，派生定义 → glossary §B>

### <表名 2>
...

## 2.2 辅助 / 非主信号 · 2.3 维表 · 2.4 已验证不可用于本场景的表 · 2.5 数据缺口 · 2.6 处理流水线

<同原版>
```

## glossary.md 模板 ⭐

```markdown
# 📖 Glossary · 变量 / 参数 / 术语 SSOT

> [← README](README.md) · [04-findings](04-findings.md) · [02-data-sources](02-data-sources.md)

本文档是**所有字段、派生量、参数阈值、业务术语**的**唯一定义源**。其他文档（04/05/06）**只引用**。
看到陌生变量时先 Ctrl+F 本文档。

## §A 原始字段（由数仓定义）
<锚点指向 02 或 DataHub，不重述>

## §B 派生字段（dbt / 计算层）
> 每条必须写**具体计算式**（SQL 表达 / 公式 / filter 链），不是自然语言解释

| 派生字段 | 定义 | 来源 |
|---|---|---|
| `avg_solar_ratio` | `AVG(solar_ratio) OVER 7d window`, where `solar_ratio = solar_count / events` | dws 层 |
| `battery_min_7d` | `MIN(battery_min) OVER 7d window` | dws 层 |

## §C 关键业务术语（含口径）
> 同名不同口径的，**必须 rename 避免歧义**（例如 `cohort_suspect` / `ads_suspect` / `popup_suspect`）

| 术语 | 精确定义（filter 链完整）| 规模 | 用于 |
|---|---|---:|---|
| `cohort_suspect` | 在 solar_required cohort 内 AND events≥10 AND avg_solar_ratio<0.2 | ~100k | ground truth / EDA |
| `ads_suspect` | `ads_table` 全量（无额外 filter）| 3,646 | ads 层快照 |

## §D 业务分类 / 分段
> 每条写**精确边界**（`<` vs `<=`）

| band | 条件 | 分布 |
|---|---|---:|
| `critical` | `battery_min_7d < 5`（严格小于 5）| X% |
| `high` | `5 ≤ battery_min_7d < 20` | X% |

## §E 阈值参数
| 参数 | 默认 | 含义 | 何时调 |
|---|---:|---|---|
| `cohort_mean_min` | 0.50 | 该 model 的均值 ratio 下限 | <0.5 则非 solar required；>=0.5 保留 |

## §F 写法约定
> 同一字段的不同简写对照表
| 表述 | 含义 |
|---|---|
| `battery_min` / `bm` / `bm_min_7d` | 同 §B.N |
```

## ARCHIVE.md 模板

```markdown
# 📜 ARCHIVE · 已撤回 / 修订的结论

> [← 04-findings.md](04-findings.md)

本文档保留历史上被推翻的结论，**只为追溯**。不要引用为当前依据。
每条记录：**原结论 → 修订后 → 证据 round**。

## R2 初版结论的修订
| # | 原结论 (R2) | 修订 | 修订 round |
|---|---|---|---|
| R2-1 | "X" | ⏸ 暂无法验证 | R3 Q20 |
| R2-2 | "Y" | ❌ 反例证据 | R4 Q26 |

## R3 / R4 / ... 逐轮列表

...

## 方法论 meta-learning（已沉淀到 skill）
| # | 教训 | 对应 skill 修订 |
|---|---|---|
| M-1 | ⏸ 不等于 ❌ | hypothesis-discipline |
```

## 03-analysis/README.md 模板

```markdown
# 03 · EDA 分析链路

## 🔬 链路图

```text
[01 baseline]    → (找到 1,549 可疑)
     │ (下一轮问：信号对吗？)
     ▼
[02 signals]     → (信号相关性 0.34，需融合)
     │ (下一轮问：有什么 pattern？)
     ▼
[03 patterns]    → ...
     │
     ▼
[0N deep-dive]   → ...
```

## 📂 章节索引

| 文件 | 覆盖问题 | 核心结论 |
|---|---|---|
| [01-xxx.md](01-xxx.md) | Round 1 | <一句话> |
| [02-xxx.md](02-xxx.md) | Q1-Q3 | <一句话> |
| ... | | |

## 🎯 EDA 对下一阶段的硬性输入

（完整结论见 [../04-findings.md](../04-findings.md)）

1. <结论 1>
2. <结论 2>
...
```

## 03-analysis/0N-*.md 模板（每轮一个）

```markdown
# 03.0<N> · Round <N>: <name>

> [← 上一节](<prev>.md) · [下一节 →](<next>.md)

## 本轮假设
**原假设**：<一句话>
**可证伪条件**：<X>

## 本轮方法选型
**选择**: <方法>
**理由**: <1-2 句>
**备选**: <B 当条件 X 时切>
**已知局限**: <≥1>

## 各问题结果

### Q<N.1> <问题>
<数据表 / 小结 / 2-3 findings / 启示>

### Q<N.2> ...

## 本轮综合结论

| 假设 / 结论 | 状态 | 依据 |
|---|---|---|
| <H> | ✅/❌/⏸ | Q<N.M> |

## 本轮对下一阶段的启示

1. <gap 1>
2. <gap 2>
3. ...
```

## 04-findings.md 模板

```markdown
# 04 · 综合结论与假设修订

## 4.1 强信号（已验证）

| # | 发现 | 来源 | 影响 |
|---|---|---|---|
| 1 | <findings> | [Q<N.M>](...) | <impact> |

## 4.2 假设修订表

| 原假设 | 修订后 | 状态 | 依据 |
|---|---|---|---|
| <H1> | <v2> | ✅ | ... |
| <H2> | <unchanged> | ⏸ | ... |
| <H3> | <refuted> | ❌ | ... |

## 4.3 弱 / 不可用信号

<列表，每条含原因>

## 4.4 数据缺口（⭐ 重要）

| # | 缺口 | 影响 | 应对（P0/P1/P2）|
|---|---|---|---|
| ... | ... | ... | ... |

## 4.5 对下一阶段硬性输入

<列表，会驱动 05-algorithm.md 的设计>

## 4.6 决策（基于上述结论）

| 决策 | 选择 |
|---|---|
| 目标人群 | <X> |
| 严重度阈值 | <Y> |
| 分型策略 | <Z> |
```

## 05-algorithm.md 模板（可选）

```markdown
# 05 · 算法设计

## 5.1 dbt 分层

<文字 / 表描述 dws / ads 层>

## 5.2 SQL 骨架

```sql
-- <model name>
{{ config(...) }}

WITH step1 AS ( ... ),
     step2 AS ( ... )
SELECT ...
```

## 5.3 关键起始参数

| 参数 | 起始值 | 来源 | 敏感度 |
|---|---|---|---|
| ... | ... | ... | ... |

## 5.4 v0.1 → v1.0 演进

| 维度 | v0.1 | v1.0 |
|---|---|---|
| ... | ... | ... |

## 5.5 局限

| 局限 | 影响 | 缓解 |
|---|---|---|
| ... | ... | ... |
```

## 06-deployment.md 模板（可选）

```markdown
# 06 · 部署计划

## 6.1 工作流

| # | 工作流 | 类型 | 责任方 |
|---|---|---|---|
| ... | ... | ... | ... |

## 6.2 部署顺序

<Stage A / B / C ...>

## 6.3 验收标准 DoD

- [ ] <item>
- [ ] ...

## 6.4 回滚预案

| 阶段 | 回滚动作 |
|---|---|
| ... | ... |

## 6.5 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| ... | ... | ... |

## 6.6 外部依赖

- <团队 / 人员 / 资源>

## 6.7 时间线

| 阶段 | 预计 | 阻塞 |
|---|---|---|
| ... | ... | ... |
```

## 核心纪律

- **README 是单一入口**：5 分钟能看懂"调查什么、现在哪、下一步做什么"
- **03-analysis 层层递进**：每个 0N-*.md 都接上 N-1 的 gap，不能散文化
- **04-findings 是"黑箱脱壳"**：不看 03 详细步骤也能单独读懂的综合结论
- **三态贯穿**：01 的假设验证表、04 的修订表、README TL;DR 都用同一套 ✅/❌/⏸

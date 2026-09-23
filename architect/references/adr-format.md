# ADR Markdown 格式 — 公司统一模板（SSOT）

ADR (Architecture Decision Record) = 决策的**推理链**，不是面向人的架构展示页。`/architect` 新增 ADR 时一律写成 Markdown；每条 ADR 一个 `.md` 文件，同目录 `README.md` 维护索引。ADR 主要供 Agent、review gate、Backstage ADR 插件和治理脚本读取，结构固定且视觉复杂度低，不需要 HTML 页面 chrome。

本文件是公司 ADR authoring 模板 SSOT：`architect` 负责设计阶段写 ADR，`service-catalog-onboarding` 负责把 ADR 目录接到 catalog / Backstage。面向人的整体架构、承重图和复杂流程仍使用 HTML-first 文档，并链接这里的 Markdown ADR。

## 文件命名

`NNNN-<kebab-slug>.md`：

- `NNNN` = 4 位零填充序号，**所属 ADR 目录内单调递增**。
- `<kebab-slug>` = 一句话决策的 kebab-case，如 `0001-use-shard-raft-groups-and-etcd-control-plane.md`。
- 一文件只记录一条决策；不要创建 `adrs.md`、`architecture-decisions.md` 等 aggregator。
- `README.md` 只做索引，不承载任何 ADR 正文。

## 存放位置（option C：per-Component + vertical + repo-wide）

| ADR 影响范围 | 路径 | 例子 |
|---|---|---|
| **per-Component**（决策只影响单个 Component 的实现） | `docs/architecture/<system>/<component>/adrs/` | `docs/architecture/engagement-backend/engagement-service/adrs/0001-use-growthbook-as-ssot.md` |
| **vertical 内部**（决策影响一个 cross-end vertical） | `docs/architecture/verticals/<vertical>/adrs/` | `docs/architecture/verticals/sync/adrs/0001-use-device-sync-phases.md` |
| **repo-wide**（决策跨 Component / 跨 vertical，影响整个 monorepo / leaf Domain） | `docs/architecture/adrs/` | `docs/architecture/adrs/0001-use-monorepo-layout.md` |

对应 `catalog-info.yaml` 注解（Component 上）：

```yaml
metadata:
  annotations:
    backstage.io/adr-location: docs/architecture/<system>/<component>/adrs
```

repo-wide ADR 由仓根的“主”Component 注解指 `docs/architecture/adrs`，或由门户统一另行索引。老仓已有 `docs/adr/` / `docs/adrs/` 可作兼容路径，但新 repo-wide ADR 不再创建到旧路径。

## Front matter 契约

每条新 ADR 的 YAML front matter 必须包含以下五个字段，字段名和类型固定：

| 字段 | 类型 | 规则 |
|---|---|---|
| `status` | enum | `Proposed` / `Accepted` / `Rejected` / `Deprecated` / `Superseded` / `Pending` |
| `date` | string | ISO 8601 日期，写成带引号的 `"YYYY-MM-DD"`，避免 YAML parser 隐式转型 |
| `deciders` | string array | GitLab username、团队或明确角色；未知时用 `[]`，不得编造 |
| `supersedes` | string array | 被本决策取代的 ADR ID；无则 `[]` |
| `superseded-by` | string array | 取代本决策的 ADR ID；无则 `[]` |

ADR ID 默认是同目录文件名去掉扩展名，例如 `0001-use-old-routing`。引用其它 ADR 目录时使用从当前 ADR 出发的相对路径并去掉扩展名。链接正文仍使用带扩展名的可点击相对链接。

机器检查至少验证：五字段存在、类型正确、status 合法、日期格式正确；`status: Superseded` 时 `superseded-by` 必须非空；其它状态不得同时存在相互矛盾的生命周期字段。

## 正文章节契约

每条新 ADR 使用稳定的英文 H2 标题，便于 Agent 和脚本做结构化读取：

1. `## Context and Problem Statement`
2. `## Considered Options`
3. `## Trade-off Analysis`
4. `## Decision Outcome`
5. `## Consequences`

要求：

- `Considered Options` 至少两个候选；不列选项就不是决策记录。
- `Trade-off Analysis` 同时写收益、代价、风险和适用条件，不能只证明已选方案正确。
- `Decision Outcome` 明确采用哪一项及理由。`Pending` ADR 改为列出完成决策前必须回答的问题。
- `Consequences` 分清正面后果、负面后果和 follow-up。
- 可选增加 `## Rejected Alternatives` 与 `## Links`，但不能替代五个固定章节。
- ADR 不复制架构图或大段架构正文；承重图留在 HTML 架构页，ADR 用相对链接引用。

这里定义的是 `/architect` 的**新文件 authoring 模板**。独立 `code-review` 为兼容从历史 Tech Design 提升的 ADR，可以把章节顺序、缺少独立 `Trade-off Analysis` 或索引漂移降为非阻断警告；这种 review 宽容度不等于新 ADR 可以省略模板章节。

## 模板（复制即用）

```markdown
---
status: Proposed
date: "YYYY-MM-DD"
deciders:
  - <gitlab-username-or-team>
supersedes: []
superseded-by: []
---

# ADR-NNNN: <一句话决策>

## Context and Problem Statement

面临的问题是什么、为什么现在要解决、有哪些业务、技术、合规或人力约束。

## Considered Options

- Option A: <候选方案说明>
- Option B: <候选方案说明>

## Trade-off Analysis

| Option | Benefits | Costs and Risks | Best Fit |
|---|---|---|---|
| A | ... | ... | ... |
| B | ... | ... | ... |

## Decision Outcome

选择 **Option A**，因为 ...。

## Consequences

### Positive

- ...

### Negative

- ...

### Follow-up

- ...

## Links

- [Architecture overview](../index.html)
- [Related ADR](0001-related-decision.md)
```

`Pending` ADR 的 `Decision Outcome` 示例：

```markdown
## Decision Outcome

当前状态为 Pending。完成决策前必须回答：

1. ...？
2. ...？
3. ...？
```

## Status 生命周期

```text
Proposed -> Accepted -> 持续生效
Proposed -> Rejected
Proposed -> Pending -> Proposed
Accepted -> Superseded
Accepted -> Deprecated
```

铁律：

1. **不删除**任何 ADR。
2. **改决策 = 新写一条 Markdown ADR**；新 ADR 的 `supersedes` 指旧 ADR，旧 ADR 改为 `status: Superseded` 且 `superseded-by` 指新 ADR，形成双向链。
3. **不改写已 Accepted ADR 的决策内容**；只允许补生命周期 metadata、修坏链或做不改变语义的机械修正。
4. **空 ADR 不写**：信息不足时用 `Pending`，并在 `Decision Outcome` 列出待回答问题。
5. **不把 ADR 全文复制进架构页**：HTML 架构页只摘要决策并链接 Markdown ADR。

## 既有 HTML ADR 兼容

既有 `.html` ADR 是历史决策资产，**不要求批量迁移或重写**：

- 保留原路径和可用链接，避免审计历史及外部引用失效。
- 新决策仍创建 `NNNN-*.md`；不得因为目录里已有 HTML 就继续新增 HTML ADR。
- 新 Markdown ADR supersede 既有 HTML ADR 时，在两边按各自原格式更新 lifecycle metadata 并双向链接，不复制旧正文。
- 既有 `index.html` 可作为历史导航保留；`README.md` 是新 authoring 规则下的机器可读索引。兼容页只导航，不维护第二份 ADR 正文。
- 只有项目明确批准迁移时才转换历史文件；迁移必须保留原编号、状态、日期、deciders、链接和 Git 可追溯性。

## ADR 索引（`adrs/README.md`）

每个 ADR 目录放一份精简 `README.md`：

```markdown
# Architecture Decision Records

| ID | Decision | Status | Date | Supersedes | Superseded By |
|---|---|---|---|---|---|
| 0001 | [Use GrowthBook as feature flag SSOT](0001-use-growthbook-as-ssot.md) | Accepted | 2026-01-15 | — | — |
```

每新增 ADR 或改变状态都同步索引。HTML 架构页中的人读 ADR 表也必须指向同一文件，不能复制正文。

## Review checklist

- [ ] 新 ADR 文件名匹配 `^\d{4}-[a-z0-9-]+\.md$`。
- [ ] YAML front matter 五字段齐全且类型、状态、日期合法。
- [ ] 五个固定 H2 章节齐全，`Considered Options` 至少两个候选。
- [ ] trade-off 同时说明收益和代价；Decision 与 Consequences 可追溯。
- [ ] `Pending` 列出待回答问题；`Superseded` 双向链完整。
- [ ] 同目录 `README.md` 与 HTML 架构页中的 ADR 链接已同步。
- [ ] 没有新增 HTML ADR，也没有删除或无批准迁移既有 HTML ADR。

## /architect skill 的对应规则

| architect §2d 要求 | Markdown ADR 对应节 |
|---|---|
| Context（面临什么问题 + 什么约束） | `Context and Problem Statement` |
| Options（至少两个方案） | `Considered Options` |
| Trade-off（每个方案的优劣，不是只列优点） | `Trade-off Analysis` |
| Decision（选了什么 / Pending） | `Decision Outcome` |
| Consequences（接受了什么代价） | `Consequences` |

`architect` 创建新 ADR 时按此判定：五个 front matter 字段与五个章节必须齐全；空 ADR、只列结论、没有 trade-off、聚合多条决策、索引未同步均不符合 authoring 默认值。独立 MR review 对历史提升 ADR 和 README 漂移可按 `code-review` 降为警告。历史 HTML ADR 只按兼容规则检查，不套用“必须迁移成 Markdown”的门禁。

# PRD → Observability → 埋点实现

本文件是 `[PRD-OBS-TRACK]` 的日历和步骤。`SKILL.md` 只在通过/不通过和输出位置引用这里的日期。

## 日历

比较**审查日**（执行这次 code review 的日期）。MR 的创建日不参与比较。

| 审查日 | 缺口怎么记 |
|:-------|:-----------|
| 2026-10-04 至 2026-11-04（含） | `[PRD-OBS-TRACK]` ⚠️。写入「建议改进」。不写入「红线问题」。不单独改变「是否应通过」。不进入 Step 4 证伪。 |
| 2026-11-05 起 | 同一缺口是确定性红线 🔴，Step 5 结论为不通过。不进入 Step 4 证伪。不享受 pre-existing 豁免。 |

其他红线照常判定。宽限期内本门禁的全部缺口合成一条建议，矩阵留在 `### 3`。这条建议不受「建议改进最多 5 条」和「重审只报新增 P2」过滤。2026-11-05 起它是确定性红线，同样不过滤。

## 这条检查查什么

从 MR 关联 issue 的 PRD 出发，核对 observability 是否覆盖这次要交付的功能，再核对埋点代码是否实现了 observability 里的必要事件。

`tracker-publish-check.js` 检查 diff 里已经改过的点位是否达到 `releaseStatus=3`，对应红线「已实现功能无可观测」。tracking-lifecycle 的 M2 只对账已经写入 `events/metrics` 与 `selected-metrics.yaml` 的 ratio 指标；AI 给出的 `covered`、`gap`、`unknown` 只留给人看。发布状态通过、数值 PASS、或 AI `covered`，都不能当作本条通过。

## 何时做

满足任一条件就做。范围只限**本 MR 要交付的**、关联 issue PRD 里的功能。不扫整份产品里没有进这次 MR 的历史缺口。没有在交付中的存量 issue 不必回补；MR 把功能交出去时，这条链要齐。

- MR 描述有完成型 `Closes #123` 或非完成型 `Relates to #123`，且该 issue 带 `type::feature`
- diff 改变了应当埋点的用户可见产品行为

关联了多条 issue 时，只对其中的 `type::feature` 做。`Closes` 表示该 issue 上仍要交付的 PRD 功能都进矩阵。`Relates to` 只列本 diff 实际交付的功能。分不清本 diff 交付了哪几条时，矩阵不能写通过。

跳过时写一行，不写矩阵：

`[PRD-OBS-TRACK] 跳过：<原因>`

可以跳过的只有：

- `type::bug`，或技术优化，且没有新的用户可见能力
- 纯 skill、文档、CI 改造，包括 `engineering/skills` 自身这类改动

## 步骤

顺序固定为三步。SDK 调用点只作为第三步的实现证据。审查从 issue 的 PRD 开始；`track()` / `logEvent()` 不能用来回头挑选一份 PRD。

### 1. 读 issue，取出 PRD

用 MR 描述里的 `Closes #N` / `Relates to #N`，或分支名 `issue-<iid>`，打开该 issue，读描述和回链 PRD 的 comment。PRD 从这里拿，不按固定路径去仓库里猜。

issue 给出的载体不固定，下面三种都要打开并阅读：

- HTML，含 GitLab Pages 上的页面
- Markdown，含仓库里的 `.md`，以及直接写在 issue 正文里的内容
- 飞书文档

`type::feature` 的「必备物」里 **PRD / User Story** 经常是入口；空着时原文是「（待补）」。comment 里后补的链接同样算。issue 明确指向、且打得开的其他载体，也读那一份。

没有关联 issue、issue 里没有 PRD、「（待补）」、链接打不开，或多份对不上而无法确定是哪一份，记 `PRD_UNREADABLE`。不要另挑一份看起来像 PRD 的文件当作通过。

### 2. 用这份 PRD 核对 observability

Observability 设计取该 issue comment 里「方案/设计文档」回链的那份。常见文件名是 `docs/plans/YYYY-MM-DD-<feature>-observability-design.html`，以 comment 里的链接为准，不按文件名在仓库里另找一份。

设计要写到决策问题和必要事件：业务目标 → 决策问题 → 指标 → 采集。本 MR 要交付的每个 PRD 功能，都要有对应的决策问题或必要事件，并且设计写明所依据的 PRD 就是 issue 上读到的这一份（同一链接、同一修订，或 issue 正文里的同一段）。

- 没有这份设计，或 comment 没有回链：`OBS_MISSING`
- 设计引用的 PRD 与 issue 上读到的不是同一份，或设计没写依据哪一份 PRD：`OBS_PRD_MISMATCH`
- 某个要交付的功能在设计里没有决策问题，也没有必要事件：`EVENT_UNMAPPED`

设计写明某功能不采集，并写了原因，该行结论写排除。只写「以后再补」仍记缺口。

### 3. 用 observability 核对埋点实现

每个必要事件要有 SDK 调用点，写成 `file:line`（`track`、`logEvent`、`Analytics.log`，或该仓等价的上报调用）。`events/changes/<issue>.yaml` 可以同时作为事件声明，声明旁边仍要有调用点。

markdown 里写「已埋点」、埋点平台的发布状态、AI 审查给出的 `covered`，都不是实现证据。

设计里有事件、代码里没有调用点：`IMPL_MISSING`。

## 输出

矩阵放在 `### 3` 的可观测性下面。不要新开 `###` 标题。

| PRD 功能 | observability 决策问题/事件 | 代码实现 file:line | 结论 |
|:---------|:----------------------------|:-------------------|:-----|
| 分享成功 | Q：分享是否完成；事件 `share_success` | `ShareScreen.kt:88` | ✅ |
| 分享失败重试 | （设计未写） | — | ⚠️ `EVENT_UNMAPPED` |

审查日在 2026-11-04（含）之前，结论列用 ⚠️，并把同一条 `[PRD-OBS-TRACK]` 写入「建议改进」。`### 4` 追溯矩阵的可观测列对这条链写 ⚠️。

审查日自 2026-11-05 起，结论列和 `### 4` 可观测列写 🔴，写入「红线问题」，类型标 `[确定性]`。

跳过时只保留跳过那一行。

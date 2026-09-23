# Metric Layer Taxonomy —— 业务决策分类法（7 层）

本文档定义 observability 设计中 **Metric Layer** 的 7 个标准分类。Metric Layer 是**业务决策分类法**，不是数仓技术术语——回答"**这个 metric 在决策中扮演什么角色**"。

与 `ab-metric-taxonomy.md` 的关系：
- `ab-metric-taxonomy.md` 深入讲 AB 实验场景下的 4 个核心 layer（near-star / far-star / funnel / interaction + guardrail）——AB 视角
- 本文档是**超集**——覆盖 AB + 非 AB 场景的完整 7 层（多出 diagnostic、system），适用于任意 observability 设计

## Metric Layer vs Metric Type（先澄清边界）

容易混淆，先区分：

| | **Metric Type** | **Metric Layer** |
|---|---|---|
| 属于 | 技术范畴 | 业务范畴 |
| 说明 | 怎么聚合（SQL 结构） | 在决策中扮演什么角色 |
| 取值 | proportion / ratio / mean / sum / count / percentile | near-star / far-star / funnel / interaction / guardrail / diagnostic / system |
| 影响 | SQL 公式 | AB 平台角色、阈值方向、样本量预估、dashboard 布局 |

一个 metric **必须同时声明两者**。例如：
- `ticket_submission_rate`：`Metric Type = proportion`, `Metric Layer = near-star`
- `single_user_5min_popup_p99`：`Metric Type = percentile`, `Metric Layer = guardrail`（同时也是 system）

## 7 层定义

### 1. Near-Star（近端北极星）

| 属性 | 值 |
|---|---|
| 定义 | 实验周期内可观测到反馈的**直接业务行为**指标，由 treatment 短链路驱动 |
| Timeframe | 小时 - 天级 |
| 样本累积 | 快，小变化也能早期检出 |
| 驱动链路 | treatment → 用户行为 → 指标（短） |
| 分母 | 全部 scene_entry（或等价主事件） |
| 对比方式 | 跨组（treatment vs control） |
| AB 平台位置 | primary goal |
| 决策角色 | 实验"**能不能跑下去**"的早期信号 |

**例子**：SmartPopup `ticket_submission_rate`、电商 `add_to_cart_rate`

**关键陷阱**：
- 只看 near-star 不看 far-star → "假阳性"（短期涨、长期伤用户）
- 不要和 funnel 混淆——funnel 看过程效率，near-star 看业务结果

---

### 2. Far-Star（远端北极星）

| 属性 | 值 |
|---|---|
| 定义 | 实验真正要优化的**长期业务结果**，需要延迟回填 |
| Timeframe | N 天后（D7/D14/D30） |
| 样本累积 | 慢，实验要跑更久才显著 |
| 驱动链路 | treatment → 多中间环节 → 指标（长，易受混杂因素影响） |
| 分母 | 观测期已到期（`IS NOT NULL`） |
| 对比方式 | 跨组 |
| AB 平台位置 | primary goal |
| 决策角色 | 实验"**是不是真有价值**"的最终裁定 |

**例子**：SmartPopup `user_d7_retention` / `user_d30_retention` / `problem_solved_rate` / `new_subscription_rate_d7`；电商 `D30_gmv_per_user` / `subscription_renewal_rate`

**关键设计点**：
- 分母**必须**带观测期过滤（`device_active_d7 IS NOT NULL`），否则把"还没到 D7 的数据"错算成"D7 没活跃"
- Metric Type 通常 `ratio`（不是 `proportion`），因为分母是子集不是全部

**关键陷阱**：
- 光看 far-star 不看 near-star → 实验跑到黄瓜菜凉，决策慢
- **必须和 near-star 配对看**：两端都涨 = 真成功；近端涨远端跌 = 局部最优；近端跌远端涨 = 几乎不可能（质疑数据）

---

### 3. Funnel（漏斗效率）

| 属性 | 值 |
|---|---|
| 定义 | 漏斗内部各环节的**转化损耗诊断指标** |
| Timeframe | 实验当日可看（和 near-star 一样快） |
| 对比方式 | **组内**分析为主（treatment 组内部比较） |
| 分母 | 全部 scene_entry（或上一步 funnel 的分子） |
| AB 平台位置 | secondary |
| 决策角色 | **根因定位**：为什么 near-star 不涨？condition 太严？fatigue 误判？ |

**例子**：SmartPopup `show_rate` / `condition_pass_rate` / `fatigue_block_rate`；电商 `search_to_click_rate` / `cart_to_checkout_rate`

**关键误区**：
- Funnel 单独涨/跌**不是**实验成功/失败的依据（UX 优化 vs bug 难区分）
- 例：`show_rate` 降可能是 fatigue 改对了（防骚扰）也可能 condition 写错了（漏展）——要配合近端看

---

### 4. Interaction（交互质量）

| 属性 | 值 |
|---|---|
| 定义 | 在"**已经展示给用户**"前提下，用户**选择哪种交互**的分布 |
| Timeframe | 实验当日 |
| 分母 | **`shown=true`**（不是全 scene_entry） |
| Metric Type | 几乎总是 `ratio`（因为分母是子集） |
| 对比方式 | 组内为主，跨组看 UX 比较 |
| AB 平台位置 | secondary（UX 实验时可以 primary） |
| 决策角色 | UX 评估：文案 / CTA 按钮布局 / 时机 质量 |

**例子**：SmartPopup `yes_rate` / `dismiss_rate` / `upgrade_rate` / `support_action_rate`；推荐系统 `click_rate_among_exposed`

**和 funnel 的区别**：
- funnel 看"**能不能看到**"（condition/fatigue/show）
- interaction 看"**看到后选什么**"
- 分母必须在指标名或 description 里明示（"among shown"），否则 reviewer 会误以为分母是全部 scene_entry

---

### 5. Guardrail（护栏）

| 属性 | 值 |
|---|---|
| 定义 | **不能突破的红线**——防止短期收益伤长期、防止系统失控 |
| Timeframe | 实验全程 |
| 语义 | upper bound / lower bound（不是 maximize / minimize） |
| 对比方式 | 跨组，看**显著偏离**而非绝对值 |
| 阈值 | 必须事先定义（数值或显著性 level） |
| AB 平台位置 | guardrails 列表（触发即自动停实验） |
| 决策角色 | **否决权**——其它指标再漂亮，破 guardrail 就止损 |

**例子**：
- 业务 guardrail：SmartPopup `never_remind_rate`（treatment 不应显著 > control）、`subscription_churn_rate_d30`、`same_scene_repeat_d7`
- 系统 guardrail：`single_user_5min_popup_p99`（单用户高频弹窗）、`api_error_rate > 1%`

**关键特点**：
- 不需要 guardrail 涨，只要不破红线就行
- guardrail 和 near/far-star 可能同一实验矛盾 → guardrail 优先

**反模式**：
- 只设"希望指标"没设 guardrail → 实验跑出"CTA 涨但 never_remind 爆"还不知道
- Guardrail 阈值**太松**等于没设 / **太严**天天 fp 告警 → 需要 calibration

---

### 6. Diagnostic（诊断）

| 属性 | 值 |
|---|---|
| 定义 | 帮助**事后复盘**和**故障定位**的指标，**不参与实验决策** |
| Timeframe | 按需 |
| 阈值 | 无（没人定期盯） |
| AB 平台位置 | **不注册**（或注册为 secondary + hidden） |
| 决策角色 | **解释性**——其它层 metric 异常时回答"具体发生了什么" |

**例子**：
- `fatigue_blocked_by` 分布（哪条 fatigue rule 挡得最多）
- `scene_id × variation_key` 漏斗对比（哪个 scene 在实验里表现最怪）
- 错误按 error_code 的分布

**实现**：
- 通常只在 Superset / DataHub 做 slice-dice 查询
- 不进 GrowthBook factMetric（进了污染实验面板）

---

### 7. System（系统）

| 属性 | 值 |
|---|---|
| 定义 | **运行时健康**指标，属于链路③（系统指标链路） |
| Timeframe | 秒级 |
| 数据源 | Prometheus counter / gauge / histogram |
| 告警对象 | SRE / oncall（不是 PM） |
| AB 平台位置 | 不注册 |
| 决策角色 | **存活**——系统不挂才谈得上业务 |

**例子**：
- `smart_popup_http_requests_total` rate
- `smart_popup_growthbook_query_errors_total`
- `rules_version_gauge` / `engine_version_gauge`（发版追踪）

**和 Guardrail 的区别**：
- 部分 metric 既是 system 又是 guardrail（例 `single_user_5min_popup_p99`）
- 划分时以**主要决策角色**归类：
  - 主要是"防止某业务伤害"→ guardrail
  - 主要是"服务健康"→ system
  - 实在两边都重要 → 声明 `layers: [system, guardrail]`（双身份）

## 创建新 metric 时的决策流程

顺序回答（第一个命中的 layer 就选它）：

```
Q1: 是业务指标还是系统指标?
  系统 → System（链路③，结束）

Q2: 有没有明确阈值不能超?
  Yes → Guardrail

Q3: 是否实验周期内就能看到（不用等 D7/D30）?
  No → Far-Star
  Yes → 下一问

Q4: 是不是实验终极想优化的业务结果?
  Yes → Near-Star
  No → 下一问

Q5: 是不是看漏斗内部步骤损耗?
  Yes → Funnel

Q6: 是不是"已展示后用户怎么反应"（分母=shown）?
  Yes → Interaction

Q7: 以上都不是 → Diagnostic
```

## Layer 决定了下游哪些事

Metric Layer 不只是分类标签，它**决定**下游多个下游决策：

| 下游决策 | Layer 影响 |
|---|---|
| GrowthBook 注册位置 | near/far-star → primary goal；guardrail → guardrails；interaction → secondary；funnel/diagnostic → secondary 或不注册；system → 不注册 |
| 阈值方向 | near/far-star → maximize；guardrail → bounded；其它看情况 |
| 样本量需求 | Far-star 需要更长实验期才显著（观测期门槛） |
| Dashboard 布局 | near/far-star 置顶；guardrail 红绿标示；diagnostic 放展开区 |
| Refresh cadence 优先级 | guardrail 最高（不能等）；其它可以慢 |
| Alert 路由 | system → SRE；业务 guardrail → 实验 owner + PM；diagnostic → 不 alert |

## 完整对照表

| Layer | 时效 | 分母 | 对比 | 决策角色 | AB 平台位置 |
|---|---|---|---|---|---|
| **Near-star** | 实验当日-周 | 全 scene_entry | 跨组 | 实验成败早期信号 | primary goal |
| **Far-star** | D7/D14/D30 后 | 观测期到期 | 跨组 | 实验真实价值 | primary goal |
| **Funnel** | 实验当日 | 全 scene_entry | 组内 | 根因定位 | secondary |
| **Interaction** | 实验当日 | `shown=true` | 组内（跨组可） | UX 评估 | secondary |
| **Guardrail** | 实验全程 | 变化指示 | 跨组 | 否决权 | guardrails |
| **Diagnostic** | 按需 | — | — | 事后解释 | 不注册 / hidden |
| **System** | 秒级 | — | — | 存活 | 不注册（走 Prometheus） |

## 参考

- `ab-metric-taxonomy.md` — AB 实验场景下 4 层深入细节（近远端配对、跨组 vs 组内、SmartPopup 12 指标分布）
- `safety-guardrail-patterns.md` — guardrail 层的"极值/频次/降级"三种技术模式
- `dwm-wide-table-pattern.md` — fact table 上的 measure 如何在 semantic layer 聚合成 metric
- customer-care 仓参考实现：`docs/architecture/smart_popup/observability/link-1-business-metrics.md §3 Semantic Layer`

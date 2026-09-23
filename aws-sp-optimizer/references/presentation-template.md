# Presentation Template

当 `status ∈ {"ok", "ok_with_adjustment"}` 且 `recommendation.must_review_before_acting == false` 时，按本模板渲染完整报告。模板是 **9 段式教育型输出**，目标是"小白也能读懂 + 每个数字都可验证"。

## 渲染原则

1. **默认全部展开** — 不要折叠 §2/§6，用户不追问也要看得到公式和推导
2. **每段都有证据源** — 每个数字要能回溯到 JSON 某个字段，缺字段就填 "—"
3. **术语首次出现必须解释** — 要么内联一句话，要么立刻链接到 `glossary.md`
4. **数值精度匹配 JSON** — `$/hr` 保留 4 位小数（如 `$69.8696/hr`），年度金额保留 2 位（如 `$612,057.70`），百分比保留 1 位
5. **ASCII 分布图由 LLM 现场画** — 不要让 Python 输出；用 `baseline.raw_stats` 的百分位点即可
6. **绝不编造数据** — JSON 没有的字段写 "—"，并在 §8 加警告

---

## 报告骨架

### Header

```
# 📊 AWS SP 增购评估报告

**一句话结论**：推荐增购 ${recommendation.delta_per_hour_to_buy}/hr，年承诺 ${recommendation.annual_delta_usd}，预期年省 ${recommendation.expected_annual_savings_usd}。

**分析窗口**：${baseline.actual_window.end[:10]} 起前 ${baseline.actual_window.days} 天 · **Org**：${context.org_alias} (${context.account_id}) · **生成于**：${generated_at[:19]} UTC
```

---

### §1. 最终决策

渲染 7 行表，三列：术语 / 值 / 白话解释。白话文案使用下方固定文本，不得改写。前三行揭示"现有 → 最优 → 增购"的递进关系，让用户一眼看清本次下单不是从零开始。

| 术语 | JSON 源字段 | 白话解释（固定文案） |
|---|---|---|
| 现有 SP effective | `recommendation.C_existing_effective_per_hour` | 你账户里已经在跑的 Compute SP，按 30 天利用率折算后的有效覆盖。购买时 AWS 会在此基础上叠加。 |
| 最优总 commitment (C*) | `recommendation.C_star_total_per_hour` | 数学最优的**总**每小时承诺（如果你从零起购、要一次买到位，就是这个数） |
| **推荐增购 Δ** ← **下单数字** | `recommendation.delta_per_hour_to_buy` | **本次要下单的增量 = C\* − 现有 SP effective**。不重复买你已经有的那部分 |
| 年承诺 | `recommendation.annual_delta_usd` | 增购部分一年内 AWS 按 Δ×8760 小时计费，**不可退不可改** |
| 预期年省 | `recommendation.expected_annual_savings_usd` | 买了之后，本该付给 AWS 的钱少掉这么多 |
| 购后覆盖率 | `recommendation.coverage_after_purchase_pct` | 全年约有这么大比例的 compute 小时会完全落进 SP 范围吃到折扣（基于 现有 + Δ 的总 commit 算） |
| 硬风险 gate | `recommendation.must_review_before_acting` | false = 数据清洁，无需人工审核阻断；true 则 skill 会拒绝给出购买命令 |

**关键约束（小白向）**：如果你手上已经有 $A/hr 的 SP（参见第 1 行），**本次只买 Δ = $B/hr**（第 3 行），不是 $C\* = $(A+B)/hr（第 2 行）。重复买会白花钱。

### §2. 公式与变量

**完全逐字照搬**以下代码块（不得改写、不得省略、不得翻译）：

```
Newsvendor 模型（运筹学经典）：

    C* = quantile_d(X)

变量：
    X  — 每小时 compute 开销（$/hr），一个随机变量，每个 CUR 小时是它的一次观测
    d  — SP 折扣率（0~1 之间的小数），由 AWS 公共 Pricing API 按 workload 加权算得
    C* — 最优 commitment（$/hr），让"年期望总成本"数学上取到最小值的那个数
```

紧跟一段话：

> **直觉翻译**：`C* = 第 d 百分位的 X`。换句话说，让你的承诺停在"1-d 比例的时间都用得完"的水平。当前 d ≈ 0.25，所以 C* ≈ p25，意味着 **75% 的小时承诺能用完，25% 的小时会轻微浪费**——这个不对称正好平衡了 SP 的成本结构。推导见 §6。

### §3. 变量 X 的证据链

**第 1 步：元数据表**

| 项 | 值（JSON 源） | 说明 |
|---|---|---|
| 数据源 | AWS CUR via Athena | 从 `context.cur_database`/`cur_table` 读 |
| 查询窗口 | `baseline.actual_window.end` 起前 `baseline.actual_window.days` 天 | 若 `ok_with_adjustment`，actual ≠ requested |
| 样本量 | `baseline.sample_size_hours` / `baseline.expected_sample_size_hours` | |
| 完整度 | `baseline.completeness_pct`（乘以 100 显示） | <100% 说明有 CUR 缺口 |
| 计费口径 | net OD cost（实际账单，EDP 折后） | X 取自 CUR net_unblended_cost，非 list price |
| 筛选条件 | EC2 BoxUsage（非 Spot） + Fargate + Lambda | SP 可覆盖的资源，硬编码不变 |
| Exclude filter | non-empty ⇒ list axes; else "none" | 来自 `context.applied_exclude_filter` |
| SP coverage share | `applied_exclude_filter.sp_coverage_share` ×100 | 现有 SP 在 filter 后保留的有效覆盖比例；empty filter = 100% |

**第 2 步：X 的分布（ASCII 直方图）**

从 `baseline.raw_stats` 取 10 个锚点（min, p5, p10, p17, p25, p50, p75, p90, p95, max）画图。

条形长度 `bar_len = max(1, round((val - min) / (max - min) × 14))`。格式示例：

```
$62.9   min    █               
$67.5   p5     ██              
$68.5   p10    ██              
$70.6   p17    ███             
$72.1   p25    ████   ← C* 落在这里
$74.7   p50    █████           
$87.2   p75    ████████        
$96.7   p90    ██████████      
$104.4  p95    ███████████     
$120.1  max    █████████████   

mean=$79.6  std=$11.6  n=2160h
```

**第 3 步：X 的趋势（3×30 天桶）**

从 `baseline.trend_buckets[]` 渲染：

| 期间 | p17 | mean | min | max |
|---|---|---|---|---|
| `period` | `p17` | `mean` | `min` | `max` |

表后加一行：

> 趋势分类：**`baseline.trend_classification`** · b3/b1 = `baseline.trend_b3_over_b1_ratio`
>
> 解读：`stable`/`increasing` → 公式假设可接受；`mild_decline`/`moderate_decline` → 会走 trend_aware 分支（§5 会标注）；`non_monotonic` → skill 会在 §8 给 risk。

**第 4 步：已应用的排除（仅当 `applied_exclude_filter != null`）**

| 轴 | 值 |
|---|---|
| Usage type patterns | `applied_exclude_filter.usage_type_patterns` |
| Account IDs | `applied_exclude_filter.account_ids` |
| Tags | `applied_exclude_filter.tag_exclusions` 格式化 |
| Untagged rows | include / exclude (based on `include_untagged`) |
| Skipped filters | `applied_exclude_filter.skipped_filter_warnings`（若非空，警示用户）|

> 本次报告**只包含 filter 后的剩余 workload**。现有 SP effective 已按
> `sp_coverage_share = {share}` 衰减；若该值显著 <1.0，说明原 SP 正在覆盖被排除
> 的那部分 workload（双计已避免）。

### §4. 变量 d 的证据链

| 项 | 值（JSON 源） | 说明 |
|---|---|---|
| d_raw | `baseline.discount_audit.d_raw` ×100 | list-price 折扣，来自 AWS 公共 Pricing API + 30 天成本加权 |
| e_sp | `baseline.discount_audit.e_sp` ×100 | SP RecurringFee 上的 EDP 折扣（CUR net/unblended） |
| e_od | `baseline.discount_audit.e_od` ×100 | OD Usage 上的 EDP 折扣（CUR net/unblended） |
| d_calibrated | `baseline.discount_audit.d_calibrated` ×100 | **实际用于 quantile 的折扣** = EDP 校正后值（插入公式的那个数） |
| 校正已应用 | `baseline.discount_audit.calibration_applied` | true = EDP 不为零且已差异化；false = 直接用 d_raw |
| e_sp 来源 | `baseline.discount_audit.e_sp_inferred_from_e_od` | true = SP RecurringFee 记录不足，e_sp 取自 e_od |
| 匹配率 | `baseline.discount_audit.coverage_pct` ×100 | 越接近 100% 越可信 |
| Matched / Total | `matched_entries` / (`matched_entries` + `unmatched_entries`) | 具体条目数 |
| 未匹配 Top 3 | 从 `unmatched_top10_by_cost[:3]` 取 service/region/usageType | 透明披露未匹配的是什么 |

表后 **逐字照搬**公式块：

```
d 计算流程：

    d_raw = 1 − Σᵢ (costᵢ × sp_rateᵢ/od_rateᵢ) / Σᵢ costᵢ   （Pricing API 加权）

    d_calibrated = 1 − (1 − d_raw) · (1 − e_sp) / (1 − e_od)   （EDP 校正）

    C* = quantile_{d_calibrated}(X_net)   （插入公式的是 d_calibrated）
```

一句话解读：

> d_calibrated 是实际 quantile 输入。当 EDP 对 SP 费用和 OD 使用费的折扣率相同时，d_calibrated = d_raw；当两者有差异时，d_calibrated 会相应偏高或偏低，让 newsvendor 公式基于真实账单成本求解。

### §5. C* 的求解过程

**第 1 步：代入**

```
C* = quantile_d(X)
   = quantile_${d}(X)
   = ${C_star_total_per_hour}/hr   ← 来自 recommendation.C_star_total_per_hour
```

**第 2 步：稳定性检查表**

| 项 | 值 | 含义 |
|---|---|---|
| Bootstrap 95% CI | `recommendation.bootstrap_95ci_on_C_star` 或 "—（trend_aware 分支不输出）" | 越窄越稳 |
| Bootstrap SE | `recommendation.bootstrap_se` 或 "—" | 标准误 |
| 前 5 候选窗口 | 从 `search_summary.top_5_alternatives[]` 总结 `deviation_score` 范围 + 全部的 `trend_classification` | 换窗口是否结论稳 |
| 被拒候选数 | `search_summary.rejected_by_category` 各 reason 的 count 合计 | 透明披露为什么候选不够 |
| 公式分支 | `recommendation.formula_branch` | `stationary` / `trend_aware` |

**第 3 步：最优性验证 — 成本函数扫描（经验证据）**

目的：让用户**亲眼看到** `E[cost(C)]` 在 11 个 C 值上的分布，确认 C* 确实是最小值、曲线是 U 型凸函数、左右不对称。不是重复公式结论，而是提供经验证据。

**计算方法**（LLM 必须精确遵循，不得偷懒）：

1. 从 `baseline.raw_stats` 读 10 个分位点：`p0, p5, p10, p17, p25, p50, p75, p90, p95, p100`
2. 把分位点之间视为均匀分布，构造 9 个 bins：
   ```
   bin_i = (x_a=p_i, x_b=p_{i+1}, mass=(percentile_{i+1} - percentile_i) / 100)
   ```
3. 对任意 C，`E[max(0, X-C)]` 按分段积分：
   ```
   for each bin (x_a, x_b, mass):
       if C >= x_b:    contribution = 0
       elif C <= x_a:  contribution = mass × ((x_a + x_b)/2 - C)
       else:           contribution = mass × (x_b - C)² / (2 × (x_b - x_a))
   E_shortfall = Σ contributions
   ```
4. `E[cost(C)] = C × (1 - d) + E_shortfall`，`d = recommendation.discount_rate_d`

**采样的 11 个 C 值**（按此顺序填表）：

1. `p0` (min)
2. `p5`
3. `p10`
4. `p17`
5. `C* − 2`（C* 左邻 $2）
6. `C*` 取自 `recommendation.C_star_total_per_hour`
7. `C* + 2`（C* 右邻 $2）
8. `p50`
9. `p75`
10. `p90`
11. `p95`

**输出表格**（5 列，严格 11 行）：

| C 尝试值 | 分位/位置 | E[cost(C)]/hr | 年化 | 相对最优多付 |
|---|---|---:|---:|---:|
| `$x.xx` | label | `$x.xx` | `$xxx,xxx` | `+$x,xxx`（或 `$0 (最小) ✓` 仅 C* 行） |

计算规则：
- `年化 = E[cost(C)] × 8760`
- `相对最优多付 = (E[cost(C)] - E[cost(C*)]) × 8760`，C* 行固定显示 `$0 (最小) ✓`
- 数字精度：E[cost] 保留 2 位小数，年化和多付取整到美元

**ASCII 成本曲线**（基于 11 点画竖直柱状图，高度反映"相对最优多付"年化美元数）：

```
相对最优多付（年化，越高越亏）
  max $xxx,xxx ┤
               ┤
               ┤
  p95 $xxx,xxx ┤                                        ███
  p90 $xxx,xxx ┤                                    ███
  p75  $xx,xxx ┤                                ███
               ┤
  p50   $x,xxx ┤                         ███
 C*+$2  $x,xxx ┤                    ███
  p17     $xxx ┤              ███
 C*-$2     $xx ┤                ███
   C*       $0 ┤                 ▼  ← 最优点
  p10   $x,xxx ┤           ███
  p5    $x,xxx ┤       ███
  min  $xx,xxx ┤  ███
               └────────────────────────────────────────────
                 min p5 p10 p17 C*-2 C* C*+2 p50 p75 p90 p95
```

（柱子宽度固定 3 字符，高度按该 C 对应"相对最优多付"美元数在全列中的相对大小画，最高柱对齐最上一行）

**解读**（固定文案 + 动态填数）：

- 曲线形态：U 型凸函数，唯一最小值在 **C\* = ${C_star_total}/hr**
- 左侧斜率 ≈ d = ${d}，右侧斜率 ≈ 1-d = ${1-d}
- 右侧惩罚是左侧的 **${(1-d)/d:.2f} 倍**（从表中验证：C*-$2 vs C*+$2 的多付比例应接近此倍数）
- 业务含义：宁可保守买少一点、错失些折扣，也不要买多浪费承诺费用

---

**第 4 步：盘点现有 SP + 推导增购量 Δ**

**4a. 盘点活跃的 Compute SP**（来自 `current_sps[]`，仅列 `type=="Compute"` 且 `state=="active"` 的条目）：

| SP ID 前缀 | Commit/hr | 起始 | 到期 | 支付方式 | 30d 利用率 | Effective/hr | 计入 gap |
|---|---|---|---|---|---|---|---|
| `savings_plan_id[:8]`... | `commitment_per_hour` | `start[:10]` | `end[:10]` | `payment_option` | `utilization_30d_pct` | `effective_coverage_per_hour` | `counted_toward_gap` |

表底汇总行：

> **合计**：现有 `N` 条活跃 Compute SP · raw commit `Σ commitment` · effective `$${C_existing_effective_per_hour}/hr` · 最早到期 `min(end)`

若 `current_sps[]` 为空：写一行 "账户内无活跃 Compute SP，增购 Δ = C* 全额"。

**4b. 三状态对照 — 当前 → 最优 → 增购**

```
┌─ 当前已有 ────────┬─ 最优总 commit (C*) ┬─ 本次要买 (Δ) ────┐
│ $${C_existing}/hr │ $${C_star_total}/hr │ $${delta_buy}/hr  │
│ (30d util 100%)   │ (newsvendor 解)     │ (= C* - 已有)     │
└───────────────────┴─────────────────────┴───────────────────┘
                    ↓ 下单后总覆盖
            $${C_existing} + $${delta_buy} = $${C_star_total}/hr
            coverage_after_purchase_pct = ${coverage_after}%
```

**4c. Δ 的年度数字**

```
Δ = $${delta_per_hour_to_buy}/hr   ← 本次下单的唯一数字（AWS --commitment 单位，list SP fee $/hr）

年承诺（list）   = Δ × 8760                                              = $${annual_delta_usd}
年 SP 费（net 实付）= Δ × 8760 × (1 − e_sp)                                = $${expected_annual_sp_fee_usd}
年节省（net）     = Δ × 8760 × [(1 − e_od)/(1 − d_raw) − (1 − e_sp)]      = $${expected_annual_savings_usd}
```

> **重要提醒**：`annual_delta_usd` 是 list 口径（= AWS 账单上的 `SavingsPlanRecurringFee` unblended 年合计），属于**增购**，不含现有 SP。实际扣款走 `expected_annual_sp_fee_usd`（EDP 后 net 实付）。

### §6. 公式推导（为什么是 `quantile_{d_calibrated}(Y)`）

**逐字照搬**：

```
变量空间（全部 list $/hr，与 AWS --commitment 对齐）：

    A       = AWS --commitment 决策变量（list SP fee $/hr）
    X_gross = 每小时 list OD 需求（来自 CUR pricing_public_on_demand_cost）
    Y       = X_gross · (1 − d_raw)         ← 把需求折算到 --commitment 空间
    d_raw   = workload 加权 SP/OD 列表价折扣
    e_sp    = SP fee 行 EDP 折扣率
    e_od    = OD usage 行 EDP 折扣率

每小时 net 总成本：

    cost_net(A, Y) = A · (1 − e_sp)                                      ← SP fee（EDP 后实付）
                   + max(0, Y − A) · (1 − e_od) / (1 − d_raw)             ← 残余 OD（EDP 后实付）

对 A 求期望一阶导数：

    ∂E[cost_net]/∂A = (1 − e_sp) − (1 − e_od)/(1 − d_raw) · P(Y > A)

令其 = 0：

    P(Y > A*) = (1 − e_sp)(1 − d_raw) / (1 − e_od)
    ⟹ F_Y(A*) = 1 − (1 − d_raw)(1 − e_sp)/(1 − e_od) = d_calibrated
    ⟹ A* = 第 d_calibrated 百分位的 Y
```

一句直觉：

> 每多承诺 $1 list SP fee，你稳定多付 `(1-e_sp)`（EDP 后实付）；只有当 `Y > A` 的那些小时才省到 `(1-e_od)/(1-d_raw) − (1-e_sp)`。平衡点正好在 `P(Y > A*) = (1-e_sp)(1-d_raw)/(1-e_od) = 1 − d_calibrated`。换句话说，**SP 折扣越大、EDP 差异越有利，越敢买得高；反之越要保守**。

Sanity check：
- Uniform EDP（`e_sp = e_od`）→ `d_calibrated = d_raw`（EDP 校准是 no-op）
- `d_calibrated = 0` → `A* = min(Y)`，不买
- `d_calibrated = 0.5` → `A* = median(Y)`
- `d_calibrated = 1` → `A* = max(Y)`，买到峰值

### §7. 自动调整

**仅当 `status == "ok_with_adjustment"` 时渲染此段**。否则写一行：

> 无自动调整。`actual_window` = `requested_window`。

有调整时，按 `adjustments_made[]` 每条渲染：

| 项 | 原值 | 实际用值 | 原因 | 影响 |
|---|---|---|---|---|
| `adjustments_made[i].requested_window` | → | `actual_window` | `reason` | deviation_score = `trade_off.deviation_score`（freshness_loss=N 天 / duration_loss=M 天） |

表后加一句解读：

> 窗口调整由 Phase A 窗口搜索自动完成，候选窗口按 `deviation_score` 排序选最小者。被拒原因统计见 `search_summary.rejected_by_category`。

### §8. 风险与警告

**risks[]**（影响数值正确性）：

- 若 `risks` 为空：写 "无 risk。"
- 否则逐条渲染：`code` + `severity` + `user_facing_explanation`（中文，逐字照搬）
- 若任一 risk 的 `must_review_before_acting == true` → **此时本模板不应被触发**（应走 failure-handling.md 的风险处理路径）

**warnings[]**（过程/环境信号，不影响数学）：

- 若 `warnings` 为空：写 "无 warning。"
- 否则表格渲染：severity / code / message（截断到 80 字符）

### §9. 名词表

渲染一个紧凑表，包含本报告实际用到的术语（从 §1-§8 里出现的词集）。每条 1 行解释。

| 术语 | 定义（一句话） |
|---|---|
| Commitment (C) | 每小时你承诺付给 AWS 的固定美元，换折扣 |
| C* | 数学上让年总成本最小的 commitment 值 |
| Δ (delta) | 这次要增购的 commitment = C* - C_existing_effective |
| C_existing_effective | 现有 Compute SP commit × 30 天利用率 |
| X | 每小时 compute 开销（美元/小时） |
| d | SP 折扣率（0~1 加权平均） |
| p_N | 第 N 百分位数 |
| Newsvendor | 运筹学经典模型，处理"一次性采购 vs 不确定需求" |
| Bootstrap 95% CI | 重采样算出的 95% 置信区间，反映点估计稳定性 |
| Coverage | 购后预计吃到 SP 折扣的小时占比 |
| must_review_before_acting | 硬 gate，true 时 skill 拒绝给购买命令 |
| quantile_d | 第 d 百分位（d ∈ [0,1]） |
| d_raw | list-price SP/OD 折扣，来自 AWS 公共 Pricing API 加权 |
| d_calibrated | EDP 校正后的折扣，是 newsvendor 公式实际使用的 quantile 参数 |
| e_sp | SP RecurringFee 上的 EDP 折扣率（CUR net/unblended） |
| e_od | OD Usage 上的 EDP 折扣率（CUR net/unblended） |
| EDP | Enterprise Discount Program — AWS 合同级别的账单折扣 |

最后加一行：

> 完整名词表见 `references/glossary.md`。

---

## 边界处理

| 场景 | 行为 |
|---|---|
| `adjustments_made[]` 为空且 `status == "ok"` | §7 渲染 "无自动调整" |
| `trend_buckets[]` 长度 ≠ 3（不应发生） | §3 第 3 步渲染 "—"，§8 additional warning |
| `bootstrap_95ci_on_C_star == null`（trend_aware 分支） | §5 稳定性表对应行填 "trend_aware 分支不输出" |
| `unmatched_top10_by_cost[]` 空 | §4 未匹配行写 "全部匹配" |
| `risks[]` 非空但无 `must_review_before_acting: true` | 正常渲染 §8，不阻断购买建议 |
| `purchase_instructions == null` | **本模板不应被触发**——这种情况走 failure-handling.md |

## 渲染后续动作

报告末尾加一句追问（不用 ❓ 卡片，一句话即可）：

> 要执行购买吗？一次 $Δ/hr 全买，还是分两次（先买一半）？选定后按 `references/purchase-execution.md` 出审批块 + 完整命令。

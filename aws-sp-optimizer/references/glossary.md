# Glossary — AWS SP Optimizer

术语按字母顺序排列。每条：**术语名** + 一句定义 + 一段展开解释（如需）。

呈现报告时（见 `presentation-template.md` §9），取报告实际用到的子集渲染紧凑表即可；完整参考见本文件。

---

## A

**adjustments_made / 自动调整**
Phase A 窗口搜索发现请求窗口有偏差时（如命中 SP 过渡期），自动挑选候选替代窗口的记录。字段位于 `adjustments_made[]`，每条含 `reason`（原因码）、`requested_window` / `actual_window`、`trade_off.deviation_score`（偏离分，越小越接近原始请求）。

**Athena**
AWS 托管的 SQL 查询服务。本 skill 用它查询 CUR 表得到 X 的每小时观测值。

---

## B

**baseline**
过去 N 天（默认 90 天）的 compute 开销快照，是 newsvendor 公式的输入分布。对应 JSON 的 `baseline` 块。

**Bootstrap**
统计重采样方法。从样本集中有放回抽样 B 次（典型 B=1000），每次算一个统计量（这里是 C*），看 B 个值的分布宽窄。分布窄 → 点估计稳。

**Bootstrap 95% CI**
Bootstrap 重采样结果的 95% 置信区间 `[lo, hi]`。区间越窄，说明 C* 对样本扰动不敏感。JSON 字段 `recommendation.bootstrap_95ci_on_C_star`。

**Bootstrap SE**
Bootstrap 标准误，是 CI 半宽的一个简洁替代指标。SE 越小越稳。

---

## C

**C (commitment)**
每小时你承诺付给 AWS 的固定美元数。1y No Upfront 的含义是承诺 8760 小时（365×24）× C 美元，AWS 每小时从账单扣这笔钱，**不管当小时是否用得上**。

**C\* (C-star)**
Newsvendor 公式求出的**最优 commitment** — 让年期望总成本数学上取到最小值的那个 C 值。JSON 字段 `recommendation.C_star_total_per_hour`。

**C_existing_effective**
现有 Compute SP commit × 最近 30 天利用率。例：现有 $2.214/hr 的 SP，利用率 100% → `C_existing_effective` = 2.214。JSON 字段 `recommendation.C_existing_effective_per_hour`。

**Compute Savings Plan**
Savings Plans 的最灵活变种，覆盖 EC2 BoxUsage（非 Spot） + Fargate + Lambda，不锁实例族 / 区域 / OS。本 skill 只推荐这一种。

**Coverage / 覆盖率**
购后预计有多少比例的小时能完全落在 SP 范围内（即 X ≤ 新的总 commitment）。JSON 字段 `recommendation.coverage_after_purchase_pct`。

**CUR (Cost and Usage Report)**
AWS 官方账单明细表。每小时每资源一行。本 skill 通过 Athena 查询 CUR 得到 X 的 2160 个观测值（90 天 × 24 小时）。

---

## D

**d (discount rate / 折扣率)**
SP 相对 On-Demand 的折扣率。`d = 0.25` 表示 25% off。本 skill 用 AWS 公共 Pricing API 按 `实例族 × 区域 × OS` 加权算得（见 `d_blended`）。JSON 字段 `recommendation.discount_rate_d`。

**d_calibrated**
EDP 校正后的折扣率。公式：`d_calibrated = 1 − (1 − d_raw) · (1 − e_sp) / (1 − e_od)`。这是 newsvendor 公式实际用于 quantile 计算的参数。当 e_sp = e_od 时，d_calibrated = d_raw。JSON 字段 `baseline.discount_audit.d_calibrated`。

**d_raw**
来自 AWS 公共 Pricing API 的 list-price SP/OD 折扣，按工作负载加权。未经 EDP 校正。JSON 字段 `baseline.discount_audit.d_raw`。

**d_blended**
加权平均折扣率的正式字段名。计算：
```
d_blended = 1 − Σᵢ (costᵢ × sp_rateᵢ / od_rateᵢ) / Σᵢ costᵢ
```
对每个资源组合分别计算 `sp_rate / od_rate`，按最近 30 天成本占比加权。

**deviation_score**
窗口搜索给每个候选窗口打的偏离分，衡量候选与原始请求的距离。越小表示越接近原始 `requested_window`。

**Δ (delta)**
这次要下单的增购量 = C* - C_existing_effective。**用户唯一需要理解的"买多少"数字**。JSON 字段 `recommendation.delta_per_hour_to_buy`。

---

## E

**EDP / PPA (Enterprise Discount Program / Private Pricing Agreement)**
AWS 合同级别的账单折扣协议。EDP 可以对 SP RecurringFee 和 OD Usage 等不同行项目类型设置不同的折扣率，因此 newsvendor 公式中的有效折扣 `d_calibrated` 可能与 list-price 的 `d_raw` 不同。

**e_od**
OD Usage 行项目上的 EDP 折扣率（EDP discount fraction on Usage）。从 CUR `1 − net_unblended_cost / unblended_cost` 计算。JSON 字段 `baseline.discount_audit.e_od`。

**e_sp**
SP RecurringFee 行项目上的 EDP 折扣率。从 CUR 的 SP RecurringFee 行项目净/未混价格比计算。若 SP RecurringFee 记录不足，`e_sp_inferred_from_e_od == true` 时取 `e_od`。JSON 字段 `baseline.discount_audit.e_sp`。

## F

**formula_branch**
公式分支，两种取值：
- `stationary`：标准 newsvendor，C* = quantile_d(X)
- `trend_aware`：应对趋势显著下降时的扩展公式，输出不含 bootstrap CI

---

## M

**must_review_before_acting**
**硬 gate**。true 时 skill 会把 `purchase_instructions` 置为 null，拒绝给购买命令，强制用户先审核 `risks[]`。false = 数据清洁，可直接购买。

---

## N

**Newsvendor / 报童模型**
运筹学经典模型，解决"一次性采购 vs 不确定需求"类问题。经典例子：报童每天清早订 N 份报纸卖，订多了剩的退不掉，订少了错过销量 — 问订多少最优？数学结论 `N* = quantile_p(demand)`，其中 p = 边际利润 / (边际利润 + 边际损失)。SP 购买决策在数学结构上正好是这个模型。

**No Upfront**
SP 付款方式之一：全年分 8760 小时等额付，无预付。相对的有 Partial Upfront（先付部分）和 All Upfront（先付全部）。本 skill 只支持 No Upfront，因为它匹配 newsvendor 的"每小时等额成本"假设。

**non_monotonic**
趋势分类之一：3 个桶之间的 mean 非单调（先升后降或先降后升）。这种情况 baseline 分布不稳定，skill 可能给 risk 或拒绝给推荐。

---

## O

**On-Demand (OD)**
AWS 按需付费价，无任何折扣。X 在 skill 里以 on-demand 等价美元计算。

**od_rate / sp_rate**
同一个资源在 OD 和 SP 下的单价。`sp_rate / od_rate = 1 - d`。

---

## P

**p_N (第 N 百分位)**
统计学分位数。p25 = 25% 的样本 ≤ 该值。本 skill 的 C* 落在 X 分布的第 `d×100` 百分位。

**Phase A**
Skill 内部阶段，负责窗口搜索和验证（`search_summary` 来自这一阶段）。

**Phase B**
Skill 内部阶段，负责从 AWS 公共 Pricing API 建 pricing cache（首次 cold start 最慢，14 个区域 ~70min）。

**Phase C**
Skill 内部阶段，负责计算 d_blended。

**Phase D**
Skill 内部阶段，负责求 C* 并生成 recommendation。

**pricing_cache**
AWS Pricing API 定价数据的本地缓存。缓存命中时 warm 跑 <15s；冷启动要下载 rate sheet（单区域 349MB）。JSON 字段 `context.pricing_cache_version` / `pricing_cache_age_hours`。

---

## Q

**quantile_d(X)**
X 分布的第 d 百分位，d ∈ [0, 1]。公式 `C* = quantile_d(X)` 意思是：取 X 分布的第 d×100 百分位作为最优 commitment。例：d=0.25 → C* = X 的 p25。

---

## R

**rejected_by_category**
窗口搜索时被拒候选的分类统计。常见原因：
- `sp_transition_in_window`：窗口内包含已有 SP 的起/止时间
- `incomplete_data`：CUR 数据有缺口（completeness < 某阈值）
- `severe_decline`：趋势严重下降（b3/b1 < 0.85）

**requested_window / actual_window**
请求窗口 vs 实际用的窗口。`ok` 状态下两者相等；`ok_with_adjustment` 下 actual 会被前滑若干天。

**risks[]**
影响**数值正确性**的问题列表。字段含 `code`、`severity`、`user_facing_explanation`、`must_review_before_acting`、`recommended_actions_for_llm[]`。区别于 warnings。

---

## S

**Savings Plan (SP)**
AWS 的折扣承诺产品。承诺 1 年或 3 年每小时花 C 美元，换 ~20-70% off。本 skill 只计算 **1y No Upfront Compute SP**。

**search_summary**
窗口搜索结果概览。包含 `total_candidates`（固定 75）、`passed`（存活数）、`rejected_by_category`、`top_5_alternatives`。

**sp_transition_in_window**
被拒窗口的一种原因码。窗口内包含已有 SP 的起或止时间点，会让 effective coverage 在窗口内发生阶跃变化，使 X 的统计失真 — 必须避开。

**stationary**
趋势分支之一，假设"历史分布 = 未来分布"。standard newsvendor 公式下使用。对应 trend_classification 为 `stable` 或 `increasing` 时。

---

## T

**trend_classification**
3-bucket 趋势分类，取值（阈值基于 `window_search.py:classify_trend` 实现，权威解释见 `trend-handling.md`）：
- `stable`：`|ratio - 1.0| < 0.02`（对称，约 [0.98, 1.02]）
- `increasing`：`ratio >= 1.02`
- `mild_decline`：`0.95 <= ratio < 0.98`
- `moderate_decline`：`0.85 <= ratio < 0.95`（走 trend_aware 分支）
- `severe_decline`：`ratio < 0.85`（不出现在 baseline，会被窗口搜索拒）
- `non_monotonic`：buckets 非单调且 swing 超 10%（stationary 分支 + 高严重度 risk）

**trend_buckets**
90 天窗口切成 3 个 30 天等长桶，每桶给出独立的 p17/mean/min/max/sample_size。

**trend_b3_over_b1_ratio**
第 3 桶 `p17` 除以第 1 桶 `p17`，单数字趋势健康度指标。用 p17 而非 mean，因为分类阈值聚焦在"底部水平"的漂移（见 `window_search.py:classify_trend`）。

**trend_aware_audit**
当 `formula_branch == "trend_aware"` 时的审计数据，包含线性拟合系数、季节性残差等。stationary 分支下此字段为 null。

---

## U

**utilization_30d_pct**
现有 SP 最近 30 天利用率。100% = 每小时都把承诺用完。乘以 commitment 得到 `C_existing_effective`。JSON 字段 `current_sps[i].utilization_30d_pct`。

---

## W

**warnings[]**
过程 / 环境信号列表。**不影响数学正确性**，不 gate 输出。区别于 risks[]。例：`region_discovery_many_regions_slow_cold_start`。

**window / window_days / window_end**
baseline 时间窗口。默认 90 天，右端默认今天。`ok_with_adjustment` 状态下可能被滑动。

---

## X

**X**
每小时 compute 开销（美元/小时），一个随机变量。**每个 CUR 小时是它的一次观测**。skill 以 X 的分布为输入求 C*。

# 自适应阈值纪律 —— 把"写死阈值"换成"数据驱动"

> SmartPopup / cohort 异常 / 设备离群 这类调查里，最隐蔽的坑不是"方法不对"而是**把经验值硬写进 SQL**。一旦 segment / 时间 / 规模变了，那条 `>= 0.5` / `< 0.2` / `>= 10` 就会**系统性地误伤或漏判**，却因为"看起来有数字"被当成客观结论。本文件把"参数写死 → 数据自适应"沉淀为一条不可妥协的纪律。
>
> 真实踩坑见 [case-study-kf226-solar.md](case-study-kf226-solar.md) R5 Q31 / R6 Q36 / R8 Q44，以及 v0.4 scope 扩展时"不同 segment 不同硬值"再次翻车。

---

## 1. 问题 —— 写死阈值为什么危险

### 1.1 跨 segment 失效

同一个指标在不同 segment 分布差异可能超过一个数量级：

| Segment | `cohort_mean`（太阳能充电率）典型分布 | 0.5 阈值的效果 |
|---|---|---|
| Outdoor Camera（CG 系列）| 0.55 – 0.85 | 大多数 cohort 通过 → 能找出离群个体 |
| Feeder Bird（KF226 / KF126）| 0.15 – 0.35（户外低频开盖）| **全部 cohort 被过滤** → 这个 segment 整个消失 |
| Indoor（部分型号）| 0.00 – 0.05 | 全过滤（符合预期）|

硬编码 `cohort_mean >= 0.5` 把喂鸟器全部误杀 —— 不是它们没异常，是**它们的 baseline 天然就低**。

### 1.2 跨时间失效

季节 / 灰度 / 固件升级 / 地理扩张都会移动 baseline。3 月份定的 `>= 0.5` 到 9 月可能就只覆盖 60% 的 cohort。没有"重新校准"的机制 → 每次 baseline 漂移都制造一批**伪信号**。

### 1.3 跨规模失效

同一个绝对阈值在小样本 cohort 上**极不稳**：

- `cohort_size = 8` 且 `mean_rate = 0.62` → 一个异常设备就能把 mean 拉到 0.45
- `cohort_size = 500` 且 `mean_rate = 0.62` → 需要 ~100 个异常才能显著动 mean

硬 0.5 阈值对前者太敏感（false positive 飙升）、对后者太迟钝（真异常被稀释）。

### 1.4 真实案例（本 session）

- **R5 Q31**: CG625-BD2 "异常率 1.08%"，硬 `cohort_mean >= 0.5` 过滤掉了非太阳能机型
- **R6 Q36**: 加 `solar_required` 过滤后同一口径变 13.68% → **12× 差异全来自阈值选择**
- **R6 伪信号**: 被过滤的机型都归到 "other" 组，算出 55.94% "异常率" → 完全是 cohort 定义造成的假信号
- **R8 Q44**: KF226/KF126 被发现是户外喂鸟器，`cohort_mean` 天然 0.2-0.3，硬 0.5 阈值把整个 segment 排除掉
- **v0.4 scope 扩展**: 有人提议"outdoor_camera=0.5 / feeder_bird=0.3"分 segment 硬编码 → **换汤不换药**，下一个新 segment 又要拍脑袋

**症状共同点**：看起来像"调参"问题，本质是**阈值来源是拍脑袋 vs 数据自证**的问题。

---

## 2. 三层自适应框架

任何"哪些个体异常"的调查都可分成三层过滤，每层都必须用**segment 自身的分布**给出阈值，而不是全局常数。

### Layer 1 —— Model 是否进入 cohort baseline

**旧做法（硬）**：`cohort_mean >= 0.5`

**新做法（自适应）**：用 **segment 内部的 MAD-based 相对阈值**

```sql
WITH segment_stats AS (
  SELECT segment,
         percentile_approx(cohort_mean, 0.5) AS seg_median,
         -- MAD = median absolute deviation，比 stddev 更抗离群
         percentile_approx(abs(cohort_mean - seg_median), 0.5) AS seg_mad
  FROM model_cohort
  GROUP BY segment
)
SELECT m.*
FROM model_cohort m JOIN segment_stats s USING (segment)
WHERE m.cohort_mean >= s.seg_median - :k_model * s.seg_mad  -- k_model 由 fact rule 参数化
  AND m.cohort_size >= :min_cohort_size
```

**好处**：
- Outdoor camera 的 `seg_median = 0.7`, `seg_mad = 0.08` → 门槛 `~0.54`
- Feeder bird 的 `seg_median = 0.25`, `seg_mad = 0.05` → 门槛 `~0.15`
- 新 segment 进来无需改 SQL，只需定义 segment label

### Layer 2 —— Cohort 是否样本足够给出可信 baseline

**旧做法（硬）**：`cohort_size >= 50`

**新做法（自适应）**：**按型号设备数动态分档** + 小样本用 Wilson 置信区间

| Size Tier | 触发条件（示例，k 参数化）| Baseline 可信度要求 |
|---|---|---|
| Large | `cohort_size >= :tier_large`（如 500）| 可直接用 `cohort_mean`，MAD 稳定 |
| Medium | `:tier_medium <= cohort_size < :tier_large`（50-499）| 用 `cohort_mean`，但离群检测要求 ≥ 2 天数据 |
| Small | `:tier_small <= cohort_size < :tier_medium`（10-49）| 用 **Wilson lower bound** 而不是 raw rate，避免单个异常拉偏 |
| Tiny | `cohort_size < :tier_small`（< 10）| **不参与自动判定**，进人工 review 队列 |

Wilson 置信区间（95%）的下界：

```
w_lower = (p + z²/(2n) - z·sqrt((p(1-p) + z²/(4n)) / n)) / (1 + z²/n)
```

其中 `p = 观测率`，`n = 样本量`，`z = 1.96`（95% CI）。小样本时 `w_lower` 显著小于 `p`，天然"不信任"小样本。

### Layer 3 —— 设备是否真的异常于其 cohort

**旧做法（硬）**：`device_ratio < 0.2`

**新做法（自适应）**：**相对其 cohort 的偏差**

```sql
WITH cohort_robust AS (
  SELECT cohort_id,
         percentile_approx(device_ratio, 0.5) AS c_median,
         percentile_approx(abs(device_ratio - c_median), 0.5) AS c_mad
  FROM device_daily
  GROUP BY cohort_id
)
SELECT d.device_id
FROM device_daily d JOIN cohort_robust c USING (cohort_id)
WHERE d.device_ratio < c.c_median - :k_device * c.c_mad
```

或者等价用 **robust z-score**：`(device_ratio - c_median) / (1.4826 * c_mad) < -:k_device`（1.4826 是把 MAD 转成"等价 stddev"的常数）。

典型 `k_device` 取 2–3，**由 fact rule 参数化**，不入 SQL。

---

## 3. 可用统计方法 —— 何时选哪个

| 方法 | 适用场景 | 不适用场景 | 典型 k |
|---|---|---|---|
| **MAD（Median Absolute Deviation）** | 分布偏态、含离群、样本 ≥ 20 | 分布极稀疏、大量 0 | 2-3 |
| **Robust z-score** (`(x-median)/(1.4826·MAD)`) | 需要标准化打分、跨 cohort 可比 | 数据近正态（此时用标准 z 更好）| z ≤ -2 或 ≤ -3 |
| **Wilson 置信区间** | 比率型指标、小样本（n < 50）| 连续型指标 | 95% CI 下界 |
| **IQR (Q1 - 1.5·IQR)** | 经典 box-plot 风格、对称假设弱 | 严重偏态 | 1.5 或 3（远端）|
| **Percentile**（如 P10）| 想明确"最差 10% 才算异常" | 样本 < 100 时分位不稳 | P5 / P10 |
| **绝对阈值 + 业务常识** | 监管 / 合规 / 物理意义明确（如电压 < 3.0V）| 其他所有 case | N/A |

**选型决策**：

1. 先看数据是否**偏态 / 有自然离群** → 是 → MAD 系（抗离群）
2. 看**样本量是否小** → 是 → Wilson / bootstrap，避免 raw rate 被单点拉飞
3. 看**业务有没有硬性物理/合规阈值** → 有 → 绝对阈值可以用，但必须在 glossary 写清楚依据
4. 其他默认 → IQR / robust z

---

## 4. 参数化与数据源的分层

**原则**：**SQL 硬层只放结构和关系，所有"可能动的数"都上浮到 fact rule 运营层**。

### 4.1 SQL 硬层（dbt model / 中间表）

只允许出现**结构性常量**：

- `:k_model`, `:k_device`, `:tier_small`, `:tier_medium`, `:tier_large`, `:wilson_z` 等以**占位符 / dbt var / jinja 参数**形式出现
- SQL 里**不写** `>= 0.5` / `>= 50` / `< 0.2` 这种裸字面量
- 若绝对必须写（例如物理常量 `>= 3.0V`），必须在模型文件顶部注释**来源和依据**

### 4.2 Fact rule 运营层（SmartPopup / rule engine）

- 所有 k / tier 边界走 **fact 参数表**（如 `smart_popup_rule_facts.adaptive_thresholds`）
- 运营 / 数据可以在不改代码、不发版的前提下调参
- 每次调参走**配置变更审批流**，记录到 glossary.md 的参数历史表

### 4.3 Glossary 必须同步

每个 segment × 参数组合在 `glossary.md` 里有一行：

```markdown
| 参数 | 当前值 | 来源方法 | 依据 round / data 产物 | 修订历史 |
|---|---|---|---|---|
| k_model (outdoor_camera) | 2.0 | segment MAD | R6 Q36 分布图 | 2026-04-17 初始化 |
| k_device | 2.5 | cohort MAD | R5 Q31 箱线图 | 2026-04-15 从 3.0 降到 2.5 |
| tier_large | 500 | P75 of cohort_size | R8 Q44 size 分布 | 初始 |
```

**写死阈值 = 偷偷改参数 = SSOT 违规**。这三件事等价。

---

## 5. 真实 case —— 本 session 踩坑链

按时间顺序的阈值硬编码 → 自适应演化：

| Round | 问题编号 | 原硬编码 | 症状 | 修订 |
|---|---|---|---|---|
| R5 | Q31 | `cohort_mean >= 0.5`（全局）| CG625-BD2 得 1.08% 异常率 | 发现该阈值把非太阳能机型全过滤 |
| R6 | Q36 | 加 `solar_required` filter 后仍 `>= 0.5` | 同机型变 13.68% | 12× 差异 = 阈值口径 |
| R6 | Q36 伪信号 | 非太阳能机型被归到 "other" 组 | "other 55.94% 异常" 的虚假结论 | 识别为 cohort 定义 artifact |
| R8 | Q44 | `cohort_mean >= 0.5` + `solar_required` | KF226 / KF126（喂鸟器）被整段过滤 | 定位到是户外低频开盖、baseline 天然低 |
| v0.4 scope | —— | 提议 `outdoor=0.5 / feeder=0.3` 分 segment 硬值 | 仍是拍脑袋；下个 segment 又要新值 | **改用 segment MAD 方案（本文 Layer 1）**|

每一步都是"补一个硬值 → 更大的坑"。只有切到 **segment-internal MAD + size-tier + robust z** 三层才结束这条回旋镖。

---

## 6. 与其他 skill / reference 的关系

- **[cohort-anomaly-detection.md](cohort-anomaly-detection.md)** —— 它的"关键参数"表现在应当读作"来源方法"而不是"起始值"。本文件是它的方法论延伸。
- **[ssot-and-glossary-discipline.md](ssot-and-glossary-discipline.md)** —— 阈值属于 glossary 的**参数段**，每个值必须有来源、修订史、依据 data 产物。偷偷改阈值 = SSOT 违规。
- **[survivorship-bias-and-cohort-drift.md](survivorship-bias-and-cohort-drift.md)** —— 当 cohort 随时间漂移时，固定阈值更不能用。应把阈值计算放在**滚动窗口**内跟着 cohort 一起动。
- **[method-selection-guide.md](method-selection-guide.md)** —— Step 5 选型时，**自适应 vs 硬编码**是一个独立维度：同一个 cohort 方法，用硬 0.5 和用 segment MAD 是两种方法、两种结果。
- **SmartPopup 运营配置纪律** —— 所有 k / tier 值通过 fact rule 上浮到运营层，禁止回流到 dbt SQL。

---

## 7. 快速自检（每轮 Step 5 / Step 7 都过一遍）

- [ ] 我的 SQL 里有没有裸字面量（`>= 0.5`, `>= 50`, `< 0.2`）？
- [ ] 每个阈值是来自**分布统计**（median / MAD / IQR / percentile / Wilson）还是**拍脑袋**？
- [ ] 如果换一个 segment / 新 model / 新时段，这个阈值还 work 吗？
- [ ] 阈值是否在 glossary.md 里有一行，写清**来源方法 + 依据 data + 修订史**？
- [ ] k / tier 参数是否走 fact rule 而不是写进 SQL？
- [ ] 小样本 cohort 是否用了 Wilson / bootstrap 而不是 raw rate？
- [ ] 有没有"cohort 自己都异常"的情况（整组漂移）？若有，Layer 1 应切滚动基线而不是固定 MAD。

任何 NO → 先把阈值的来源补上，再下结论。

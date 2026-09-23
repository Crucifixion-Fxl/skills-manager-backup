# Cohort Anomaly Detection — 方法范本

> [method-catalog.md](method-catalog.md#cohort-anomaly-detection) 里的 sub-pattern 详解。**这是本 skill 的 1 个例子，不是"首选方法"** —— Step 5 的 3 路找方法流程决定要不要用它。

## 适用场景

当且仅当以下条件**全部**满足：

1. **问题类型**：诊断类（找"哪些个体异常"）
2. **数据特征**：群体中天然存在可比 cohort（同型号 / 同地区 / 同 cohort_month）
3. **cohort 样本量**：`cohort_size ≥ 50`（下限经验值）
4. **cohort 内同质性假设合理**：同 cohort 内个体应该行为相近（除了被观察的异常）
5. **需要相对比较**：绝对阈值会被自然偏差（纬度/季节/硬件差异）系统性误伤

不适合的情况：
- Cohort 定义不清（个体没有天然分组）
- 样本量 < 50
- Cohort 内同质性假设不成立（如混入"室内安装"会拉低整组"太阳能充电率"）

## 核心思想

不用绝对阈值（被自然偏差拉偏），用**同 cohort 横向比较**识别"本该一样但不一样"的个体。

## 2 步过滤

### Step 1: 识别"本该正常"的 cohort

```sql
WITH model_cohort AS (
  SELECT cohort_dim,
         AVG(metric) AS cohort_mean,
         COUNT(DISTINCT entity) AS cohort_size
  FROM <daily_agg_table>
  WHERE <time_window>
  GROUP BY 1
)
SELECT *
FROM model_cohort
WHERE cohort_mean >= <baseline_threshold>   -- 该 cohort 整体是健康的
  AND cohort_size >= 50                     -- 样本足以稳定 baseline
```

**解读**：只看"该 cohort 整体应该是健康的"那些 cohort。个别整体都异常的 cohort（比如某 model 大规模回归）需走别的方法（批次质检）。

### Step 2: 在合规 cohort 里找个体离群

```sql
SELECT entity,
       AVG(metric) AS entity_metric,
       -- 辅助字段用于置信度
       COUNT(*) AS days_with_data,
       AVG(reports_per_day) AS mean_activity
FROM <daily_agg_table>
JOIN model_cohort USING (cohort_dim)
WHERE cohort_mean >= <baseline_threshold>
  AND cohort_size >= 50
GROUP BY 1
HAVING AVG(metric) < <individual_threshold>
   AND AVG(reports_per_day) >= <ghost_filter>  -- 排除活跃度过低的"ghost"
```

## 关键参数 —— **用来源方法，不用硬起始值**

> ⚠️ 以前这张表写"起始值 0.5 / 50 / 0.2 / 10"，这是**反模式** —— 拍脑袋阈值跨 segment / 时间 / 规模都会系统性失效（见 [adaptive-thresholds-discipline.md](adaptive-thresholds-discipline.md) §1）。现在每个参数必须写**来源方法 + 数据产物依据**，具体 k / tier 值通过 fact rule 参数化，不入 SQL。

| 参数 | 来源方法 | 典型计算式 | 失效信号 |
|---|---|---|---|
| `baseline_threshold` (cohort_mean 下限) | **segment 内部 MAD** | `seg_median(cohort_mean) - k_model · seg_mad(cohort_mean)`（k_model 由 fact rule 给，典型 2-3）| 某 segment（如 feeder_bird）整段被过滤 → 说明用了全局值而不是 segment-internal |
| `cohort_size_min` | **size tier 动态分档** | Large/Medium/Small/Tiny 四档（tier 边界由 fact rule 给，典型 500 / 50 / 10）；Tiny 不参与自动判定 | 小样本 cohort 的 raw rate 波动过大 → 应改用 Wilson CI 下界而不是拉高下限 |
| `individual_threshold` | **相对 cohort 的 robust z** | `device_ratio < c_median - k_device · c_mad`，或 `(device_ratio - c_median) / (1.4826·c_mad) < -k_device`（k_device 2-3）| 全局 0.2 在 baseline 0.3 的 cohort 里永远命中，在 baseline 0.7 的 cohort 里过松 |
| `ghost_filter` (mean_activity) | **cohort 内 P10 / Wilson 下界** | 取 cohort 活跃度分布的 P10 或按 cohort 内 Wilson 下界筛；不要全局硬 10 | 真异常（故障伴低上报）被误过滤 → 说明硬阈值太激进 |

**参数化规则**：

- SQL / dbt model 里**只出现占位符**（`:k_model`, `:k_device`, `:tier_large`, `:wilson_z`），不写裸字面量
- 所有 k / tier 在 `smart_popup_rule_facts.*`（或等价 fact 表）配置，运营可调、发版不需
- 每个值在 `glossary.md` 有一行写明：**来源方法 / 依据 round 或 data 产物 / 修订历史**

完整框架见 [adaptive-thresholds-discipline.md](adaptive-thresholds-discipline.md)。

## Cohort 偏差指标

对每个命中的 entity 计算：

```sql
cohort_deviation = cohort_mean - entity_metric
```

- `cohort_deviation` 越大 → 越异常
- 作为 downstream 排序键 + SmartPopup trigger 优先级

## 严重度分级

不要只给一个"异常 / 正常"标签。分级：

| Band | 条件 | 典型占比 |
|---|---|---|
| A (zero) | `entity_metric = 0` | 40-80% |
| B (critical) | `< 0.05` | 3-10% |
| C (high) | `< 0.10` | 5-15% |
| D (moderate) | `< 0.20` | 5-20% |

## 常见陷阱

1. **"活跃度门槛"过高把真异常筛掉** — 坏故障常伴随上报变稀。典型"days >= 3"筛选归零。
2. **cohort 包含异质子群** — 同一 model 可能同时有户外 + 室内安装，室内恒为 0% 会拉低整组 baseline。应加额外维度过滤（`device_usage_type`、安装环境标签）。
3. **单轮命中不等于高 confidence** — 还应 cross-validate 第二信号源（见 [signal-cross-validation](method-catalog.md#signal-cross-validation)）。
4. **Cohort 定义太细**（`model × state × firmware`）—— 样本量全部不达标。Cohort 粒度应该从粗到细逐步下钻。
5. **忘了 naive baseline** — 先跑 `AVG(metric) across all entities`，看整体分布，再决定 cohort 粒度。

## 何时切换备选方法

| 现象 | 切换到 |
|---|---|
| Cohort 样本不足 | Absolute threshold + 人工 review |
| Cohort 内异质严重 | 先分层再 cohort（stratified cohort）|
| 时序主导（变点）| Change-point detection |
| 需要概率 score | Isolation Forest / One-Class SVM |
| 多维 outlier | Robust Covariance / LOF |

## 与其他方法的组合

- + **Severity stratification**：用 `entity_metric` 分桶做 downstream 差异化处理
- + **Time pattern typing**：看历史 `entity_metric` 趋势判断 "一直坏 vs 最近坏"
- + **Signal cross-validation**：第二信号源再确认

## 在本 skill 的 Step 使用

- Step 3 Find Data: 确认 cohort 维度字段 + metric 字段存在
- Step 4 Assess Data: 验证 cohort_size 分布（histogram）是否有足够多 > 50 的 cohort
- Step 5 Method Pick: 通过 3 路评估后选择 cohort 方法，写备选（e.g., "若 cohort < 50 则切 z-score"）
- Step 6 Analyze: 跑 2 步 SQL + 计算 deviation + severity band
- Step 7 Consolidate: `03-analysis/0N-cohort.md` 含完整表格
- Step 8 Revise: 若 cohort 假设被证伪（cohort 自己都异常），升级为批次质检方法

# Method Catalog — 方法目录

> SKILL.md 主体提及的方法目录。**这是起点不是终点**——Step 5 除了查本目录，还必须独立思考 + WebSearch（见 [method-selection-guide.md](method-selection-guide.md)）。

## 按问题类型分类

### 🔍 Descriptive — 发生了什么

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| Summary statistics (count/avg/p50/p95/stddev) | 任何时候第一轮 | 任意 | 只看汇总，缺模式 |
| Distribution plots (histogram / box plot) | 发现分布形状 | > 30 | 视觉，不量化 |
| Time series plot | 观察趋势 | > 14 天 | 季节性需分解 |
| Cohort retention curves | 用户留存分析 | > 100 users/cohort | 需要明确 cohort 定义 |

### 🩺 Diagnostic — 为什么

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| **Cohort Anomaly Detection** | 群体有基线，找离群个体 | cohort ≥ 50 | cohort 定义不当会误伤 |
| **Signal Cross-Validation** | 多信号源交叉对照 | 至少 2 独立源 | 源之间要真独立 |
| **Root Cause Analysis (5Whys/Fishbone)** | 单次事故事后 | N/A（定性）| 主观，需专家参与 |
| **Regression w/ interaction** | 多因素，线性/可加 | > 100 | 假设线性 |
| **Funnel Drop-off** | 多步转化路径 | > 1000 用户 | 需明确步骤定义 |

### 🔮 Predictive — 接下来会怎样

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| STL 分解 + ARIMA | 时序预测 | > 60 天稳定数据 | 对 shocks 不鲁棒 |
| Prophet (Meta) | 快速时序预测 + 节假日 | > 90 天 | 黑盒 |
| Logistic Regression | 二分类预测 | > 500 | 线性 |
| XGBoost / LightGBM | 结构化特征预测 | > 5k | 需特征工程 |
| Survival Analysis (Kaplan-Meier / Cox) | 时间到事件 / **存活偏差检验** | > 200 | 需时间戳完整。**看到 age × rate 单调趋势时必选作为候选之一**，见 [survivorship-bias-and-cohort-drift.md](survivorship-bias-and-cohort-drift.md) |

### 🎯 Prescriptive — 应该做什么

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| A/B Test + Power Analysis | 随机化可行 | power 计算得出 | 需随机化环境 |
| DiD (Difference-in-Differences) | 准实验 / 自然实验 | > 100 单位/组 | 需 parallel trends 假设 |
| RDD (Regression Discontinuity) | 阈值处断点 | 阈值附近 ≥ 50 | 断点不能被操纵 |
| IV (Instrumental Variable) | 有工具变量 | ≥ 200 | 工具变量强度难验证 |
| Propensity Score Matching | 观察研究 / 选择偏差 | > 500 | 只能控制可观测混杂 |
| Uplift Modeling | 异质处理效应 | > 5k + 对照 | 需足够 label |

### 🧪 Exploratory (EDA) — 还有什么没发现

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| Tukey EDA workflow | 拿到新数据 | 任意 | 无结构化结论 |
| K-means / DBSCAN clustering | 群体分层 | > 1k | 选 K 依赖经验 |
| PCA / t-SNE / UMAP | 高维降维可视化 | > 500 | 失真 / 不稳定 |
| Correlation heatmap | 字段关联筛选 | > 100 | 只看线性 |
| Mutual Information | 非线性关联 | > 500 | 需 bin 离散化 |

### 🔬 Confirmatory (CDA) — 假设检验

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| t-test / Mann-Whitney | 两组均值比较 | > 30/组 | 假设独立 |
| χ² / Fisher 's exact | 两组比例比较 | > 20/格 | Fisher 小样本，χ² 大样本 |
| ANOVA / Kruskal-Wallis | 多组比较 | > 30/组 | 方差齐性 |
| Bootstrapped CI | 任意统计量的 CI | > 100 | 计算重 |
| Permutation test | 无分布假设 | > 100 | 计算重 |

### 🚨 Anomaly / Outlier Detection

| 方法 | 何时用 | 最小样本 | 局限 |
|---|---|---|---|
| **Absolute Threshold** | 业务已知阈值 | 任意 | 不适合自然偏差大 |
| **Z-score / IQR** | 正态/近正态分布 | > 30 | 离群值会拉偏 |
| **Cohort relative** | 群体内相对 | cohort ≥ 50 | 假设同 cohort 同质 |
| **Isolation Forest** | 大样本，多维 | > 1k | 黑盒，参数调优 |
| **One-Class SVM** | 大样本，平滑边界 | > 1k | 慢，参数敏感 |
| **Robust Covariance (MCD)** | 多元离群，椭球分布 | > 500 | 对大偏离敏感 |
| **LOF (Local Outlier Factor)** | 密度异常 | > 500 | 计算重 |
| **Change-point detection (Ruptures / BOCPD)** | 时序突变 | > 100 点 | 参数敏感 |

## 数据特征 → 方法推荐矩阵

| 数据特征 | 适用方法 |
|---|---|
| 样本小 (< 100) | Bootstrap / Permutation test / Fisher 's exact |
| 样本中 (100-10k) | Cohort 异常 / 回归 / t-test / ANOVA |
| 样本大 (> 10k) | ML 模型 / Isolation Forest / XGBoost |
| 时序 | STL / Change-point / ARIMA / Prophet |
| 截面 | Cohort / Regression |
| 面板（含时间 + 个体）| Fixed effects / DiD |
| 高基数分类特征 | Target encoding / embeddings |
| 类别极度不平衡 | Precision/Recall + SMOTE / Focal loss |
| 缺失严重 | Imputation 前需评估；缺失本身是信号 |
| 群体天然分层 | Cohort / Stratified sampling |

## 方法类 → 推荐 skill / 工具

| 方法类 | 执行工具 | 本 skill 链 |
|---|---|---|
| SQL 聚合 | `addx:superset` (SQL Lab) | Step 6 Analyze |
| Notebook 原型 | Jupyter / Polars / pandas | 自行，建议写 `docs/research/` |
| 因果推断 | Python (`causalml`, `econml`) | 自行 |
| ML 实验 | Python (`sklearn`, `xgboost`) | 自行 |
| 流式 | Flink / Spark Streaming | 通常非本 skill 覆盖 |
| 时序预测 | `prophet`, `statsmodels` | 自行 |
| RCA 框架 | 白板 / Miro | `addx:root-cause-analysis` |

## 常用 Reference 链接

- [Exploratory Data Analysis - Wikipedia](https://en.wikipedia.org/wiki/Exploratory_data_analysis)
- [4 Types of Data Analytics - HBS](https://online.hbs.edu/blog/post/types-of-data-analysis)
- [Isolation Forest - scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- [EDA Best Practices 2026](https://blog.weskill.org/2026/03/exploratory-data-analysis-eda-best_01635878408.html)
- [Exploratory vs Confirmatory Data Analysis](https://dataheadhunters.com/academy/exploratory-vs-confirmatory-data-analysis-approaches-and-mindsets/)
- [Causal Inference: The Mixtape](https://mixtape.scunning.com/)
- [Forecasting: Principles and Practice (Hyndman)](https://otexts.com/fpp3/)

## 重要 caveat

**本目录是滞后的。不要只靠它选方法**。Step 5 必须走 3 路（本目录 + 独立思考 + WebSearch），否则你会错过业界最新方法。详见 [method-selection-guide.md](method-selection-guide.md)。

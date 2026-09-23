---
name: data-driven-investigation
description: >
  当你需要从数据出发**验证或证伪一个具体假设**时用这个 skill——典型场景是客服反馈 / 差评 / 异常现象
  归因 / 产品事故事后分析 / A/B 效果复核 / ML 特征工程预调查 / 埋点数据调查 / 用户行为异常排查。它
  强制把一次性的 SQL 查询升级成"**假设先行 + 方法选型 + 循环迭代 + 措辞纪律**"的结构化调查。每一轮
  都有明确的假设状态转移（✅证实 / ❌证伪 / ⏸暂无法验证），每一次方法选择都基于**问题类型 × 数据
  特征**判断（不锁定任何单一方法），每一次颠覆发现都沉淀到 memory 和结构化文档里。每次遇到"看一下
  数据 / 这个现象是不是 / 数据驱动地判断一下 / 归因到 / EDA / 验证假设 / 为什么 XX 变多了 / A/B 效果"
  这类话都应该调用；尤其当调查会跨多个信号源 / 需要多轮深挖 / 可能推翻原假设 / 需要在多种分析方法
  中择优时，一定要调。不适合单次简单 SQL 或已有 dashboard 能直接看到答案的场景。
---

# Data-Driven Investigation

> **核心主张**：把"看一下数据"升级成**假设驱动 + 方法选型 + 循环迭代 + 证据纪律**的结构化调查。
> 方法不预设（cohort / RCA / time series / 因果推断 都是候选），由问题类型 + 数据特征驱动选型。

---

## Description

适用场景：客服反馈归因 / 差评异常调查 / 产品事故事后分析 / A/B 效果复核 / ML 特征工程预调查 / 埋点数据异常排查 / 用户行为异常归因。把一次性 SQL 升级成"**假设先行 + 方法选型 + 循环迭代 + 措辞纪律**"的结构化调查：每一轮都有明确假设状态转移（✅证实 / ❌证伪 / ⏸暂无法验证），每一次方法选择都基于**问题类型 × 数据特征**，每次颠覆发现都沉淀到 memory 和分层文档。

不适合：单次简单 SQL、已有 dashboard 能直接看到答案的场景。

---

## Rules

1. **先写假设再看数据** —— 假设必须可证伪；事后凑假设等于自我欺骗
2. **3 路找方法，不锁方法** —— 查 catalog + 独立思考 5 问 + WebSearch，至少比较 2 种方法才选
3. **Find Data + Assess Data 独立于 Analyze** —— 先确认字段存在、填充率、分区策略，再跑分析（避免拿错数据跑完才发现假设失效）
4. **措辞三态绝不混用** —— ✅证实 / ❌证伪 / ⏸暂无法验证（识别缺失属于 ⏸，绝不能写成 ❌）
5. **每轮一个 analysis file** —— 避免一条 monster SQL，round N 的 consolidation 放 `03-analysis/0N-*.md`
6. **并行派 agent 但不嵌套** —— 同轮 > 3 题必须并行，但 sub-agent 不能再派 sub-agent
7. **基础设施瓶颈升级再循环** —— 撞 bytes 限额 / 表不存在等不是"证伪"，是要建 dbt dws 中间层再回来重跑
8. **memory 沉淀** —— 颠覆性发现（假设被证伪 / 方法不适配 / 数据字段失效）必须写 memory，避免下次重踩

---

## 为什么需要这个 skill

数据调查最常犯的 4 类错：

1. **先看数据再编假设** — 事后凑的假设永远能 cherry-pick 支持，拿不到真相
2. **方法锁定（method lock-in）** — 每次都用同一把锤子（绝对阈值 / 单一 cohort / 简单 avg），不看问题是否适配
3. **一次性 SQL** — 没有 round-by-round 纪律，发现新 gap 时随手加一条，结论脉络断裂
4. **武断措辞** — 把"暂无法验证"说成"证伪"是工程文化里最致命的数据欺骗

本 skill 把这四类错变成**主动纪律**。

---

## 核心哲学（3 条，不妥协）

### 1. Hypothesis-First（假设先行）

**不允许先跑 SQL 再编假设**。每次调查开始必须先写：

```
原假设：[一句话]
证据来源（为什么这么假设）：[业务反馈 / 客服工单 / 数据趋势 / 直觉]
可证伪条件：[什么样的数据会让我判定此假设为假]
```

没有"可证伪条件"的假设就不是假设，是偏见。

### 2. Method Selection by Problem × Data（方法选型基于问题 × 数据）

**不锁定任何方法**。看问题类型（描述/诊断/预测/规范）+ 看数据特征（样本量/时间结构/维度/干预是否可观测）再选。

详见下面 [方法选型框架](#方法选型框架) 章节 + [references/method-selection-guide.md](references/method-selection-guide.md)。

### 3. Iterative Loops + Evidence Discipline（循环 + 措辞纪律）

- **循环**：不允许一次性 EDA 到底。每轮的结论产生新 gap，新 gap 派生下一轮问题。
- **措辞**：所有关于假设的判定必须落入三态：

| 状态 | 含义 | 允许的措辞 |
|---|---|---|
| ✅ 证实 | 在独立信号源上交叉验证过 | "已验证 XX" / "数据支持 XX 假设" |
| ❌ 证伪 | 有明确反证 + 数据覆盖充分 | "XX 被证伪" |
| ⏸ 暂无法验证 | 识别缺失 / 数据未 backfill / 样本不足 | "暂无法验证 / 识别缺失 / 需补数据" |

**最致命错误**：把 ⏸ 当成 ❌。详见 [references/hypothesis-discipline.md](references/hypothesis-discipline.md)。

---

## 方法选型框架

### 第 1 步：识别问题类型（选 1-N）

基于 [Harvard Business School 4 analytics 分类](https://online.hbs.edu/blog/post/types-of-data-analysis) + Tukey EDA / CDA 二元：

| 问题类型 | 本质问题 | 例子 |
|---|---|---|
| 🔍 **Descriptive** | 发生了什么 | "最近差评涨了吗" |
| 🩺 **Diagnostic** | 为什么会这样 | "为什么 KF226 充电不良投诉多" |
| 🔮 **Predictive** | 接下来会怎样 | "下周会继续涨吗" |
| 🎯 **Prescriptive** | 应该怎么办 | "弹窗要在什么阈值触发" |
| 🧪 **Exploratory (EDA)** | 还有什么没发现 | "这批数据有什么 pattern" |
| 🔬 **Confirmatory (CDA)** | 这个已知的假设成立吗 | "A/B 差异是否显著" |

### 第 2 步：观察数据特征

| 维度 | 值域 | 影响 |
|---|---|---|
| 样本量 | 小(<100) / 中(100-10k) / 大(>10k) | 小 → 统计检验功率不足；大 → 适合 ML |
| 时间结构 | 截面 / 面板 / 纯时序 | 面板 → DiD / fixed effects；时序 → STL、change-point |
| 干预可观测性 | 可随机化 / 观察性 | 可随机 → A/B；不可 → 因果推断（DiD, RDD, IV, matching）|
| 标签可用性 | 有标签 / 无监督 | 有 → 监督 ML；无 → 聚类、Isolation Forest |
| 群体是否有基线 | 同型号/同地区可比 / 独立个体 | 有基线 → cohort 异常法；无 → z-score / Isolation Forest |
| 数据质量 | 干净 / 含缺失 / 大量 NULL | NULL heavy → 先 data quality 调查，不能直接建模 |

### 第 3 步：**多路**找方法（不只是查目录）

查目录只是起点，真正的方法选型要**走 3 条平行路径**，取并集后再评估：

#### 路径 A: 查本 skill 的 method catalog
查 [references/method-catalog.md](references/method-catalog.md) 的决策树，挑 1-3 个候选：

| 问题 × 数据 | 推荐方法 |
|---|---|
| 诊断 × 群体有基线 × 样本中大 | **Cohort 异常检测** + severity 分级 |
| 诊断 × 时序数据 × 有变点 | Change-point detection + STL 分解 |
| 诊断 × 多因素 × 样本大 | Regression + interaction terms |
| 规范 × 可随机化 | A/B test + power analysis |
| 规范 × 不可随机化 | DiD / 断点回归 (RDD) / 匹配 |
| 异常 × 大样本 × 无监督 | Isolation Forest / One-Class SVM |
| 探索 × 新数据 | Tukey EDA workflow |
| 事故 × 单次 | RCA 框架 → delegate 到 `addx:root-cause-analysis` |

#### 路径 B: **独立思考**——问自己 5 个问题

目录是滞后的，自己的思考永远是第一生产力：

1. **如果我完全不懂统计，直觉会怎么做？** — 常识方法往往就是好方法（eg. "跟同款比较"、"看趋势"、"按时间分段"）
2. **这个问题如果换成另一个领域会怎么解？** — 医学做诊断、质检做缺陷检测、金融做欺诈、电商做归因——跨域移植通常很有效
3. **相反的视角：如果我想**证伪**这个假设，数据应该长什么样？** — 设计"反面实验"能避免确认偏差
4. **这个问题的**最简版本**是什么？有没有 90/10 方法先跑个粗结果？** — 避免一上来就用重武器
5. **方法 A / B / C 分别会在什么情况下失败？** — 预先思考失败模式，才能在第一轮命中"水土不服"时快速切换

#### 路径 C: **web 搜索**最新方法 / 相关领域实践

用 `WebSearch` 搜索至少一次。典型 query 模板：

- `"<问题类型> detection 2026 methodology"` — 如 `"anomaly detection iot device 2026"`
- `"how to detect <X> in <domain>"` — 如 `"how to detect faulty battery charging iot telemetry"`
- `"<我的候选方法> limitations alternatives"` — 避免方法锁定
- `"<数据特征> analysis techniques"` — 如 `"sparse event data analysis techniques"`

**核心目的**：
1. 验证目录里的方法在业界是否已被证明有效
2. 发现目录没收录的新兴方法（比如新的 ML 算法、统计技术）
3. 找到**相邻领域的现成解法**（医疗/金融/电商常有更成熟的方法可借鉴）

Web 搜索结果一定要注明**来源链接**，并评估其可信度（arXiv / 厂商 blog / Wikipedia / Stack Overflow 可靠性不同）。

#### 合并 3 条路径：筛选候选

取并集后，用以下标准收敛到 1-2 个候选：

| 评估维度 | 打分 |
|---|---|
| 能否用现有数据跑（Step 3/4 已知数据特征）| 必须 YES，否则跳过 |
| 实现复杂度 | 优先低复杂度的（SQL aggregation > ML model）|
| 可解释性 | 业务方能否理解结论 |
| 已知局限的严重程度 | 不能把致命局限 push 到生产 |
| 是否有 reference 实现 / 现成 SQL 模板 | 有 reference 的优先 |

#### 方法选型文档化要求

在 01-background.md 或 04-findings.md 写入：

```markdown
## 方法选型

**选择**: <方法名>
**理由**:
  - 数据特征 XX 匹配该方法的前提（<samples> >= <threshold>）
  - 目录 / web 搜索 / 自己思考 得出的 3 个候选：A / B / C，我们选 A 因为……

**替代方案（备选，当 A 失败时切换）**:
  - B: <什么时候切到 B>
  - C: <什么时候切到 C>

**已知局限**:
  - <至少 1 条>

**参考**:
  - [链接 1]
  - [链接 2]
```

### 第 4 步：方法不合适就换

EDA 是**反馈循环**：初选方法如果在第一轮发现水土不服（样本不足 / 假设不满足 / 结果不可解），**应切换到备选方案**而不是硬调参。举例：本 session 中"cohort 异常法需要 50+ 设备 model"—— 如果目标 model 只有 10 台，应该切到 z-score + 人工 review 而非强行套 cohort。**预先写好备选方案**在 Step 3 的文档里，切换时就不用重新 brainstorm。

---

## Loop Pattern — 每一轮 8 步

```
┌─── Round N ───────────────────────────────────────────────────────┐
│                                                                    │
│  1. State           已知事实 + 假设声明（含可证伪条件）+ 上轮 gap   │
│  2. Questions       本轮问题清单（每题带"想证实/证伪什么"标注）     │
│  3. 🔍 Find Data    找数据源——每题对应哪张表/哪个字段？             │
│                     MUST invoke `addx:datahub-schema-search`        │
│                     验证表/字段真实存在、类型匹配、分区结构         │
│  4. 🧪 Assess Data  评估数据质量 / 覆盖率 / NULL 率 / 样本量         │
│                     跑一个 sanity-check SQL（COUNT / distinct / NULL│
│                     比例）再决定能否用——不允许假设数据干净          │
│  5. 🧭 Method Pick  **3 路找方法**: (A) 查 method-catalog           │
│                     (B) 独立思考/跨域移植  (C) web 搜索最新实践     │
│                     合并后打分收敛到 1 主方法 + ≥1 备选              │
│                     文档化理由 + 备选切换条件 + 已知局限             │
│  6. 🔬 Analyze      并行 dispatch + SQL 执行                        │
│                     MUST invoke `superpowers:dispatching-parallel-  │
│                     agents` + `addx:superset` (SQL Lab)             │
│  7. Consolidate     append to 03-analysis/0N-<name>.md              │
│                     每题: 数据 + finding + 对下一阶段启示            │
│  8. Revise & Decide ✅/❌/⏸ 状态转移 + memory 沉淀颠覆发现 +         │
│                     决定下一轮 focus / 升级基础设施 / 固化退出       │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 每步的硬性要求

| Step | 必做动作 | 失败就别进下一步 |
|---|---|---|
| **1 State** | 写假设 + 可证伪条件（3 行以内），承接上轮 gap | 没写假设就别跑数据 |
| **2 Questions** | 每题标注"想证实什么" vs "想证伪什么" | 无标注的问题 = 随意探索，不算本 skill 的流程 |
| **3 Find Data** ⭐ | invoke `addx:datahub-schema-search` 验证每题用到的表/字段 | 绝不**凭记忆写** `SELECT col FROM table`——字段不存在就 fail fast |
| **4 Assess Data** ⭐ | 跑 sanity-check SQL：`SELECT COUNT(*), COUNT(DISTINCT key), SUM(CASE WHEN col IS NULL THEN 1 ELSE 0 END)` | 数据覆盖率 < 50% 或 NULL 率 > 50% → 提示到 04-findings.md，不当真结论 |
| **5 Method Pick** | **3 路找方法**（目录查询 + 独立思考 + WebSearch）+ 写"为什么选 X 而不是 Y/Z"+ 备选切换条件 + 已知局限（≥ 1 条） | 只查目录 = 方法锁定 anti-pattern；无 WebSearch = 忽视业界最新实践 |
| **6 Analyze** | 并行跑 + 每题独立 SQL（不要 Monster SQL） | 同 session 内跑 ≥ 3 题用 dispatching skill |
| **7 Consolidate** | 每题实时写进 03-analysis/0N-*.md（不是最后一把写）| 只留在 terminal 就等于没调查 |
| **8 Revise & Decide** | ✅/❌/⏸ 三态 + 颠覆性发现 → memory | 把 ⏸ 说成 ❌ = evidence-discipline 违规 |

### 为什么把 "Find Data" 和 "Assess Data" 拆成独立 step

大部分数据调查失败的**真因是"数据缺口"而非"方法不对"**。常见数据缺口模式：

| 模式 | 典型症状 | 发现时机 |
|---|---|---|
| **关键字段低填充** | 理论上金标准字段在生产数据里 > 50% NULL / 空字符串 | 第 4 步 sanity-check 必抓 |
| **新实体未入 dim** | 新产品/feature 上线但 dim 表未注册 → 按 ID 精准过滤失败 | 第 3 步 schema 查询时发现 |
| **backfill 未完成** | 字段存在但历史数据大量 NULL | 第 4 步分布统计发现 |
| **样本量不足** | 目标群体 < 方法的最小样本要求（如 cohort 方法需 50+ 同类）| 第 4 步 distinct count |
| **分区策略不利** | 大表只按日期分区，按 SN/uid 筛选无法 partition prune → 扫描 bytes 超限 | 第 4 步试跑时撞 quota |
| **字段口径不一致** | 多个信号源"同名字段"其实不同源（如衍生字段 vs 原始字段）| 第 5 步方法选型时应对比 |
| **维度缺失** | 缺某个切片维度（地理/版本/用户属性）| 第 3 步列数据源时显式检查 |

这些都**应在第 1 轮跑分析 SQL 之前就发现**——拆独立步骤是为了：

1. **Fail fast**：数据不支持某假设时不浪费一轮 analysis
2. **方法选型可行性**：Step 5 方法选型必须基于 Step 3/4 的真实数据情况，而非理论期望
3. **缺口暴露即产出**：数据缺口自身就是重要结论（应写入 04-findings.md "数据缺口"章节 + `addx:gitlab-issue-sop` 建 issue 让上游修）

详细模板 + 反面例子：[references/loop-pattern.md](references/loop-pattern.md)

---

## 分层文档结构（中间产物）

```
docs/scenarios/<feature>/<topic>/
├── README.md           入口（TL;DR + 文件地图 + 链路图）
├── 01-background.md    业务背景 + 范围 + 决策 + 方法选型理由
├── 02-data-sources.md  数据源 + 缺口 + 处理流水线
├── 03-analysis/        每一 Round 一个文件
│   ├── README.md       链路索引
│   ├── 01-baseline.md
│   ├── 02-signals.md
│   ├── 03-patterns.md
│   ├── 04-segments.md
│   └── 05-deep-dive.md
├── 04-findings.md      综合结论 + 假设修订表（✅/❌/⏸）+ 方法局限
├── 05-algorithm.md     （可选）算法/方案设计
└── 06-deployment.md    （可选）部署 / 灰度 / DoD
```

详细模板：[references/layered-doc-template.md](references/layered-doc-template.md)

---

## 方法目录（完整列表）

下面是**候选方法清单**，不是"本 skill 必须这么做"。选型时查 [references/method-catalog.md](references/method-catalog.md)：

### 诊断（Diagnostic）类
- **Cohort Anomaly Detection** — 同型号/同地区横向比较，消化自然偏差 · [详](references/cohort-anomaly-detection.md)
- **Signal Cross-Validation** — 两个独立信号源都异常才高 confidence
- **Root Cause Analysis (RCA)** — 5 Whys / Fishbone / Fault Tree · 用 `addx:root-cause-analysis`
- **Funnel Drop-off Analysis** — 看哪一步转化跌

### 异常检测（Anomaly）类
- **Absolute Threshold** — 简单但不适合自然偏差大的群体
- **Z-score / IQR** — 参数化/非参数化统计 outlier
- **Isolation Forest / One-Class SVM** — 大样本 ML 无监督 · [参考 scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- **Time-series Change-point** — 突变点检测（Ruptures, BOCPD）

### 时序（Time Series）类
- **STL 分解** — 趋势 / 季节 / 残差
- **ARIMA / Prophet** — 预测
- **Retention / Cohort Curves** — 用户留存分析

### 因果（Causal Inference）类
- **A/B Test + Power Analysis** — 可随机化首选
- **Difference-in-Differences (DiD)** — 准实验
- **Regression Discontinuity (RDD)** — 阈值处断点
- **Instrumental Variable (IV)** — 有工具变量
- **Matching / Propensity Score** — 观察研究

### EDA 基础类
- **Tukey EDA workflow** — Shape → Types → Missingness → Duplicates → Target sanity → Leakage → Distributions → Relationships
- **Data Profiling** — 字段统计 / NULL 率 / 基数 / 分位数

完整决策树 + 每方法的 when-to-use / 局限 / SQL 或代码模板：[references/method-catalog.md](references/method-catalog.md)

---

## 必须调用的其他 skills（强制，不是可选）

本 skill 是**编排器**，绝不自己重造数据工具。在以下时刻**必须 invoke 对应 skill**（不是"参考"，是"调用"）：

### 强制触发表（对应 8 步 Loop Pattern）

| 时刻（Loop step）| MUST invoke | 怎么判断触发 |
|---|---|---|
| **Step 3 🔍 Find Data** — 找数据源 | **`addx:datahub-schema-search`** | 任何时候你想"查 XX 表有什么字段 / 哪张表有 XX 数据"；不要猜字段名，不要猜表存在 |
| **Step 4 🧪 Assess Data** — 评估数据质量 | **`addx:superset`**（SQL Lab 跑 sanity-check）| 跑 COUNT + NULL 率 + 覆盖率 sanity-check 前确认用 Superset，不要直接 Athena CLI |
| **Step 5 🧭 Method Pick** — 3 路找方法 | **`WebSearch`** + `addx:root-cause-analysis`（若 RCA）| 不只是查目录——要 web 搜索业界最新方法 + 考虑跨域移植 |
| **Step 6 🔬 Analyze** — 跑 SQL / 分析数据 ⭐ | **`addx:superset`**（SQL Lab）+ **`superpowers:dispatching-parallel-agents`** | **任何需要跑 SQL 的时刻强制走 Superset SQL Lab**（不要直接用 Athena CLI / bash hive）；问题数 ≥ 2 必须派并行 subagents，不串行跑 |
| **Step 6 🔬 Analyze** — 固化发现为看板 | **`addx:superset`**（建 chart/dashboard）| 发现值得长期观察时，立即在 Superset 建 chart/dashboard 固化，不是一次性 query |
| **Step 7 Consolidate** — 产出 dbt model | **`addx:gitlab-mr`** | 产出 dbt 中间层 / ads 层时 |
| **Step 8 Revise** — 颠覆性发现 | **`auto-memory`** | 当结论推翻原假设或发现系统级事实时立即写 memory file（不然下次再踩同坑）|
| **Step 8 Decide** — 下游动作 | **`addx:gitlab-issue-sop`** | 结论需要 downstream 动作（dev 改代码、PM 决策、数据 backfill）时立即建 issue |
| 方法选型 = 事故事后 RCA（5Whys/Fishbone/Fault Tree）| **`addx:root-cause-analysis`** | 直接 delegate，本 skill 退居编排 |
| 数据缺口需要建 dbt 中间层 | **`addx:gitlab-mr`** | 原始表扫描撞 bytes 限额 / 需长期聚合字段 |
| 发现告警/指标关联 | **`addx:sla-alert-analysis`** | SLA 告警聚合分析专治 |

### 反模式（Anti-Pattern）

❌ **用 `Bash('hive -e ...')` 或直接 curl Athena API** — 正确做法是 invoke `addx:superset` SQL Lab
❌ **凭记忆写 `SELECT col_name FROM some_table`** — 正确做法是先 invoke `addx:datahub-schema-search` 验证
❌ **一个 Round 里串行跑 5 个 SQL** — 正确做法是 invoke `superpowers:dispatching-parallel-agents` 派 5 个子 agent
❌ **发现颠覆性结论只写进本次文档** — 正确做法是同时 invoke `auto-memory` 写 project memory
❌ **手工 `glab mr create` 不走 MR skill** — 正确做法是 invoke `addx:gitlab-mr`，由它驱动到真正可合并

### 相近但不同的 skills（不要混用）

- `addx:root-cause-analysis` — **RCA 框架专用**（5Whys / Fishbone / Fault Tree / FMEA）。适合**单次事故事后**分析。本 skill 覆盖面更广 + 强调循环 + 方法选型开放
- `addx:tool-exploration` — 探索**工具/SDK**的用法，不是现象归因
- `addx:product-tech-research` — **方案/竞品**调研
- `addx:observability-design` — 监控体系设计
- `addx:testing-strategy` — 测试方案设计
- `addx:sla-alert-analysis` — SLA 告警拉取与分析（数据领域相近，但专治告警聚合）

### 调用示例（真实 session 动作）

调查 "KF226 太阳能充电异常" 时的典型 skill 序列（按 Round 展开）：

| Round | 动作 | invoke skill |
|---|---|---|
| 0 | 启动调查，需先了解"太阳能充电"相关字段 | `addx:datahub-schema-search` (`query=solar charging device`) |
| 1 | 跑 1,549 suspect 设备 cohort 查询 | `addx:superset` (SQL Lab) |
| 1 | 发现"KF226 在 dim 里查不到" | `auto-memory` 写 project_kf226_model_dim_unregistered.md |
| 2 | 本轮派 7 个 EDA 问题并行跑 | `superpowers:dispatching-parallel-agents` |
| 2 | 要把"异常用户清单"固化展示 | `addx:superset` 建 dataset 2799 + dashboard 476 |
| 2 | 数据发现需 dev 跟进 | `addx:gitlab-issue-sop` 建 issue #75 |
| 3 | 发现"KF226 归因不成立"是武断结论 | `auto-memory` 写 project_solar_popup_cross_tenant.md |
| 3 | 扫描原始 PIR 表撞 bytes 限额 | `addx:gitlab-mr` 推 dbt dws 中间层 MR !3037 / !3038 |
| 固化 | 方案文档分层结构 | `addx:gitlab-mr` 推 docs MR !120 |

---

## Worked Example

**Case**: kiwibit KF226 太阳能充电异常识别（2026-04-19）

3 轮 Round × 20 个 EDA 问题的完整调查。Round 3 在用本 skill 的"措辞纪律"时把原本武断的 "KF226 归因
不成立" 校准成 "⏸ 暂无法验证（识别缺失）"，避免错误叙事固化。方法选型上：尝试了**绝对阈值**（失败：
坏充电板设备低功耗上报稀，days >= 3 筛选归零）→ 切到**cohort 异常法**成功命中 1,549 suspect 设备。

最终交付：Superset dataset 2798+2799 / dashboard 475+476 / dbt dws 中间层 / GitLab issue #75。

详细复盘 + 方法切换的决策链：[references/case-study-kf226-solar.md](references/case-study-kf226-solar.md)

---

## Anti-Patterns

1. **"让我先看一下数据"** → 没写假设就跑 SQL。强制先写假设。
2. **方法锁定** → 每次都用同一把锤子。强制第 5 步做"方法选型文档化"。
3. **把 ⏸ 当成 ❌** → 数据里没找到 ≠ 不存在。永远先问 "是数据没覆盖到还是真的不存在？"
4. **一条 Monster SQL 覆盖所有问题** → 每 Round 一问，每问独立。
5. **没有 Round 间 state 追踪** → 每轮开头写 "上轮结论 + 本轮待解 gap"。
6. **偶然发现当确定结论** → 未 cross-validate 不能进 04-findings.md。
7. **文档写在最后** → 边跑边记，03-analysis/0N-*.md 在 Round N step 7 完成。
8. **不写 memory** → 下次踩同一坑。每次颠覆性发现必须 memory。
9. **⭐ 凭记忆写字段名** → 没查 DataHub 就写 `SELECT col FROM table` 是最常见误判源。永远先 invoke `addx:datahub-schema-search`。
10. **⭐ 跳过数据质量评估** → 跑完分析才发现 NULL 率 90% 是最大浪费。Step 4 的 sanity-check 非可选。
11. **手工 `glab`/`curl` 代替 skill** → 走 `addx:gitlab-mr` / `addx:gitlab-issue-sop`，它们有 CI 校验、blob 链接规范、assignee 纪律。
12. **⭐ 只查 method-catalog 就拍方法** → 目录是滞后的。Step 5 MUST 走 3 路：(A) 查目录 (B) 自己思考相邻领域+反向验证+简化版本 (C) WebSearch 业界最新实践。漏任一路都算方法锁定。
13. **⭐ WebSearch 不记来源** → 搜索结果务必保留 URL 链接并评估可信度（arXiv / 厂商 blog / 社区回答可信度不同）。
14. **⭐⭐ 把 "存活偏差" 当成 "时间效应"** → 看到 cohort 按 bind_age / tenure / session 等时间变量分桶呈现**单调趋势**（失败率下降、留存率上升、错误率降低）时，第一反应**不是**"随时间稳定下来"，而是先问"**前面的桶里失败个体被退货/流失/注销筛掉了吗？**" 如果不检查退出事件就下"时间效应"结论，会把整个业务目标搞反（例如把"赶在退货前拦截" 误做成 "找异常推 RMA"）。详见 [references/survivorship-bias-and-cohort-drift.md](references/survivorship-bias-and-cohort-drift.md) 的 4 问自检。
15. **⭐⭐ 没有 glossary / SSOT，同名术语跨 round 口径漂移** → 同一个词 "suspect" 在 R1 指 cohort heuristic / R3 指 ads 表全量 / R4 指 popup 最严 filter，三个数字分别 1.08% / 0.009% / 14.78% 却被当作可比。**每场景必须有 `glossary.md`** 作为变量/参数/术语 SSOT；同名不同口径必须 rename（`cohort_suspect` / `ads_suspect` / `popup_suspect`）；结论翻转时更新 `04-findings` + 写入 `ARCHIVE.md`，**不改 `03-analysis/0N` 证据账本**。详见 [references/ssot-and-glossary-discipline.md](references/ssot-and-glossary-discipline.md)。
16. **⭐⭐ 写死阈值而非数据自适应** → SQL 里出现 `cohort_mean >= 0.5` / `cohort_size >= 50` / `device_ratio < 0.2` 这种裸字面量 = 拍脑袋阈值。换一个 segment / 新 model / 新时段立刻误伤或漏判（real case：R5 Q31 / R6 Q36 / R8 Q44，硬 `>= 0.5` 把喂鸟器整段过滤，又把非太阳能机型归到 "other" 造出 55.94% 伪信号）。**正确做法**：Layer 1 用 segment 内部 MAD（`cohort_mean >= seg_median - k·MAD`）、Layer 2 按 cohort_size 动态分档 + 小样本走 Wilson CI、Layer 3 设备异常判定用相对其 cohort 的 robust z-score。所有 k / tier 边界通过 **fact rule 参数化**，不写进 SQL；每个阈值在 `glossary.md` 有一行写明"来源方法 + 依据 data 产物 + 修订史"。详见 [references/adaptive-thresholds-discipline.md](references/adaptive-thresholds-discipline.md)。

---

## Rigid Checklist（每轮必做）

每 Round 结束前必须能回答 YES：

- [ ] Round 开头写了假设 + 可证伪条件？
- [ ] 每个问题带"想证实/证伪什么"标注？
- [ ] ⭐ **Find Data**：每个问题用到的表/字段通过 `addx:datahub-schema-search` 验证真实存在？
- [ ] ⭐ **Assess Data**：跑过 sanity-check（COUNT + NULL 率 + 覆盖）？数据质量问题已标注？
- [ ] 每个问题走过 **3 路**找方法（目录 + 自己思考 + WebSearch）？
- [ ] 每个问题有明确的方法选型理由 + **备选方案** + 已知局限？
- [ ] 每个问题独立跑过（没被合并成 Monster SQL）？
- [ ] 用 `superpowers:dispatching-parallel-agents` 派并行 agent（≥2 题时）？
- [ ] 结果写进 03-analysis/0N-*.md？
- [ ] 明确区分 ✅/❌/⏸ 三态？
- [ ] 颠覆性结果写了 memory？
- [ ] ⭐ 分析**涉及 time-since-X**（bind_age / tenure / days_since_signup）时，有没有检查**退出事件分布**（unbind / churn / uninstall 等）？没检查 → **不得**下"时间效应"结论（见 [survivorship-bias-and-cohort-drift.md](references/survivorship-bias-and-cohort-drift.md)）
- [ ] ⭐ **本轮推翻了 `04-findings` 的旧结论**？有的话 `ARCHIVE.md` 是否记了 "原 X → 修订 Y → round N"？`03-analysis/0N-*.md` 旧 round 证据**没被改动**？（见 [ssot-and-glossary-discipline.md §3](references/ssot-and-glossary-discipline.md#3-每轮-round-结束时的-ssotglossary-自检)）
- [ ] ⭐ **本轮引入新变量/字段/参数/业务分段**？`glossary.md` 是否**同步更新**，定义写了**精确计算式**（SQL / 公式 / filter 链，不是自然语言）？
- [ ] ⭐ 本轮用了和前面 round **同名但可能不同口径**的术语？有没有 rename 避免歧义？
- [ ] ⭐⭐ 本轮 SQL / 结论里**每个阈值**（cohort 准入 / size 下限 / 设备离群判定）是否来自**分布统计**（median / MAD / IQR / percentile / Wilson CI）而不是拍脑袋？裸字面量（`>= 0.5` / `>= 50` / `< 0.2`）一律替换为参数化 + 来源方法说明；k / tier 走 fact rule，不进 SQL。违反即触发 [adaptive-thresholds-discipline.md](references/adaptive-thresholds-discipline.md) 自检。
- [ ] 决定了下一轮 focus 还是退出/固化？

有任何 NO，别进下一轮。

---

## Examples

### Bad Example

```
用户："帮我分析一下 KF226 的异常率，看看是不是真的比其他机型高很多"

Agent: 直接跑
  SELECT model_no, COUNT(*) abn_rate FROM device_status
  WHERE charging_mode != 3 GROUP BY 1 ORDER BY 2 DESC
# ❌ 没写假设、没查 DataHub 确认字段、没评估 NULL 率、
#    跑完直接下结论 "KF226 异常率最高"
# ❌ model_no 可能根本不含 KF226（dim 表未注册）→ 被归到 NULL 组
#    此时写 "KF226 归因不成立" 是把 ⏸ 当 ❌ 的典型武断
```

### Good Example

```
用户："帮我分析一下 KF226 的异常率"

Agent:
  Round 1
  - 假设: H1 "KF226 异常率显著高于同类 cohort"，可证伪条件 = KF226 与 solar-cohort 均值差异 < 1σ
  - Find Data: invoke addx:datahub-schema-search 确认 dim_device_base_df.model_no 覆盖情况
  - Assess Data: sanity-check → 发现 dim 中 KF226 匹配 0 行（识别缺失 ⏸）
  - Method Pick: 3 路比较（cohort 异常 / z-score / 分层） → cohort 最合适，z-score 备选
  - Analyze: 按 cohort 筛出 1,549 可疑设备，555 用户跨 14 个 solar-required model
  - Consolidate: 03-analysis/01-cohort-baseline.md 写明
  - Revise: H1 = ⏸ 暂无法验证（KF226 model_no 识别缺失，不是证伪）
  - Memory: 沉淀 project_kf226_model_dim_unregistered.md 避免下次重踩
  - Decide: 下轮 Round 2 做 pattern/segment EDA 换角度
```

---

## Flexibility

本 skill 是"flexible"类 — **纪律必须守**（假设先行 / 方法选型 / 措辞三态 / memory 沉淀），**文档
步骤可裁剪**（单轮 + 简化 doc 也可）。但以下绝不妥协：

- 假设先行
- 方法选型文档化
- 措辞三态
- Memory 沉淀颠覆发现

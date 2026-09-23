# Case Study: KF226 Solar Charging Anomaly Investigation

> 完整 worked example，展示 data-driven-investigation skill 的 3 轮 loop 在真实 session 里如何展开。日期 2026-04-19，项目 `services/customer-care#75`。

## 问题起源

客服反馈：kiwibit KF226 摄像机用户差评集中在"太阳能板坏了"。PM 猜测根因是**插头胶塞过紧**导致用户未插到位。需要**数据驱动**验证并设计 SmartPopup 拦截。

## 初始假设（Step 1）

```
原假设：KF226 太阳能充电不良是因为胶塞插不紧。受影响用户群可通过数据识别。
证据来源：客服工单 + PM 直觉
可证伪条件：
  - 如 KF226 异常率接近 / 低于同级 cohort → 推翻胶塞假设或 KF226 归因
  - 如异常用户分布均匀无模式 → 假设需细化
```

## Round 1 — Baseline Cohort Validation

### Find Data (Step 3)
通过 `addx:datahub-schema-search` 识别到关键表：
- `device.dwd_device_status_hi` — 含 `charging_mode=3`（太阳能充电）
- `device.dim_device_base_df` — 含 `original_model_no` / `model_no`
- `bind.dwd_bind_successful_details_di` — 含 `user_id` / `tenant_id`

### Assess Data (Step 4) ⚠️ 发现 4 个重要数据缺口

通过 sanity-check SQL 发现：

| 字段 | 观察 | 影响 |
|---|---|---|
| `solar_sn` (实装太阳能板 SN) | 99.999% 空 (25/2.1M 非空) | Panel 级批次质检 **不可行** |
| `chg_v_event` / `chg_c_event` | 填充率 0.28% / ≈0% | 物理金标准信号 **不可用** |
| `ads_us_pir.is_solar` | 55% NULL | 权威"是否配 solar 板" 字段**未 backfill** |
| KF226 `model_no` | 在 dim 中 0 匹配 | **识别缺失**，无法精准过滤 |

→ 这些缺口早于任何 analysis 跑出来就被暴露。

### Method Pick (Step 5)
**3 路找方法**：
- **A 目录**：cohort 异常检测（适用群体有基线）/ Absolute threshold / z-score
- **B 独立思考**：医学亚组分析 / 质检批次检测 → 都是 cohort 思路
- **C WebSearch**：`"anomaly detection iot device"` → Isolation Forest / cohort relative

**选 Cohort Anomaly Detection**（见 [cohort-anomaly-detection.md](cohort-anomaly-detection.md)）。
**备选**：若某 model 样本 < 50，切 z-score + 人工 review。
**局限**：cohort 内异质时假设不成立。

### Analyze (Step 6)
并行派 agent 跑 5 个问题（Q1-Q5），使用 `superpowers:dispatching-parallel-agents` + `addx:superset` SQL Lab。

### Consolidate (Step 7)
写入 `03-analysis/01-cohort-baseline.md`：
- 命中 1,549 可疑设备
- 分布在 14 个 solar-required model，555 用户

### Revise & Decide (Step 8)
- ✅ "cohort 方法能识别疑似异常个体" 被验证
- ⏸ 胶塞假设还未直接验证（需行为特征）
- ⏸ KF226 归因仍无法直接验证（model_no 识别缺失）

Memory 沉淀：`project_solar_sn_empty.md`、`project_kf226_dim_missing.md`

## Round 2 — Pattern & Segment EDA

### State
上轮得到 1,549 可疑设备。本轮问：它们有什么**共同 pattern**？

新假设：
- H1: 异常设备有特定时序模式（always-bad vs recent-drop）
- H2: 异常率随某些维度显著变化（geo / firmware / bind_age）
- H3: 多信号源交叉验证能加强诊断

### Questions (Step 2)
13 个 EDA 问题分 4 phase：
- P1 信号交叉验证 (Q1-Q3)
- P2 异常模式 (Q4-Q7)
- P3 细分维度 (Q8-Q10)
- P4 行为辅助 (Q11-Q13)

### Find + Assess Data
多表 join 发现 PIR 原始表 2.4 亿事件/天，直接扫描撞 Athena bytes 限额。

### Method Pick
- Q1 (信号相关性)：Pearson correlation
- Q4 (电量日内轨迹)：时间 × 组分组均值对比
- Q10 (绑定时长 vs 异常率)：分桶比例对比
- Q11-13：计数比例

### Analyze
派 7 并行 agent，5 成功 + 4 被 bytes 限额卡住（Q7/Q11/Q12/Q13）。

### Consolidate
写 `03-analysis/02-patterns.md`, `03-analysis/03-segments.md`, `03-analysis/04-behavioral.md`（标记 deferred 的问题）

### Revise
- ✅ Q10: 新装 <7d 异常率 29.36%，是稳态的 2.4× → **强验证胶塞假设**
- ✅ Q8: 固件 1.14.10 异常高
- ✅ Q9: 北方 state 冬末异常率 9.73%（季节信号）
- ⏸ Q11-13: **不是证伪**，是数据 infra 限制（PIR 表 bytes）

### Decide
- 🚨 **升级基础设施**：建 dbt dws 中间层把 PIR 表按天聚合 → 解除 bytes 限制
- 下轮在新 dws 上重跑 Q11-13

→ invoke `addx:gitlab-mr` 推 MR !3037 / !3038（DATA/dbt 新建 `dws_device_pir_solar_daily_di` / `dws_device_soc_wakeup_daily_di`）

## Round 2.5 — Infrastructure Upgrade

MR !3037 合入 staging (10:41) + Jenkins staging dbt run 成功 + MR !3038 合入 master (11:07)。prod 表 709K 行落库。

无新 analysis，纯 infra 上线。

## Round 3 — Deep-Dive with is_solar + Verification

### State
新增 dws 表使 PIR 分析可行。重跑 Q11-13 + 追加 5 个深挖问题（Q14-Q20）。

### Method Pick
- Q14: 用 `prod_camera.ads_us_pir.is_solar` 作 ground truth 替代 cohort heuristic
- Q15: severity stratification（battery_min 分桶）
- Q16: firmware deep-dive（排除 bind_age 混淆）
- Q19: 时序分型（A_always_bad vs B_recent_drop）
- Q20: cross-tenant anomaly rate 对比

### Analyze
并行 5 agent + 之前失败的 Q11-Q13 重跑（用 dws 中间层）。

### Revise & Decide — ⚠️ 重要措辞修订

Q20 初步结论："KF226 归因**不成立**，dzeesHome 45.2% 才是 outlier"。

User 提醒 → 纪律校准：**KF226 `model_no` 未确认** + `is_solar` 55% NULL → kiwibit 的 555 异常用户被归到 NULL 组**只是识别缺失**，不是证伪。

修改措辞：

| 原武断写法 | 纪律写法 |
|---|---|
| "KF226 归因不成立" | "⏸ KF226 归因**暂无法验证**（不是证伪）" |
| "kiwibit 未上榜" | "kiwibit 被归到 NULL 组，**只是识别缺失**" |
| "dzeesHome 才是真 outlier" | "可识别池中 dzeesHome 45.2% 最高" |

→ 所有相关 doc（README / 01 / 03-analysis / 04）同步更新。Memory 沉淀 `project_solar_popup_cross_tenant.md` 记录这次纪律校准。

### 最终三态

| 假设 | 状态 | 依据 |
|---|---|---|
| 新装期（<7d）异常率高 | ✅ | Q10: 29.36% (2.4×) |
| A_always_bad 占多数 | ✅ | Q19: 67% |
| 纬度/季节影响异常率 | ✅ | Q9: AK 15.6% outlier |
| KF226 是 top 异常 tenant | ⏸ | **暂无法验证**（识别缺失）|
| 固件 1.14.10 全局回归 | ⚠️ **半真** | Q16: 仅 CG623/CQ121 真回归，CG625 反而更好 |

## 固化产出

- Superset dataset 2798 + 2799 + 2801
- Superset dashboard 475 + 476
- 13 文件分层场景 doc（README + 01-06 + 03-analysis/01-06）
- GitLab issue #75 + dev branch + MR !120 (docs) + MR !3037/!3038 (dbt)
- 3 个 memory file

## Skill 使用总结

| Loop step | 用到的 skill |
|---|---|
| Find Data | `addx:datahub-schema-search` × 10+ |
| Assess Data | `addx:superset` (SQL Lab) × 20+ |
| Method Pick | WebSearch + `addx:root-cause-analysis` 参考 |
| Analyze | `superpowers:dispatching-parallel-agents` × 5+ 轮（每轮 5-7 agent）+ `addx:superset` |
| Consolidate | Write tool（docs/scenarios/smart-popup/solar-panel/）|
| Revise | `auto-memory` × 3 |
| Decide | `addx:gitlab-issue-sop`（issue #75）+ `addx:gitlab-mr`（MR !3037/!3038/!120/!3039）|

## 本 case 的教训（已沉淀为 skill 规范）

1. **数据缺口是第一影响因素** → Find Data + Assess Data 拆成独立 step
2. **措辞纪律 ⏸ vs ❌ 极重要** → 纳入 SKILL.md 核心 anti-pattern
3. **方法选型走 3 路** → 不要只查 catalog
4. **基础设施瓶颈时要升级再循环** → Loop Pattern Step 8 明确"升级基础设施 → 入环"
5. **每 Round 一个 analysis file** → 避免一条 Monster SQL

这些教训就是**本 skill 和 baseline EDA 的差异化价值**。

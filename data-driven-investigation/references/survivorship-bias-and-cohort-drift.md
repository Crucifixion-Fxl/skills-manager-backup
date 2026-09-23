# Survivorship Bias & Cohort Drift —— 当你在时间轴上看到单调趋势

> 这是 data-driven-investigation 最容易踩的一类坑。列在这里作为"看到时间维度 × 成功/失败率单调关系"时的**强制 checklist**。

## 症状：你以为你看到了时间效应，其实看到的是"谁留下来了"

经典表现：

- cohort 按 `bind_age / tenure / session_count` 等时间变量分桶
- 在某指标（失败率、异常率、流失率、错误率）上看到**单调递减**（或递增）
- 第一反应："新装期高、随时间稳定" / "老用户错误率低" / "重度用户留存率高"
- **实际可能的真相**：前面的桶里**失败/异常的个体被选择性移除了**（退货 / 注销 / 卸载 / 转移 / 死亡）

## 这不是"cohort 内异质"，是"cohort 成员资格本身被前置事件过滤"

`cohort-anomaly-detection.md` 讲的是"同 cohort 内异质需要分层"。本问题更深一层：

- 前置层：**哪些个体能进 cohort** 本身就被前置事件（退货/注销/流失）筛选过
- `>=14d cohort` 里的设备 ≠ "所有设备熬了 14 天"；而是 "**没被退货/注销**的设备"
- 差异极大的群体会被这层筛子漏掉；你看到的"稳态"其实是 survivor pool

## 强制自检 —— 看到单调趋势时先问这 4 问

**只要你的分析涉及 time-since-event（bind_age / tenure / days_since_signup / session 数），必答以下 4 问。答不上来或未验证，不得下"时间效应"的结论。**

1. **退出事件是什么？** 一个个体从 cohort 里消失的具体事件名（unbind / churn / uninstall / return / 账号注销）
2. **退出率在不同 buckets 里均匀吗？** 如果前段桶退出率显著高（尤其高在"异常"亚组），有存活偏差嫌疑
3. **如果把退出的个体"拖回来"，趋势还在吗？** 把 unbind 前的最后状态 append 回 cohort，重画曲线
4. **你的结论是描述"个体时间轨迹"还是"cohort 横截面差异"？** 两者完全不同，前者需要 within-subject longitudinal，后者是 cross-sectional
   - ❌ "设备随时间稳定下来" = 个体轨迹叙事（需 longitudinal 证据）
   - ✅ "14d 以上 cohort 的异常率低于 7d 以下 cohort" = 横截面事实（不蕴含时间因果）

## 可用方法（接 method-catalog）

| 目的 | 方法 |
|---|---|
| 量化存活偏差规模 | 异常组 30d unbind 率 vs 健康组 30d unbind 率（>=2× 即显著偏差） |
| 把退出事件还原到 cohort | Kaplan-Meier 估计 + 把 unbind 当 event、is_abnormal 当 covariate |
| 看 hazard ratio | Cox 比例风险模型（是否异常 → unbind 的 HR） |
| 时间到事件窗口 | `days_to_unbind` 分位数 + 桶化 |
| 替代"时间单调"叙事 | **固定首绑 cohort**（fixed-entry cohort），对同一批人跟踪 T 天里状态变化，不再混入新进入者 |

## 业务含义的重构（非常重要）

如果存活偏差被验证 —— 你的**问题本身可能需要重构**：

- 旧叙事："新装期失败率高 → 需要在新装 ≥14d 后做干预" ❌ 这是 survivor cohort 视角
- 新叙事："**新装期失败者会在 N 天内退货/流失，需要在退货前窗口干预**" ✅ 存活偏差视角

这从"找异常 → 推 RMA"变成"**赶在流失前挽救**"。前者优化用户体验，后者直接影响营收（每拦截一次退货 = 保留一个客户 + 节省硬件/物流成本）。**干预的紧迫性、文案、SLA 全都变**。

## Anti-pattern 清单（都是看到单调趋势时的典型错误）

| 错误 | 正确做法 |
|---|---|
| "新装 <7d 异常率高，新装是黄金干预窗口" | 先算 <7d 异常设备的 30d 退货率，对比健康组。如果异常组退货率显著高 → **新装 <7d 看到的不是"容易修复"，是"容易流失"** |
| "老用户错误率低，系统随时间稳定" | 先算新 vs 老的流失率差异。若新用户流失率高，**老用户 cohort 是被错误漏斗筛剩下的胜者** |
| "A/B 实验组留存率持平" | 先看实验组 vs 对照组的退出率是否均衡。如果实验组退出率低（留下更多"边际用户"），留存率数字可能是 **下调偏差**（retention metric 看起来差是因为筛子松了） |
| "用户随时间 engagement 上升" | 大概率是 low-engagement 用户流失，剩下的是高 engagement survivor。**按固定 cohort 追踪 engagement 轨迹**才是真相 |

## 与 data-driven-investigation Loop 的关系

- **Step 2 Questions**：涉及 time-since-X 变量时，强制列出"退出事件是什么"作为一个 assess 问题
- **Step 4 Assess Data**：sanity check 加一项"该 cohort 的退出率分布"
- **Step 5 Method Pick**：如果数据里有明显退出事件（unbind/churn/uninstall），**3 路找方法时必须考虑 Survival Analysis 作为候选**，不能只用 cross-sectional cohort rate
- **Step 6 Analyze**：对单调趋势发现，要求用本文 "4 问自检" 作为发布前 gate
- **Step 7 Consolidate**：描述时严格分清"**时间轨迹**"与"**横截面差异**"

## 真实 case（data-driven-investigation 本身踩过的坑）

services/customer-care#75 Round 4 Q26：

1. 报告 solar_required cohort 按 bind_age 分桶 abnormal_rate：
   ```
   <7d 40.2%  →  7-14d 22.6%  →  14-30d 18.6%  →  30-90d 14.8%  →  >=90d 13.5%
   ```
   单调递减，差距 26.7pp
2. 初步结论："新装是黄金干预窗口" —— 跑了 6 个 Round EDA 都没意识到问题
3. **User 指出存活偏差假说**：失败的 <7d 设备会被退货，熬不到 >=14d
4. 补做 Q41/Q42/Q43：量化 30d unbind 率、退货窗口期、tenant×model 转化率
5. 业务目标从"找异常推 RMA"重构为"**赶在退货前拦截新装失败**"

这就是为什么这份 reference 存在 —— **即使是看起来最清楚的单调趋势，没做存活偏差检验都不敢说是"时间效应"。**

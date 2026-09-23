# Loop Pattern — 详细 8 步

> 本文档给 SKILL.md 的"Loop Pattern"章节提供完整模板、反面例子和 state 文件格式。

## 8 步全景图

```
Round N
 ├── 1. State              承接 + 假设
 ├── 2. Questions          问题清单
 ├── 3. 🔍 Find Data       invoke datahub-schema-search
 ├── 4. 🧪 Assess Data     跑 sanity-check
 ├── 5. 🧭 Method Pick     3 路找方法
 ├── 6. 🔬 Analyze         invoke superset + dispatching-parallel-agents
 ├── 7. Consolidate        写 03-analysis/0N-*.md
 └── 8. Revise & Decide    三态转移 + memory + 决定下一轮
```

---

## Step 1 — State（承接上轮 + 本轮假设）

**目的**：让 Round 之间无缝衔接，每轮开头都能独立看懂上下文。

**模板**：

```markdown
## Round <N> State

### 已知事实（来自 Round N-1 的 ✅ 结论）
- [列表形式，每条 1 行]

### 上轮未解 gap
- [列表形式，每条 1 行]

### 本轮主要假设
**原假设**：<一句话>
**证据来源**：<业务反馈 / 客服工单 / 数据趋势 / 直觉>
**可证伪条件**：<什么数据证据会让我判定此假设为假>

### 本轮覆盖范围
[什么 in scope，什么 out of scope]
```

**反例**：

❌ "上轮结论还算清楚，直接进本轮" —— 没有显式 state 承接就开始容易走偏
❌ "假设：KF226 可能有问题" —— 没有可证伪条件，模糊无法验证
✅ "假设：KF226 的 30 天 solar_charging_rate 显著低于同型号 cohort 均值 20pp+。可证伪条件：KF226 池的 solar_rate > cohort mean - 5pp。"

---

## Step 2 — Questions（问题清单）

**目的**：把本轮要验证/证伪的 5-10 个具体小问题列出来，每题可独立跑 SQL + 独立有结论。

**每题必须包含**：

```
Q<N.M>: <一句话问题描述>
  想证实什么: <如果数据显示 X，则假设被支持>
  想证伪什么: <如果数据显示 Y，则假设被推翻/修订>
  依赖数据: <大致知道是哪张表/字段 — 真实验证在 Step 3>
```

**典型问题类型**：

- 分布类：`分布 by X`，找主导值 / outlier
- 比较类：`A vs B`，两个 cohort / 时段 / 维度对比
- 关联类：`X 与 Y 的相关性`
- 时序类：`近 N 天 / N 周趋势`
- 交叉类：`X × Y 矩阵`，找共同出现的异常

**问题数量建议**：
- 第一轮：3-5 题（快速 baseline）
- 中间轮：5-10 题（并行派 agent）
- 深挖轮：2-3 题（聚焦剩余 gap）

**反例**：

❌ "查一下充电相关数据" —— 不是问题，是散步
❌ "XXX 表有什么" —— 是 Step 3 的事，不是本轮问题
✅ "Q2.3: 同 model cohort 下，KF226 设备 30 天平均 solar_rate 是否低于同 cohort 均值 20pp+？"

---

## Step 3 — 🔍 Find Data

**强制**：invoke `addx:datahub-schema-search`。

**目的**：
1. 验证每题引用的表 / 字段真实存在
2. 记录字段类型 / 分区策略 / 预估数据量
3. 提前暴露"字段不存在"类 gap

**模板**：

```markdown
### Q<N.M> 数据源

| 表 | 字段 | 类型 | 填充率（从 schema 描述或历史 EDA） |
|---|---|---|---|
| analytics.dwd_event_smart_device_pir_trigger_hi | is_solar_event | boolean | 高（100% 填充） |
| device.dim_device_base_df | original_model_no | varchar | 高 |
| ... | | | |

### 分区策略
- `analytics.dwd_event_smart_device_pir_trigger_hi`: 按 date (day) 分区
- `device.dwd_device_status_hi`: 按 dt (day) 分区 + bucket(48, serial_number)

### 查询 bytes 预估
- 若按 date 过滤 7 天：~N 亿事件 → 可能超 quota
- 若只按目标 SN 过滤但不 partition prune：可能超 quota
```

**反例**：

❌ `SELECT col FROM table WHERE ...`  **直接写 SQL 而没查表** —— 最常见误判源
❌ "应该是 `cap_event` 字段吧" —— 凭记忆没验证
✅ 跑 `/api/search?query=solar charging device` → 拿回真实 schema

---

## Step 4 — 🧪 Assess Data

**强制**：invoke `addx:superset` (SQL Lab) 跑 sanity-check。

**目的**：在真正分析前确认数据**实际可用**，不是理论可用。

**必跑的 sanity-check 类别**：

```sql
-- 1. 覆盖率
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT key_column) AS distinct_entities,
       COUNT(DISTINCT dt) AS distinct_days
FROM <table>
WHERE <partition filter>

-- 2. NULL / 空字符串率
SELECT
  SUM(CASE WHEN col IS NULL THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS null_rate,
  SUM(CASE WHEN col = '' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS empty_str_rate,
  SUM(CASE WHEN col IS NOT NULL AND col != '' THEN 1 ELSE 0 END) AS valid_count
FROM <table>

-- 3. 值分布 (distinct + top 10)
SELECT col, COUNT(*) AS cnt
FROM <table> GROUP BY 1 ORDER BY 2 DESC LIMIT 10

-- 4. 样本量可用性（for cohort-based methods）
SELECT cohort_dim, COUNT(DISTINCT entity) AS cohort_size
FROM <table> GROUP BY 1 HAVING cohort_size >= <threshold>
```

**数据缺口决策**：

| 观察到 | 决定 |
|---|---|
| 字段 NULL/空 > 50% | ⛔ 该字段不可作主信号；在 04-findings 写"数据缺口" |
| 分区 prune 不生效 → bytes 超限 | ⛔ 切换方法 or 建 dbt 中间层 |
| cohort_size < 最小阈值 | ⛔ 换方法（绝对阈值 / z-score 代替 cohort）|
| 历史覆盖不够 | ⛔ 缩短窗口 or 等 backfill |
| 数据充分 | ✅ 继续 Step 5 |

---

## Step 5 — 🧭 Method Pick

**走 3 路**（见 SKILL.md 主体 + [method-selection-guide.md](method-selection-guide.md)）：

- A. 查 [method-catalog.md](method-catalog.md)
- B. 独立思考 5 问
- C. `WebSearch`

**产出**（写进 04-findings.md 或单独 method-pick 段）：

```markdown
## 方法选型

**选择**: <主方法>
**理由**:
  - 数据特征 <X> 匹配该方法前提（eg. 样本 >= threshold）
  - Catalog + WebSearch + 独立思考，候选 A/B/C，选 A 因为 …

**备选方案**:
  - B: 当 <条件 X> 时切换
  - C: 当 <条件 Y> 时切换

**已知局限**:
  - <≥ 1 条>

**参考**:
  - [链接]
```

---

## Step 6 — 🔬 Analyze

**强制**：
- invoke `addx:superset` (SQL Lab) 跑 SQL
- invoke `superpowers:dispatching-parallel-agents` 派多 agent 并行（问题数 ≥ 2）

**原则**：
- **每题一个独立 SQL**（不合并成 Monster SQL）
- 每 agent 只负责 1-2 道题，输出格式统一
- SQL 骨架在 Step 5 的方法选型里准备好，Step 6 只是执行

**agent prompt 模板**（本轮每道题派一个）：

```
你是 EDA 子 agent，用以下工具跑 SQL 分析问题：

工具: /tmp/sp_call_post.sh '/api/v1/sqllab/execute/' '<body>'
Body: {"database_id":<id>,"schema":"<schema>","sql":"...","runAsync":false,"queryLimit":N,"expand_data":true}

问题 Q<N.M>: <描述>
数据源 (Step 3 已验证): <表 + 字段>
方法 (Step 5 已选): <cohort 异常 / z-score / ...>
前置 SQL 骨架: <从 Step 5 拿>

输出到 /tmp/round-<N>-q<M>-result.md，格式：
  ## Q<N.M> 结果
  [数据表 + 2-3 findings + 对下一阶段启示]
  字数 < 400

查询失败重试 ≤ 3 次（缩窗口/改参数）。返回总结 + wc -c。
```

---

## Step 7 — Consolidate

**目的**：Round N 的所有结果按小节写入 `03-analysis/0N-<name>.md`。

**文件结构**：

```markdown
# Round <N>: <主题> (<date>)

## 本轮假设
[从 Step 1 复制]

## 本轮方法选型
[从 Step 5 复制]

## 各问题结果

### Q<N.1> <问题>
<数据 + findings + 启示>

### Q<N.2> <问题>
...

## 本轮综合结论
[✅/❌/⏸ 三态]

## 发现的新 gap
[下轮 Round N+1 的输入]
```

**反例**：

❌ 把 7 题结果贴到聊天里，最后一口气写文档 → 会漏细节
✅ 每题跑完立即写进 `03-analysis/0N-*.md`

---

## Step 8 — Revise & Decide

### 假设状态转移

每个假设必须落入 ✅/❌/⏸（详见 [hypothesis-discipline.md](hypothesis-discipline.md)）。

### Memory 沉淀

**强制 invoke `auto-memory`**，当结论：
- 推翻原假设
- 发现系统级事实（工具/数据限制）
- 与团队已有认知相悖

Memory file 模板：

```markdown
---
name: <short-title>
description: <一句话，会在 MEMORY.md 索引显示>
type: project
---

<事实/结论>

**Why**: <why this matters / background>

**How to apply**: <下次遇到类似情况怎么办>
```

### 决定下一轮

**3 种可能出口**：

| 出口 | 条件 | 动作 |
|---|---|---|
| 下一轮 | 还有未解 gap 且可继续 | 回到 Step 1（本轮结论 → 下轮 state）|
| 升级基础设施 | 撞工具/数据边界（bytes 超限 / 字段不存在）| 先 invoke `addx:gitlab-mr` 建 dbt 中间层 or 发 issue 要 backfill，完成后再入循环 |
| 固化退出 | 假设定型（✅ 或稳定 ❌）+ 业务方认可 | 产出 05-algorithm.md / 06-deployment.md，invoke `addx:gitlab-issue-sop` 建 downstream issue |

---

## 附：Round state 文件格式（/tmp/round-<N>-state.json）

```json
{
  "round_n": 2,
  "started_at": "2026-04-19T10:00:00Z",
  "hypothesis": "<本轮主要假设>",
  "falsifiability": "<可证伪条件>",
  "questions": [
    {"id": "Q2.1", "question": "...", "confirm_if": "...", "refute_if": "...", "data_source": {...}, "method": "cohort_anomaly", "status": "✅|❌|⏸|pending"}
  ],
  "data_gaps_found": [...],
  "revisions": [
    {"hypothesis": "...", "new_state": "⏸", "reason": "..."}
  ],
  "memory_writes": ["project_xxx.md"],
  "next_decision": "next_round|infra|exit",
  "next_focus": "..."
}
```

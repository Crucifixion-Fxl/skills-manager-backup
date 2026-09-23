# Parallel Dispatch Guide — 如何在 Round 内并行派 agent

> Step 6 Analyze 的执行指南。核心是接 `superpowers:dispatching-parallel-agents`。

## 何时并行

| 问题数 | 策略 |
|---|---|
| 1 题 | 直接跑，无需并行 |
| 2-3 题 | 并行（启动成本小于收益）|
| 4-10 题 | **必须并行**（串行太慢）|
| > 10 题 | 分两批并行 or 收敛问题数 |

## 独立性前提

并行的前提是**问题之间独立**。不独立的情况：
- Q2 的 SQL 依赖 Q1 的输出（子查询关系）→ 不能并行，合成一题 or 用中间结果
- 多题共享同一批候选 SN / 实体 → 并行是对的，但应先跑"生成候选"一题，再并行跑"对候选做 X"
- 对同一张大表的大范围扫描 → Athena bytes 串行排队，并行没加速

## Agent 提示词模板

每题派一个 sub-agent。**每个 agent 的 prompt 是自包含的**（子 agent 没有主会话上下文）：

```text
你是 EDA 子 agent。用下列工具跑 SQL 分析问题，结果写到指定文件。

## 工具
/tmp/sp_call_post.sh '/api/v1/sqllab/execute/' '<body>'
Body 格式: {"database_id":<id>,"schema":"<schema>","sql":"...","runAsync":false,"queryLimit":N,"expand_data":true}

## 问题 Q<N.M>
<完整问题描述 + 想证实/证伪什么>

## 数据源（Step 3 已验证）
- <表名>: <字段清单 + 分区策略>

## 方法（Step 5 已选）
- 主方法: <X>
- SQL 骨架（已准备，子 agent 直接套）:
```sql
<SQL 模板>
```
- 备选（如果主方法因 <条件> 失败）: <B 方法>

## 输出
写到 /tmp/round-<N>-q<M>-result.md，格式：
  ## Q<N.M> 结果
  ### 数据
  <表格 / 数值>
  ### Findings (2-3 条)
  ### 对下一阶段启示 (1 段)
字数 < 400 中文字。

## 约束
- 查询失败最多重试 3 次（缩窗口/改条件）
- 如果撞 bytes 限额 → 切备选方法，记录原因
- **不要**改其他文件
- **不要**嵌套派子 agent

返回 < 100 字总结 + wc -c 文件大小。
```

## 对共享前置条件的处理

如果多题依赖同一个前置（比如"先找出 1549 个 suspect 设备，然后对它们做各种分析"），分两阶段：

**阶段 1（串行）**：跑前置，产出候选列表到共享文件 `/tmp/round-<N>-candidates.csv`

**阶段 2（并行）**：每个 sub-agent prompt 里引用 `/tmp/round-<N>-candidates.csv`，独立完成自己的分析

## 失败处理

每个 sub-agent 回报后：

| 状态 | 处理 |
|---|---|
| ✅ 完成 | append 到 03-analysis/0N-*.md |
| ❌ SQL 失败（bytes / 字段不存在） | 视为 "数据缺口" ⏸，记录到 04-findings 缺口章节 |
| ❌ agent 超时 / 中断 | 重派一次，仍失败则标 ⏸ + gap |
| ⚠️ agent 自改主方法 | 记录切换原因 → 下轮更新 Step 5 方法选型 |

## 主会话（编排者）的职责

派完 agents 之后，**不要等着轮询**。主会话可做：
1. 预先起草 `04-findings.md` 骨架（先填 assumptions 结论待填）
2. 准备下轮 Step 1 的 state draft（基于本轮假设）
3. 若发现一题特别关键，手动写 SQL 备用（作为 agent 失败时的 fallback）
4. 接到 agent 回报通知时逐题 consolidate

## 反例

❌ **嵌套并行**：sub-agent 又派 sub-agent —— 2 层以上不可控，一律平铺到主会话派
❌ **共享可变状态**：多 agent 同时写同一文件 → race condition
❌ **过度并行**：同时派 > 10 agent → Athena workgroup 限额 / rate limit
❌ **不可重试的 prompt**：prompt 里写"一次跑通"→ 真的网络抖动就废了

## 和其他 skill 的关系

- 本 guide 使用 `superpowers:dispatching-parallel-agents` 作为底层机制
- SQL 执行强制 `addx:superset`
- 数据发现强制 `addx:datahub-schema-search`

## 典型 Round 时间预算

- Step 1-5（假设/问题/数据/方法）：20-40 分钟
- Step 6 并行 dispatch：10-40 分钟（取决于查询复杂度）
- Step 7-8 consolidate + revise：15-30 分钟
- **一个 Round 总计 ~1-2 小时**（非 data-heavy 时可更快）

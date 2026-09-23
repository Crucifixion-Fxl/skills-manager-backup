# Hypothesis Discipline — 措辞纪律与三态

> 本文档给 SKILL.md 的"Evidence Discipline"章节提供详细的措辞规范、常见陷阱和 memory 沉淀模板。

## 三态：✅ / ❌ / ⏸

一切关于假设的结论必须落入以下三态之一：

| 态 | 图标 | 名字 | 充要条件 | 允许的措辞 |
|---|---|---|---|---|
| 证实 | ✅ | validated | 独立信号源上交叉验证（≥2 源一致）+ 样本规模充分 | "已验证 / 数据支持" |
| 证伪 | ❌ | refuted | 明确反证 + **确认数据覆盖充分**（排除识别缺失） | "被证伪 / 不成立 / 推翻" |
| 暂无法验证 | ⏸ | unverifiable | 识别缺失 / 数据未 backfill / 样本不足 | "暂无法验证 / 识别缺失 / 需补数据 / 待 X 后重跑" |

## 🚫 最致命错误：把 ⏸ 说成 ❌

**场景示例**：

假设"新推出的产品 X 异常率高于平均"。数据查询发现产品 X 的用户归类到 NULL 组（因为 `model_no` 未在 dim 注册）。

❌ **武断措辞**（错的）：
- "X 产品异常率不高于平均，假设不成立"
- "X 归因被证伪"
- "X 产品没有问题"

✅ **纪律措辞**（对的）：
- "X 的 `model_no` 在 dim 未注册（识别缺失），现有数据**无法验证**假设"
- "X 被归到 NULL 组（17.5%），**不是因为 X 没问题，而是因为识别缺失**"
- "等 PM 确认 `model_no` 字串 + dim 补齐后重跑此判定"

**核心差异**：
- ❌ 要求"看过真实数据 + 看清了"
- ⏸ 承认"没看清 / 没看到"
- 混用 ⏸ 和 ❌ = 掩盖数据缺口 = 长期偏差

## ⏸ vs ❌ 的判定树

```
发现"我没找到支持假设的证据"时：
├── 数据里明确相反证据？
│   ├── 是 → 检查：反证的数据覆盖充分吗？
│   │   ├── 是 → ❌ 证伪
│   │   └── 否 → ⏸ 暂无法验证
│   └── 否 → 进入下一问
├── 关键字段 NULL/空比例 > 30%？
│   └── ⏸ 识别缺失
├── 关键实体在 dim 里缺失？
│   └── ⏸ 识别缺失
├── 样本量 < 方法的最小要求？
│   └── ⏸ 样本不足
├── 数据时间窗口不够覆盖假设期？
│   └── ⏸ 窗口不足
└── 以上都不符合 + 数据充分 + 无支持假设的证据
    └── ❌ 证伪
```

## 常见措辞陷阱

| 陷阱短语 | 问题 | 改为 |
|---|---|---|
| "XX 没有问题" | 武断 | "现有数据无法显示 XX 有问题"（如果只是 ⏸）|
| "XX 不是 top" | 武断 | "可识别池中 XX 未进入 top"（⏸）|
| "XX 归因不成立" | 混淆 | "XX 归因暂无法验证"（⏸）|
| "未发现" | 模糊 | "数据覆盖范围内未发现"（⏸）|
| "XX 已推翻" | 过强 | "XX 暂时没有数据支持" 或 "XX 在此样本中不成立"（⏸ 或限制性 ❌）|
| "看起来 XX" | 软弱 | "在 p = <...>, n = <...> 下，XX 的证据为 <具体数值>"（量化）|

## Memory 沉淀时机与模板

### 必须 invoke `auto-memory` 的场景

1. **颠覆性结论**：推翻原假设 / 与团队常识相悖
2. **数据/工具缺口**：发现某字段不可用、某表分区不利、某指标口径不一致
3. **方法适用性边界**：某方法在特定场景失败（e.g., "cohort 方法需 >= 50 同类"）
4. **系统性误判源**：如"默认以为 X 字段有值，实际 99% 空"

### 不要写 memory 的情况

- 一次性 SQL 结果（放文档）
- 业务数字（放 dashboard）
- 待办（放 issue）

### Memory file 模板

```markdown
---
name: <10 字内 short-title>
description: <一句话，未来 retrieval 时会依赖这行判断相关性；要包含：主体 + 关键特性 + 适用条件>
type: project | feedback | user | reference
---

<结论 / 事实（一段话）>

**Why**: <为什么这件事重要 / 背景>

**How to apply**: <下次遇到类似情况时该怎么用这条记忆>
```

### 好例子 vs 坏例子

✅ **好的 memory**：

```markdown
---
name: analytics dim tenant_id JSON 抽取 bug
description: analytics.dim_analytics_user_info_lastest_hf.tenant_id 字段全 NULL，dbt JSON 抽取逻辑 bug；任何依赖 tenant 过滤的用户侧分析必须走 bind.dwd_bind_successful_details_di 代替
type: project
---

`analytics.dim_analytics_user_info_lastest_hf` 这张 dbt 模型里的 `tenant_id` 字段 100% 为 NULL。
原因是 dbt SQL 里的 JSON 抽取表达式有 bug（事件侧 tenant_id 在 base-schema context 里的 path
与其他字段不同）。

**Why**: 因为这是广泛被引用的 user 维度 dim，很多分析会默认它有 tenant_id 可过滤，实际会
悄悄退化成"全量用户混合"的结果，产生误判。

**How to apply**:
- 任何需要按 tenant 过滤用户的分析，不要用此 dim 的 tenant_id
- 改走 bind.dwd_bind_successful_details_di (bind event 侧 tenant_id 正常)
- 若发现其他类似"理论上有实际 NULL"的字段，写入同 memory 目录
```

❌ **坏的 memory**（避免）：

```markdown
---
name: 昨天跑了个查询
description: 查了充电异常
---

SELECT * FROM dwd_device_status_hi WHERE charging_mode = 3 LIMIT 100
```

—— 是 query 不是记忆，没 why / how to apply。

## 三态在文档中的视觉呈现

**README.md TL;DR**：

```markdown
1. ✅ 新装 <7d 异常率 29.36%（2.4×）→ 强验证"胶塞"假设
2. ⏸ KF226 归因暂无法验证（model_no 未注册）
3. ❌ 固件 1.14.10 全局回归假设不成立（仅 CG623/CQ121 真回归）
```

**04-findings.md 假设修订表**：

| 原假设 | 三态 | 依据 |
|---|---|---|
| [原] | ✅ / ❌ / ⏸ | [证据 / 缺失原因] |

**绝不允许**写 "颠覆之前假设" 这种笼统说法——必须落到具体哪个假设进入哪个三态。

## 总结：措辞纪律的 3 条黄金法则

1. **当你要说 XX 没问题 / 被证伪时，先问：是数据看清了，还是数据没覆盖到？**
2. **当你要写"颠覆"两个字时，先具体化：哪个假设进入哪个三态？**
3. **一切颠覆性发现都必须 memory —— 不写 memory 就是下次再犯同样的错**

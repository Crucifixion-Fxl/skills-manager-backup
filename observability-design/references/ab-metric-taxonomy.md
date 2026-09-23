# AB 实验指标四层分类

AB 实验的指标不是平铺一个列表，而是按"时效 × 用途"分四层。每层有明确的必要性、对比方式、消费时机。

## 四层总览

| 层 | 用途 | 时效 | 对比方式 | 必要性 |
|---|------|------|---------|-------|
| **北极星 - 近端** | 即时反馈，实验是否驱动了直接行为 | 实验当日可看 | 跨组 | ✅ 必备 |
| **北极星 - 远端** | 终极业务结果，实验是否带来真实价值 | 7-30 天后回填 | 跨组 | ✅ 必备 |
| **漏斗效率** | 诊断 treatment 组内部各步骤损耗 | 实验当日 | 组内（treatment） | ✅ 必备 |
| **交互质量** | 内容/时机质量评估，以 shown=true 为分母 | 实验当日 | 组内 | 推荐 |
| **护栏** | 防止短期收益伤长期 | 实验全程 | 跨组 | ✅ 必备 |

## 近端 vs 远端配对原则

**只看近端会掉进局部最优陷阱。**

举例：弹窗 AB 实验中 treatment 组的 CTA 点击率涨了 20%（近端北极星涨了），但 D7 留存跌了 5%（远端北极星跌了）。近端上涨是因为弹窗更显眼更有侵略性，短期驱动了点击，但长期骚扰用户导致流失。只看近端 → 判定成功 → 半年后用户流失才发现。

**规则：** 每个近端北极星必须配一个远端北极星。近端看"这次实验做了什么"，远端看"这次实验值不值得"。

| 近端（行为） | 远端（结果） | 时间差 |
|-------------|-------------|-------|
| Ticket Submission Rate | D7 Device Retention | +7 天 |
| Ticket Submission Rate | D30 Device Retention | +30 天 |
| Purchase Conversion Rate | LTV 30d | +30 天 |
| Popup CTA Click Rate | D7 App Active | +7 天 |

**实现要求：** 数仓要有 backfill 任务，每日把 D7/D30 窗口到期的记录回填留存结果。参考 `dwm_smart_popup_funnel_backfill`。

## 跨组对比 vs 组内诊断

| 指标层 | 对比方式 | 为什么 |
|--------|---------|-------|
| 北极星（近+远） | 跨组（treatment vs control） | 要判断实验"有没有带来增量" |
| 护栏 | 跨组 | 要判断实验"有没有伤害基本盘" |
| 漏斗效率 | 组内（treatment 内部每一步） | control 组通常没这些步骤，跨组没意义；组内看内部损耗 |
| 交互质量 | 组内（shown=true 为分母） | 只在展示过的样本里算，不跨 control |

**典型错误：** 把漏斗指标（Show Rate / Fatigue Block Rate）塞到跨组对比里，发现 control 组 Show Rate = 0 就判定实验显著——但 control 本来就没弹窗，这是设计使然不是实验效果。

## 每层指标的标准模板

指标定义必须有以下 5 列：

| 列 | 说明 |
|---|------|
| 指标名 | 业务侧能读懂的名字，如 "Ticket Submission Rate" |
| 公式 | SQL-like 表达式，分子/分母清晰 |
| 分母 | 明确是 scene_entry / shown=true / 其他 |
| 维度 | 按什么切片（scene_id / variation_key / date） |
| 阈值方向 | "越高越好"/"越低越好"/"偏离基线告警" |

### 北极星近端模板

```
指标：<动作> Rate
公式：COUNT(<目标动作>) / COUNT(<公共分母>)
分母：scene_entry（公共分母，所有组都上报）
维度：variation_key × date
阈值方向：越高越好
```

### 北极星远端模板

```
指标：<结果> Retention / <结果> LTV
公式：COUNT(<结果在窗口内发生>) / COUNT(<窗口到期的记录>)
分母：D7/D30 窗口已到期的 scene_entry
维度：variation_key × date
阈值方向：越高越好
实现：dwm_xxx_backfill 每日回填
```

### 漏斗效率模板

```
指标：<步骤> Pass Rate
公式：COUNT(<步骤通过>) / COUNT(<上一步通过>)
分母：上一步的通过量
维度：scene_id × variation_key（不跨组）
阈值方向：正常区间告警
```

### 交互质量模板

```
指标：<交互类型> Rate
公式：COUNT(action=<类型>) / COUNT(shown=true)
分母：shown=true（仅展示过的）
维度：scene_id × variation_key
阈值方向：按动作类型正/负
```

### 护栏模板

```
指标：<副作用> Rate
公式：COUNT(<副作用>) / COUNT(<全量分母>)
分母：scene_entry 或其他全量
维度：variation_key（跨组对比）
阈值方向：treatment 显著高于 control → 红线
```

## 实例：SmartPopup 12 指标按四层分布

来自 [customer-care observability.md §1.6](../../customer-care/docs/architecture/smart_popup/observability.md)。全部基于数仓宽表 `dwm_smart_popup_funnel_hi` 计算。

### 北极星（3 个）

| 层 | 指标 | 公式 | 时效 |
|----|------|------|-----|
| 近端 | Ticket Submission Rate | `COUNT(action='click_primary') / COUNT(*)` per variation | 即时 |
| 远端 | Device D7 Retention | `COUNT(device_active_d7=true) / COUNT(device_active_d7 IS NOT NULL)` | +7 天 |
| 远端 | Device D30 Retention | `COUNT(device_active_d30=true) / COUNT(device_active_d30 IS NOT NULL)` | +30 天 |

### 漏斗效率（3 个）

| 指标 | 公式 | 分母 |
|------|------|------|
| Condition Pass Rate | `COUNT(condition_met=true) / COUNT(*)` | scene_entry |
| Fatigue Block Rate | `COUNT(fatigue_passed=false AND condition_met=true) / COUNT(condition_met=true)` | condition 通过的 |
| Show Rate | `COUNT(shown=true) / COUNT(*)` | scene_entry |

### 交互质量（4 个）

| 指标 | 公式 |
|------|------|
| Primary CTA Rate | `COUNT(action='click_primary') / COUNT(shown=true)` |
| Secondary CTA Rate | `COUNT(action='click_secondary') / COUNT(shown=true)` |
| Dismiss Rate | `COUNT(action='dismiss') / COUNT(shown=true)` |
| Auto Dismiss Rate | `COUNT(action='auto_dismiss') / COUNT(shown=true)` |

### 护栏（1 个跨组 + 1 个系统安全护栏）

| 指标 | 类型 | 公式 | 红线 |
|------|------|------|------|
| Never Remind Rate | 业务护栏 | `COUNT(action='never_remind') / COUNT(*)` | treatment 显著高于 control → 弹窗在赶走用户 |
| 单用户 5min 弹窗次数 P99 | 系统安全护栏 | 5 分钟窗口内按 user_id 统计 shown=true 次数，取 P99 | P99 > 2 warning，P99 > 5 critical |

### 判断逻辑

**Ticket Rate (treatment) > Ticket Rate (control) AND Never Remind Rate (treatment) 未显著偏高 AND 单用户 5min P99 在正常范围 → 实验成功。**

任一不满足都应该先排查再下结论。

## 常见错误

| 反模式 | 后果 | 正解 |
|-------|------|-----|
| 只定义近端北极星 | 局部最优陷阱 | 必须配远端 |
| 用跨组对比算漏斗步骤 | control 组空值拉偏显著性 | 漏斗只在 treatment 组内算 |
| 护栏只看平均 | 少数用户被严重骚扰被稀释 | 护栏用 P99/Max/Top-N（见 [safety-guardrail-patterns.md](safety-guardrail-patterns.md)）|
| 交互质量用 scene_entry 做分母 | 未展示的样本污染分子分母 | 交互质量分母 = shown=true |
| 所有指标都按 variation 拆 | SLA 告警时 variation 切分无意义 | 业务效果 SLA 按 scene 聚合，AB 对比再按 variation 拆 |

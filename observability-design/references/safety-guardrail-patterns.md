# 系统安全护栏指标模式

业务指标好看 ≠ 用户没受伤。这章讲"系统出错保护用户"的护栏指标怎么设计。

## 为什么需要安全护栏

业务指标衡量"效果"（CTA 涨了吗、留存涨了吗），**它们会被平均值和总量稀释极端 case**。护栏衡量"伤害"：

| 场景 | 业务指标表现 | 实际情况 |
|------|-------------|---------|
| 频控代码 bug | Show Rate 平均 30%（看起来正常） | 某用户一天被弹 100 次 |
| AB 分流 bug | 实验覆盖率 80%（看起来正常） | 5% 用户同时命中 2 个实验 |
| 配置错误 | API Error Rate 0.5%（看起来正常） | 某 scene_id 100% 返回错误配置 |

**护栏规则：看的不是"大多数用户体验"，看的是"最糟糕那一小撮用户体验"。**

## 三种检测模式

### 模式 1：极值检测

**检测什么：** 单用户/单实体是否被异常对待。

**计算方式：** P99 / P999 / Max / Top-N（**绝不用平均值**）。

**为什么不用平均值：**
- 假设 10000 个用户，9999 个正常（1 次弹窗），1 个异常（50 次弹窗） → 平均 1.005 次，完全看不出异常
- P99 = 1、P999 = 50 → Top 0.1% 问题立刻暴露
- Max 在每个时间窗口内只保留最极端 case，不会被稀释

**典型指标：**

| 指标 | 实现 |
|------|------|
| 单用户 5min 内弹窗次数 P99 | `SELECT PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cnt) FROM (SELECT user_id, COUNT(*) AS cnt FROM popup_interaction WHERE ts > NOW() - INTERVAL '5 min' AND shown=true GROUP BY user_id)` |
| 单设备每日 push 次数 Max | `SELECT MAX(cnt) FROM (SELECT device_sn, COUNT(*) FROM push_events GROUP BY device_sn, date)` |
| 单实验每用户命中数 P99 | `SELECT PERCENTILE_CONT(0.99) ... FROM (SELECT user_id, COUNT(DISTINCT experiment_id) ... GROUP BY user_id)` |

### 模式 2：频次上限

**检测什么：** 单位时间内某个重复行为是否爆发。

**计算方式：** 滑动窗口内 COUNT（分钟级、5 分钟级、小时级都可以，选业务允许的最小时间单位）。

**何时用：** 重复动作本来就应该有频次上限（弹窗、push、短信、webhook 回调、重试）。

**典型指标：**

| 指标 | 意图 |
|------|------|
| 单用户 5min 内弹窗次数 | OncePerSession / OncePerScene fatigue 失效检测 |
| 单用户 1h push 接收次数 | 推送风暴检测 |
| 单 webhook URL 1min 重试次数 | 外部系统回调风暴检测 |
| 单设备 10s API 调用次数 | 爬虫/失控客户端检测 |

窗口大小选择：**窗口越小，越能捕捉瞬时风暴；窗口越大，越能捕捉慢性问题。**一般业务用 5min 捕捉瞬时，1h 捕捉累积。

### 模式 3：降级触发

**检测什么：** 服务是否正在降级（silent failure）。

**计算方式：** 关键指标等于 0 / null / 空值的占比（**不是错误率**）。

**为什么不是错误率：** 降级往往是"走 catch 分支返回空"而不是"抛异常"。抛异常走 Sentry，错误率会涨；返回空是合法业务值（"这次确实没结果"），错误率不会动，但业务就是死的。

**典型指标：**

| 指标 | 检测场景 |
|------|---------|
| `resolver_recipes_returned == 0` 的请求占比 | resolver 整个链路降级（DB / GrowthBook / CMS 任一挂了） |
| `search_results_count == 0` 的请求占比 | 搜索索引全量失效 |
| `cache_hit_rate < 10%` | 缓存节点挂了走回源，即将雪崩 |
| `recommended_items_count == 0` 的请求占比 | 推荐引擎降级 |

这类指标通常在 Prometheus / Grafana 里做（实时），也可以在数仓里算（延迟）。

## 为什么 P99 而不是平均

**总结：极端 case 被稀释问题。**

```
场景：1000 用户，999 人正常弹 1 次，1 人异常弹 100 次
平均：(999*1 + 1*100) / 1000 = 1.099 → 正常
P99： 按大小排序取第 990 位 = 1 → 正常
P999：取第 999 位 = 100 → 告警！
Max： = 100 → 告警！
```

**规则：**
- 总体健康度 → 用平均 / 中位数
- 护栏 → **只用** P99 / P999 / Max / Top-N
- 需要知道"有多少用户被坑"而非"最坑到什么程度" → 用分桶（`COUNT(*) WHERE value > threshold`）

## 阈值设计

### 三类阈值

| 类型 | 何时用 | 示例 |
|------|-------|------|
| **绝对阈值** | 业务有明确上限 | "OncePerSession 保证 P99 = 1，P99 > 2 warning, P99 > 5 critical" |
| **基线对比** | 正常值会随流量波动 | "错误率 > 历史同时段 3σ 告警" |
| **相对变化** | 关心突变而非绝对值 | "Scene QPS 5min rate 骤降 >50% critical" |

### 双等级 warning/critical

单阈值要么太松要么太紧。双等级可以区分：

- **warning**：有问题苗头，工作时间跟一下（通知 Slack 频道）
- **critical**：严重事故，立刻叫人（PagerDuty oncall）

典型间距：`critical = 2-5 × warning`。

### for 持续时间

Prometheus 告警用 `for` 子句避免瞬时毛刺误报：

| 护栏类型 | 建议 for |
|---------|---------|
| 极值（P99） | 5m |
| 频次风暴 | 1-2m |
| 降级检测 | 2-5m |

**频次风暴 for 要短**，因为真的风暴需要立即介入；极值 / 降级可以容忍 5min 观察。

## 实施位置

三种模式在哪实现，取决于时效需求：

| 模式 | 实时需求 | 实施位置 |
|------|---------|---------|
| 极值（业务侧） | 分钟级 | Prometheus 直接算（histogram percentile）| 
| 极值（业务侧） | 小时级 | 数仓 SQL + SLA 平台告警 |
| 频次上限 | 秒级 | Prometheus histogram + recording rule |
| 频次上限 | 小时级 | 数仓滑动窗口 SQL |
| 降级触发 | 秒级 | Prometheus `rate(metric{value=0}[5m])` |

**实时护栏优先放 Prometheus**（秒级响应），业务级护栏放数仓 SLA（更准、但延迟几十分钟到几小时）。

## 实例：SmartPopup 单用户 5min 弹窗次数 P99

来自 [customer-care observability.md §1.7](../../customer-care/docs/architecture/smart_popup/observability.md) + [launch-checklist.md §6](../../customer-care/docs/deployment/launch-checklist.md)。

**背景：** SmartPopup 有 fatigue 规则 OncePerSession（一次会话弹一次）。如果 fatigue 代码 bug 或配置错误，用户可能短时间被反复弹窗。

**设计：**

```sql
-- SLA 指标定义（dapp 平台，按小时调度）
WITH per_user_per_window AS (
  SELECT
    user_id,
    DATE_TRUNC('5 min', ts) AS window_start,
    COUNT(*) AS popup_count
  FROM dwm_smart_popup_funnel_hi
  WHERE shown = true
    AND ts > NOW() - INTERVAL '1 hour'
  GROUP BY 1, 2
)
SELECT
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY popup_count) AS p99,
  MAX(popup_count) AS max_count
FROM per_user_per_window;
```

**阈值：**

| 级别 | 条件 | 动作 |
|------|------|-----|
| 正常 | P99 = 1 | — |
| warning | P99 > 2 持续 10m | Slack 提示 |
| critical | P99 > 5 持续 5m | PagerDuty oncall |

**为什么这样设计：**
- OncePerSession 理论上 P99 = 1 是硬约束
- P99 > 1 = fatigue 在系统层面失效 → 一定是 bug 或配置错误
- 用 P99 而不用平均：哪怕只有 1% 用户被弹 10 次，也必须告警
- 5min 窗口：比 1h 更能捕获瞬时风暴；比 1min 稳定

## 常见错误

| 反模式 | 后果 | 正解 |
|-------|------|-----|
| 护栏用业务侧均值/总量 | 极端 case 永远隐藏 | 必须用 P99/Max/Top-N |
| 只在数仓算护栏 | 延迟几小时，事故早已扩散 | 实时护栏放 Prometheus |
| 只有 critical 没 warning | 要么一直不报，要么报了就出大事 | 双等级 + 不同通知渠道 |
| `for` 设得太长 | 风暴已过才告警 | 频次类 for 1-2m |
| 阈值拍脑袋 | 误报 / 漏报 | 先观察基线 1-2 周再定 |

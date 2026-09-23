# Dry Run And Preview

本文件回答两个问题：

1. `Dry Run / Preview` 作为治理能力，通用上应该怎么设计  
2. `customer-care / SmartPopup` 当前是如何把这套方法落地的

## 1. Dry Run 解决什么问题

Dry Run / Preview 的核心价值不是“给用户看一下结果”，而是 `在发布前发现配置失配和解释决策过程`。

它通常用来提前暴露这些风险：
- `DecisionPoint` 本身不会触发，或触发条件理解错误
- `Strategy` 命中逻辑与预期不一致
- `EligibilityRule` 与 `ExposurePolicy` 互相冲突
- `ExperimentBinding` 指向错误的 feature / variant
- `ContentBinding` 指向不存在、失效或错误内容
- `DeliveryBinding` 指向不可执行或状态异常的外部对象

所以它本质上是 `发布前验证能力`，不是演示页。

## 2. 它在抽象模型里的位置

Dry Run / Preview 主要验证这四层：

- `DecisionPoint`
  在给定上下文下，是否会进入这次业务决策
- `Strategy`
  若后台拥有方案定义主权，哪个方案会命中
- `*Binding`
  相关实验、内容、执行对象映射是否有效
- `ExecutableTarget`
  运行时最终会形成什么执行对象

可以把它理解成：

```text
输入上下文
  -> 验证 DecisionPoint
  -> 验证 Strategy / Binding
  -> 形成 ExecutableTarget 预览
  -> 输出解释信息
```

## 3. 通用技术方法

### 3.1 双模式执行

治理台里的 Dry Run，推荐默认支持两种执行模式：

- `同步单样本模拟`
  适合编辑过程中即时验证、单条样本调试、手工造数
- `异步批量回放`
  适合用真实样本做发布前评估、批量回归和影响分析

不要把所有诉求都塞进一种模式里。

### 3.2 执行引擎 SSOT

Dry Run 不应重写一套平行规则逻辑，而应复用与运行时一致的求值核心。

推荐原则：
- 运行时和 Dry Run 共享同一套规则求值逻辑
- Dry Run 只替换依赖注入，如：
  - in-memory store
  - collecting tracker
  - mock reporter
  - mock binding resolver

这样做的目标是：
- 避免运行时和 Dry Run 结果漂移
- 把差异限制在环境依赖，而不是规则语义

### 3.3 数据回放方法

如果批量 Dry Run 主要用于发现真实风险，推荐默认：

- 直接查数仓或真实事件存储
- 由批处理执行环境完成回放
- Admin 只负责：
  - 选择规则对象
  - 选择时间范围 / 用户过滤 / 采样率
  - 发起任务
  - 查询状态
  - 展示结果

不建议在 Admin 自己再维护一套历史事件存储。

### 3.4 输入归一方法

不管数据来自：
- 数仓真实样本
- 手工造数
- 真实样本上的 override

最后都应先归一成统一的 `Evaluation Context`，例如：
- `event_context`
- `user_context`
- `experiment_context`
- `content_context`
- `delivery_context`

这样 Dry Run 的评估逻辑才能和具体数据来源解耦。

### 3.5 结果归一方法

推荐把 Dry Run 的输出拆成两层：

1. `引擎原始事件流 / trace`
   用于保留完整求值过程
2. `分析友好结果表`
   用于 UI 展示、批量分析、报表和对比

而且如果系统里存在多个 Dry Run 执行端，这些聚合逻辑必须保持一致。

### 3.6 资源与边界控制

Dry Run 不是线上主链路，因此要明确资源边界：

- 允许秒级到分钟级延迟
- 允许异步任务
- 必须限制时间窗口、样本量、采样率、并发数
- 不得写线上状态
- 不得消耗真实曝光次数、频控计数或渠道配额

## 4. 两种推荐模式

### 4.1 即时模拟

适合：
- 单次配置调试
- 编辑过程中快速验证
- 产品、运营、研发一起看“为什么命中 / 不命中”

典型输入：
- 用户属性
- 事件类型
- 设备状态
- 时间上下文
- 试验上下文

典型输出：
- 是否进入该 `DecisionPoint`
- 是否命中某个 `Strategy`
- 哪条 `EligibilityRule` 生效
- 是否被 `ExposurePolicy` 拦截
- 最终形成的 `ExecutableTarget`

### 4.2 历史回放

适合：
- 发布前批量评估
- 新规则替换旧规则时做影响分析
- 检查是否会误伤、漏发或放大量级异常

典型输入：
- 历史事件样本
- 用户样本集
- 指定时间窗口

典型输出：
- 命中率 / 拦截率
- 方案分布
- binding 失效样本
- 与旧配置的差异摘要

## 5. 标准输出建议

一个合格的 Dry Run / Preview，不应只返回“命中 / 未命中”。

建议至少输出：

| 输出项 | 回答的问题 |
|------|-----------|
| `DecisionPoint Result` | 这次是否进入该决策点 |
| `Strategy Result` | 命中了哪个方案，或为何没有命中 |
| `EligibilityRule Trace` | 哪些资格规则通过，哪些未通过 |
| `ExposurePolicy Trace` | 是否被频控、冷却或其他曝光策略拦截 |
| `Binding Status` | 相关 GrowthBook / CMS / 外部执行对象是否有效 |
| `ExecutableTarget Preview` | 运行时最终会执行或展示什么 |
| `Warnings` | 哪些字段、引用或状态存在风险但尚未阻断 |

## 6. 与发布的关系

Dry Run / Preview 和发布的关系应是：

```text
编辑
  -> Dry Run / Preview
  -> 冻结 snapshot
  -> PublishOrder
```

也就是说：
- Dry Run 在 `PublishOrder` 之前
- Dry Run 不应偷偷修改线上状态
- Dry Run 的输出应能进入发布前检查摘要

## 7. 常见反模式

### 7.1 只返回 true / false

这会让用户知道结果，却不知道原因，出了问题也无法定位到底是规则、频控还是 binding。

### 7.2 只预览内容，不验证 binding

这会把 Dry Run 做成展示页，而不是治理能力。

### 7.3 复用线上副作用逻辑

Dry Run 不应写入线上状态，也不应消耗真实曝光次数、频控计数或渠道配额。

### 7.4 把 Dry Run 当成发布替代品

Dry Run 只能帮助发现风险，不能替代 `PublishOrder`、审计和回滚。

## 8. Example: SmartPopup In customer-care

`customer-care` 当前的 SmartPopup Dry Run 是这套方法的一个具体实现。

### 8.1 执行模式

- `Mode A (simulate)`
  Admin 内同步执行，适合单 recipe 即时验证
- `Mode B (batch)`
  Dagster 异步执行，适合批量回放真实数仓样本

### 8.2 引擎复用

SmartPopup 不是在 Python 或 Next.js 中重写规则逻辑，而是：
- 用 Dart 编译出 `dryrun_engine.js`
- Admin Node VM 与 Dagster PyMiniRacer 都加载同一份 JS 引擎

这保证了：
- 端侧评估逻辑
- Admin 模拟
- Dagster 批量回放

共享同一套求值核心。

### 8.3 数据回放来源

批量回放直接查 Athena：
- 表：`analytics.dwd_base_hi`
- 过滤条件：
  - 日期范围
  - 指定 user_ids
  - sample_rate
  - bundle 中真正用到的 event keys

也就是说，SmartPopup 当前的批量 Dry Run 不自己维护一套历史样本库，而是直接把数仓作为回放数据源。

### 8.4 输入与输出

当前实现的核心输入是：
- `bundle_json`
- `rule_version`
- `user_ids`
- `sample_rate`

当前实现的输出分两层：
- 引擎原始输出：`events + errors`
- 聚合结果表：
  `dt, user_id, rule_version, scene_id, event_key, is_hit, is_fatigued, fatigue_operator, feature_values, condition_trace`

### 8.5 已落地的方法抽象

从 SmartPopup 里可以直接抽出这几条通用方法：
- Dry Run 应支持同步模拟和异步批量回放
- 批量回放可以直接查数仓真实事件
- Admin 只负责触发和展示，不直接承担批量计算
- 规则求值引擎必须与运行时共享 SSOT
- 原始 event 流与聚合结果表要分层保存

## 9. SmartPopup 如何映射抽象概念

在 SmartPopup 里，Dry Run / Preview 可读成：

- `DecisionPoint -> Scene`
- `Strategy -> Recipe`
- `EligibilityRule -> Condition`
- `ExposurePolicy -> Fatigue[]`
- `ContentBinding -> cms_id`
- `ExperimentBinding -> GrowthBook feature / variant`
- `ExecutableTarget -> resolve 后返回给 App 的 popup config`

所以一次 SmartPopup 的 Dry Run，至少应能解释：
- 为什么某个 scene 会或不会被触发
- 为什么命中某个 recipe
- 是否被 fatigue 拦截
- 当前 recipe 的 CMS 与 GrowthBook 映射是否有效
- 最终返回给 App 的 popup config 是什么

## 10. 一句话定义

`Dry Run / Preview` 是运营控制台在发布前验证 `DecisionPoint / Strategy / *Binding / ExecutableTarget` 的能力，用来降低多系统配置失配和发布风险，而不是普通的结果展示功能。

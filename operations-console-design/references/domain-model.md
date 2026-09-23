# Domain Model Reference

本文件给 `operations-console-design` 提供一份更面向人阅读的领域模型说明。目标不是把所有渠道硬揉成一棵对象树，而是先回答：

- 这类后台到底在管什么
- 哪些是后台自己的对象
- 哪些只是和外部系统的绑定关系
- 最终谁在运行时真正执行

真实项目样例见 [example-smartpopup.md](example-smartpopup.md)。

## 1. 先用一句话理解这类后台

这类后台本质上是在管理：

`一个业务域里的若干决策点，以及这些决策点和实验系统、内容系统、投放系统之间的受控关系。`

所以它的核心不是“页面”或“表”，而是三类东西：
- 后台自己定义和发布的对象
- 后台和外部系统之间的绑定关系
- 运行时最终消费的执行对象

## 2. 核心概念总表

| 概念 | 作用 | 它回答的问题 | 主权 | 常见落地名 | 建议最小字段 |
|------|------|-------------|------|-----------|--------------|
| `Domain` | 业务治理边界 | 这组对象属于哪个业务治理边界？ | Admin | `Project` | `domain_id`, `name`, `description`, `status` |
| `DecisionPoint` | 一个需要做触达决策的业务时点 | 在什么业务时点需要做一次触达/不触达决策？ | Admin | `Scene` | `decision_point_id`, `domain_id`, `type`, `name`, `entry_condition`, `owner`, `status` |
| `Strategy` | 一个决策点下的候选业务方案 | 如果后台自己定义方案，有哪些候选方案？ | Admin（仅在后台拥有方案定义时） | `Recipe` / `Treatment` | `strategy_id`, `decision_point_id`, `description`, `eligibility_rule`, `exposure_policy`, `sort_order`, `status` |
| `ExperimentBinding` | 和实验系统的映射关系 | 这个对象在实验系统里对应谁？ | Admin 维护映射；实验系统拥有实验能力 | `feature mapping` / `variant mapping` | `provider`, `external_project_id`, `external_feature_key`, `external_variant_key`, `status` |
| `ContentBinding` | 和内容主实体的映射关系 | 这个对象展示的内容由谁持有？ | Admin 维护映射；CMS 拥有内容 | `cms_id` / content ref | `provider`, `content_id`, `content_type`, `title_snapshot`, `status_snapshot` |
| `DeliveryBinding` | 和执行主实体的映射关系 | 这个对象最终由哪个外部执行对象承载？ | Admin 维护映射；外部渠道系统拥有执行 | `journey_id` / placement ref | `provider`, `delivery_object_id`, `delivery_object_type`, `name_snapshot`, `status_snapshot` |
| `PublishOrder` | 一次受控发布动作 | 这次发布了什么、经过了哪些状态？ | Admin | publish order / release order | `publish_order_id`, `domain_id`, `scope`, `snapshot`, `status` |
| `AuditLog` | 变更责任追踪 | 谁改了什么、何时生效？ | Admin | audit log | `actor`, `object_type`, `object_id`, `before`, `after`, `timestamp` |
| `ExecutableTarget` | 最终被运行时或外部系统消费/执行的对象 | 最终真正影响用户的对象是什么？ | Runtime 或外部系统 | resolved config / content / journey | 视运行时或外部系统而定，不强行统一 |

## 3. 关系总览

### 3.1 后台自己拥有的对象

```text
Domain
  └── DecisionPoint
        ├── Strategy*
        ├── PublishOrder*
        └── AuditLog*
```

这层只看“后台自己治理什么”。

读法：
- `Domain` 是业务边界
- `DecisionPoint` 是一级治理对象
- `Strategy` 是可选的，因为不是每个场景都必须由后台自己定义方案
- `PublishOrder` 和 `AuditLog` 是治理闭环对象

### 3.2 后台和外部系统的关系

```text
DecisionPoint
  ├── ExperimentBinding*
  └── DeliveryBinding*

Strategy
  ├── ExperimentBinding*
  ├── ContentBinding*
  └── DeliveryBinding*
```

这层只看“后台对象如何连接外部系统”。

读法：
- 绑定整个决策点时，挂在 `DecisionPoint`
- 绑定某个具体方案时，挂在 `Strategy`

典型例子：
- `Scene -> GrowthBook feature` 更像 `DecisionPoint` 级绑定
- `Recipe -> GrowthBook variant` 更像 `Strategy` 级绑定
- `Recipe -> CMS content` 通常也是 `Strategy` 级绑定

### 3.3 运行时真正执行的对象

```text
Strategy or DeliveryBinding
  └── ExecutableTarget
```

读法：
- 后台治理对象最终会指向某个实际执行目标
- 这个目标可能是后台发布后的结果，也可能是外部系统里的主实体

例如：
- 一个 resolved popup config
- 一个 CMS content instance
- 一个 Dittofeed journey

## 4. 什么时候该建 Strategy，什么时候不该建

这是最关键的建模判断。

### 应该建 `Strategy`

当某个业务方案满足下面大多数条件时，应建 `Strategy`：
- 后台自己创建它
- 后台自己编辑它
- 后台自己校验它
- 后台自己发布它
- 它经常作为 A/B 候选方案出现

典型例子：
- popup 的 `Recipe`
- 某些 touchpoint 的 `Recipe`

### 不应强建 `Strategy`

当某个执行对象满足下面大多数条件时，不应为了模型统一而硬建 `Strategy`：
- 主权在外部系统
- trigger / fatigue / execution flow 已在外部系统定义
- 后台只需要绑定、校验、审计、跳转

典型例子：
- Dittofeed `user journey`

此时更合理的是：
- `DecisionPoint + DeliveryBinding`

## 5. Condition 和 Fatigue 放在哪

这两个概念不建议升成一级治理对象，更适合作为 `Strategy` 的内部结构。

推荐抽象：

| 维度 | `condition` | `fatigue` |
|------|-------------|-----------|
| 抽象说法 | `EligibilityRule` | `ExposurePolicy` |
| 它回答的问题 | 这个方案在当前上下文下是否有资格命中？ | 即使有资格命中，这次是否允许再次投放？ |

所以可读成：

```text
Strategy
  ├── EligibilityRule
  ├── ExposurePolicy
  ├── ContentBinding?
  └── DeliveryBinding?
```

这里的重点是：
- `condition` 决定“应不应该命中”
- `fatigue` 决定“这次能不能发”

## 6. 主权判断清单

设计时，对每个对象都问这四个问题：

1. 是后台自己定义的吗？
2. 是后台自己发布的吗？
3. 是后台自己审计的吗？
4. 还是后台只是引用一个外部系统里的主实体？

判断结果：
- 前三项为主：更像 `DecisionPoint` 或 `Strategy`
- 第四项为主：更像 `Binding`

## 7. 三个例子的概念映射

| 例子 | `Domain` | `DecisionPoint` | `Strategy` | `ExperimentBinding` | `ContentBinding` | `DeliveryBinding` | `ExecutableTarget` | 模式总结 |
|------|----------|-----------------|------------|---------------------|------------------|-------------------|--------------------|-----------|
| 触点 | engagement / growth | 首页会员触点位 | 强：可有 `control`、`upsell-card-a`、`upsell-card-b` | 强：`DecisionPoint -> GrowthBook feature`，`Strategy -> GrowthBook variant` | 常有：若触点内容在 CMS，则绑定 CMS content | 可有：若存在独立触点配置对象，则绑定该对象 | 触点内容 / 触点配置 | `DecisionPoint + Strategy + ContentBinding` |
| Push | push engagement / reactivation | 低电量提醒场景 | 弱或无：若 Dittofeed `user journey` 已拥有 trigger/fatigue 主权，则不强造本地 strategy | 可有：可让整个 push scene 接一个 GrowthBook feature | 弱或无：除非 push 文案本身有独立内容主实体 | 强：绑定 `dittofeed_user_journey_id` | Dittofeed `user journey` | `DecisionPoint + DeliveryBinding` |
| 差评拦截 | support deflection / retention | 用户准备去打差评时的拦截时点 | 强：可有 `control`、`contact-support-popup`、`retry-guide-popup` | 强：`DecisionPoint -> GrowthBook feature`，`Strategy -> GrowthBook variant` | 常有：若拦截弹窗内容在 CMS，则绑定 CMS content | 弱：通常不需要，除非后面接外部执行对象 | 拦截弹窗 | `DecisionPoint + Strategy + ExperimentBinding + ContentBinding` |

## 8. 最后记住三句话

1. `DecisionPoint` 是后台真正的一级对象。
2. `Strategy` 只在后台拥有方案定义主权时才出现。
3. `Binding` 用来连接外部系统，不要把外部主实体硬建成后台自己的对象。

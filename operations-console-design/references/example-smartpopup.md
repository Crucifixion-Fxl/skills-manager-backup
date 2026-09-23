# Example: SmartPopup

本文件只做一件事：说明抽象领域模型如何在 `customer-care` 当前的 SmartPopup 方案里落地。

阅读顺序固定为两层：
- 先看抽象概念：`Domain / DecisionPoint / Strategy / *Binding / PublishOrder / AuditLog / ExecutableTarget`
- 再看 SmartPopup 里的具体术语：`Project / Scene / Recipe / cms_id / publish order`

## 1. 先给出结论

SmartPopup 不是通用模型本身，而是下面这组抽象概念的一种具体实现：

```text
Domain
  └── DecisionPoint
        ├── Strategy
        ├── ExperimentBinding
        ├── ContentBinding
        ├── PublishOrder
        └── AuditLog
```

它在当前项目中的实际落地是：

```text
Project (隐含治理边界)
  └── Scene
        ├── Recipe
        ├── GrowthBook Mapping
        ├── Publish Order
        └── Audit Log
```

对应关系要点：
- `DecisionPoint -> Scene`
- `Strategy -> Recipe`
- `ExperimentBinding -> Scene / Recipe 与 GrowthBook 的映射`
- `ContentBinding -> Recipe 对 CMS 内容的引用`
- `ExecutableTarget -> backend resolve 后返回给 App 的 popup config`

## 2. 抽象概念与实际概念对照

| 抽象概念 | SmartPopup 落地 | 在 SmartPopup 里回答什么问题 |
|---------|----------------|------------------------------|
| `Domain` | `Project`（当前多为隐含边界） | 这套 popup 规则属于哪个业务域 |
| `DecisionPoint` | `Scene` | 在哪个触达时点做一次弹窗决策 |
| `Strategy` | `Recipe` | 命中该 scene 后具体采用哪种方案 |
| `ExperimentBinding` | `Scene / Recipe -> GrowthBook feature / variant` | 这个业务决策如何进入实验分流 |
| `ContentBinding` | `Recipe -> cms_id` | 这个方案最终展示哪份内容 |
| `PublishOrder` | 发布单 / 分环境发布记录 | 这次变更如何冻结、审批、发布 |
| `AuditLog` | 审计日志 | 谁改了什么、何时改、为何改 |
| `ExecutableTarget` | popup config snapshot / resolve 结果 | App 最终真正消费的配置是什么 |

这一层对齐后，再去看 `Scene / Recipe` 就不会把它误当成通用模型。

## 3. SmartPopup 业务能力如何回到抽象模型

从 [docs/product/user-stories/smart-popup-admin.md](/home/jchen/customer-care/docs/product/user-stories/smart-popup-admin.md) 能抽出几类稳定能力：

| SmartPopup 能力 | 先落到抽象概念 | 再落到实际对象 |
|----------------|----------------|----------------|
| 配置一个弹窗触达时点 | `DecisionPoint` | `Scene` |
| 在该时点下定义多个候选方案 | `Strategy` | `Recipe` |
| 把方案接入 GrowthBook 实验 | `ExperimentBinding` | `Recipe -> variant` |
| 把方案接入 CMS 内容 | `ContentBinding` | `cms_id` |
| 做 Dry Run / Preview | `DecisionPoint + Strategy + ExecutableTarget` | `Scene + Recipe + resolve 结果` |
| 发起 staging / prod 发布 | `PublishOrder` | 发布单 |
| 追踪历史修改 | `AuditLog` | 审计日志 |

所以 SmartPopup 的 user story，不是围绕“表单页面”展开，而是围绕这套治理闭环展开。

## 4. SmartPopup 的实际领域结构

从 [docs/architecture/smart_popup/domain-model.md](/home/jchen/customer-care/docs/architecture/smart_popup/domain-model.md) 可以把实际模型整理成：

```text
Project (逻辑边界)
  └── Scene
        ├── EntryEvent
        ├── Recipe
        │     ├── Condition
        │     ├── Fatigue[]
        │     └── cms_id
        ├── GrowthBookMapping
        ├── PublishOrder
        └── AuditLog
```

把这张图翻译回抽象模型时，要先做概念提升：

- `Scene` 不是“弹窗专属名词”，而是 `DecisionPoint`
- `Recipe` 不是“通用二级对象名”，而是 `Strategy`
- `Condition` 对应 `EligibilityRule`
- `Fatigue[]` 对应 `ExposurePolicy`
- `cms_id` 对应 `ContentBinding`

所以更高一层的读法是：

```text
Domain
  └── DecisionPoint
        ├── Strategy
        │     ├── EligibilityRule
        │     ├── ExposurePolicy
        │     └── ContentBinding
        ├── ExperimentBinding
        ├── PublishOrder
        └── AuditLog
```

## 5. 主权边界如何对应

| 抽象概念 | SmartPopup 实际落地 | 主权方 | 边界说明 |
|---------|--------------------|--------|----------|
| `DecisionPoint` | `Scene` | Admin | 后台定义弹窗决策点 |
| `Strategy` | `Recipe` | Admin | 后台定义命中后的业务方案 |
| `ExperimentBinding` | `Scene / Recipe -> GrowthBook` | Admin 维护映射，GrowthBook 持有实验对象 | 后台不重做实验平台 |
| `ContentBinding` | `Recipe -> cms_id` | Admin 维护引用，CMS 持有内容主实体 | 后台不重做内容编辑器 |
| `PublishOrder` | 发布单 | Admin | 负责冻结、审批、发布、回滚 |
| `ExecutableTarget` | resolve 后的 popup config | Runtime | App 消费发布结果，而不是直接读 Admin |

## 6. 页面结构如何先对齐抽象模型，再映射到 SmartPopup

从 [docs/architecture/smart_popup/admin.md](/home/jchen/customer-care/docs/architecture/smart_popup/admin.md) 可抽出 SmartPopup 页面结构：

```text
/facts
/scenes
  └── /scenes/:id
/publish
/audit
```

先按抽象概念理解：

| 抽象概念 | SmartPopup 页面承载 |
|----------|---------------------|
| `DecisionPoint` | `/scenes`、`/scenes/:id` |
| `Strategy` | `/scenes/:id` 中的 recipe 编辑区 |
| `ExperimentBinding` | `/scenes/:id` 中的 GrowthBook 状态 |
| `ContentBinding` | `/scenes/:id` 中的 `cms_id` 引用区 |
| `PublishOrder` | `/publish` |
| `AuditLog` | `/audit` |

再回到实际术语时，才说：
- Scene 列表页
- Scene 详情页
- Recipe 编辑器
- Publish 页面
- Audit 页面

这样写，页面设计就仍然建立在抽象模型上，而不是反过来让抽象模型被具体页面名绑死。

## 7. 部署架构如何先看抽象关系，再看 SmartPopup 实现

从以下文档可抽出 SmartPopup 的部署经验：
- [docs/deployment/admin.md](/home/jchen/customer-care/docs/deployment/admin.md)
- [docs/architecture/smart_popup/overview.md](/home/jchen/customer-care/docs/architecture/smart_popup/overview.md)
- [docs/architecture/overview.md](/home/jchen/customer-care/docs/architecture/overview.md)

应先按抽象概念理解：

| 抽象概念 | SmartPopup 中的实现关系 |
|----------|-------------------------|
| `DecisionPoint / Strategy / *Binding` | Admin 中的编辑态对象 |
| `PublishOrder` | snapshot 组装与分环境发布 |
| `ExecutableTarget` | Go backend resolve 后返回给 App 的 popup config |

因此真正的运行链路是：

`Admin 编辑态 -> 发布态产物 -> Runtime 消费`

不是：

`App -> 实时依赖 Admin`

## 8. 这个例子真正可复用的部分

SmartPopup 可复用的不是 `Scene / Recipe / cms_id` 这几个词本身，而是下面这组结构关系：

- 先有统一的 `DecisionPoint`
- 对 Admin-native 渠道，再有 `Strategy`
- 用 `ExperimentBinding` 接入 GrowthBook
- 用 `ContentBinding` 接入 CMS
- 用 `PublishOrder` 管理受控发布
- 让 Runtime 消费发布结果，而不是直接依赖控制面

这就是为什么它可以推广到触点、运营位、部分内容触达后台，而不是只适用于 popup。

## 9. 哪些词不要直接拿去泛化

- `Scene` 只是 SmartPopup 里 `DecisionPoint` 的落地名
- `Recipe` 只是 SmartPopup 里 `Strategy` 的落地名
- `cms_id` 只是 SmartPopup 里 `ContentBinding` 的落地字段

因此，写方案时应优先写抽象概念；只有在落到 SmartPopup 这个例子时，才写 `Scene / Recipe / cms_id`。

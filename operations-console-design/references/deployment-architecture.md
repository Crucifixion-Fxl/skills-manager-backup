# Deployment Architecture Reference

本参考方案关注的不是“Admin 部署到 K8s 就结束了”，而是：
- 控制面和运行时怎么分层
- 多 DC 怎么切
- 跨 DC 网络依赖怎么收敛
- 哪些对象必须区域内读，哪些对象可以跨区同步

这类后台如果不把 DC 和网络问题提前设计清楚，后面很容易变成：
- 控制面部署在单一区域
- 运行时请求跨区拉配置
- 外部系统跨区串联
- 延迟、稳定性、合规问题一起出现

## 1. 设计目标

部署架构至少要回答：

1. 控制面放哪里
2. 运行时放哪里
3. 发布产物放哪里
4. 外部系统是否允许跨 DC 主路径访问
5. 运行时是否依赖远端控制面

推荐的默认目标是：

`运行时请求不跨 DC 同步依赖控制面；控制面可以集中，但运行时消费的必须是区域内可读的已发布结果或区域缓存。`

## 2. 分层

推荐把系统拆成四层：

### 2.1 Control Plane

包括：
- Admin Web
- Admin API
- PublishOrder
- AuditLog
- Preview / Dry Run orchestration

这是编辑态和治理态。

### 2.2 Professional Systems

包括：
- GrowthBook
- CMS
- Dittofeed
- 审批系统

这些系统可能是全局的，也可能按区域有不同实例。

### 2.3 Publish / Distribution Layer

包括：
- 编辑态数据库
- 发布态 snapshot
- 对象存储
- 区域缓存 / 镜像

这是连接控制面和运行时的关键层。

### 2.4 Runtime Plane

包括：
- Runtime backend
- App
- Channel runtime

这是面向最终用户的执行平面。

## 3. 多 DC 的优先原则

### 3.1 不让运行时主路径跨 DC

最重要的原则：

`最终用户请求链路，不应跨 DC 同步访问控制面依赖。`

不应该出现：
- App 在 US 请求一个 CN 控制面接口
- Runtime backend 在 EU 同步读取另一个 DC 的编辑态数据库
- 用户请求时再跨区查 CMS / GrowthBook / 控制面数据库

### 3.2 编辑态可以集中，运行态必须区域内可读

允许：
- 控制面集中在主 DC
- 运营在一个主控制面上编辑

但要求：
- 运行时消费的结果必须区域内可读
- 至少要有区域镜像、区域缓存、区域 snapshot

### 3.3 跨 DC 传的是发布结果，不是用户请求

跨 DC 同步应该优先同步：
- snapshot
- 内容镜像
- 实验配置镜像
- delivery binding 镜像

而不是让最终用户请求直接跨区查控制面。

## 4. 推荐拓扑

### 4.1 逻辑拓扑

```text
                 Global / Home DC
         ┌──────────────────────────────┐
         │ Control Plane                │
         │ Admin + DB + Publish + Audit │
         └──────────────┬───────────────┘
                        │
                        │ publish / replicate
                        ▼
        ┌────────────────────────────────────────┐
        │ Distribution Layer                     │
        │ snapshot store / object store / cache  │
        └───────┬─────────────────┬──────────────┘
                │                 │
                ▼                 ▼
          Regional DC A      Regional DC B
        ┌───────────────┐  ┌───────────────┐
        │ Runtime Plane │  │ Runtime Plane │
        │ backend / app │  │ backend / app │
        └───────────────┘  └───────────────┘
```

核心思想：
- Control Plane 可以集中
- Runtime Plane 按区域部署
- Distribution Layer 承担跨 DC 分发

### 4.2 区域内依赖原则

在每个 Runtime DC 内，优先要求：
- 读取区域本地 snapshot
- 读取区域本地缓存
- 使用区域本地镜像配置

如果某个外部系统是全局单实例，需要明确：
- 是否允许跨区访问
- 是否只在控制面使用
- 是否需要镜像或预拉取

## 5. 对象按 DC 的归属

### 5.1 编辑态 SSOT

通常集中在控制面主 DC：
- Domain / Project
- DecisionPoint / Scene
- Strategy / Recipe
- Binding
- PublishOrder
- AuditLog

### 5.2 发布态对象

应可被复制到各运行时 DC：
- snapshot
- 配置索引
- 内容索引或已解析内容镜像
- 外部 binding 的状态快照

### 5.3 运行态对象

只应在区域内读：
- runtime config
- experiment assignment result
- resolved content/config

## 6. 外部系统的网络位置问题

部署设计时必须逐个回答下面这些问题。

### 6.1 GrowthBook

要回答：
- GrowthBook 是全局实例还是区域实例
- 运行时是否在请求主路径查 GrowthBook
- 如果是，是否允许跨 DC

推荐默认：
- GrowthBook 查询尽量前置到解析层
- 如果运行时必须依赖 GrowthBook，优先使用区域可读方式

### 6.2 CMS

要回答：
- 内容是否在发布时拉取并固化
- 还是运行时每次查 CMS
- CMS 是否区域可访问

推荐默认：
- 不让最终用户请求长链路同步依赖远端 CMS
- 若内容变化频率允许，优先考虑发布时解析或区域缓存

### 6.3 Dittofeed / Delivery Systems

要回答：
- 这些系统是在控制面使用，还是运行时主路径使用
- 若是 push/journey 系统，运行时是否真正同步依赖它

推荐默认：
- Admin 只做 binding、校验、展示
- 用户主路径不要因为 delivery system 的远端调用而变长

## 7. SmartPopup 抽象出来的经验

从 `customer-care` 当前文档中可提炼出一个很重要的网络原则：

`Backend 是解析中枢，但最终目标仍然是让运行时主路径使用可缓存、可区域化的已发布结果。`

尤其是在 SmartPopup 的 ADR 中，已经收敛出一个关键方向：

`运行时请求不跨 DC 同步访问控制面依赖；区域内只读快照 / 配置缓存，本地完成 resolve。`

这个结论非常适合沉淀成此 skill 的默认规则。

## 8. 部署模式选择

### 8.1 模式 A：集中控制面 + 区域运行时

适合大多数场景。

特点：
- Admin 单点治理
- 发布态产物分发到各 DC
- 运行时区域内读

优点：
- 后台治理简单
- 运行时稳定

风险：
- 发布分发链路必须设计好

### 8.2 模式 B：每个 DC 一套控制面

只在以下情况考虑：
- 合规强隔离
- 区域业务强自治
- 无法共享编辑态数据库

代价：
- 运营复杂度高
- 多套控制面一致性难

### 8.3 模式 C：集中控制面 + 运行时直接跨区读

默认不推荐。

问题：
- 延迟高
- 故障面大
- 合规风险高

## 9. 发布链路与网络

发布链路不能只写“staging -> prod”，还要明确：

1. 发布产物是否按 DC 分发
2. 各 DC 是否有独立对象存储路径
3. 发布是否需要等待各 DC 分发完成
4. 回滚是否也是按 DC 维度执行

推荐默认：

```text
Control Plane Publish
  -> build snapshot
  -> validate bindings
  -> publish to staging distribution path
  -> regional verification
  -> approval
  -> publish to prod distribution path
  -> replicate / activate per DC
```

## 10. K8s / 网络层建议

如果部署在 K8s，需要明确：
- Admin 部署在哪个 region / cluster
- Runtime backend 部署在哪些 region / cluster
- 对象存储是全局桶还是区域桶
- Ingress 是否按 region 暴露
- Service 间网络是否跨 cluster / 跨 region

建议输出至少包括：
- DC 拓扑图
- 关键调用链路图
- 哪些调用允许跨 DC，哪些禁止

## 11. 必问清单

设计部署架构时必须问：

1. 最终用户请求链路允许跨 DC 吗？
2. 控制面是否集中？
3. 运行时消费编辑态 DB 还是发布态 snapshot？
4. GrowthBook / CMS / Dittofeed 是否区域可读？
5. 发布产物如何分发到各 DC？
6. 回滚是全局回滚还是按 DC 回滚？

## 12. 反模式

### 12.1 把控制面数据库当运行时主数据源

后果：
- 一旦跨 DC，就把编辑态依赖引到用户主路径

### 12.2 最终用户请求链路跨区串联多个外部系统

后果：
- 延迟抖动
- 故障面叠加

### 12.3 只讲 K8s 部署，不讲 DC 和网络

后果：
- 文档看起来完整，实际没有回答最关键的可用性问题

### 12.4 把发布理解成“改数据库”

后果：
- 无法冻结版本
- 无法稳定分发到多 DC
- 无法做区域回滚

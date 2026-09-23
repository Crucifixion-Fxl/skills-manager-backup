# Admin Information Architecture And Flows

这份文档只回答一个问题：

`当领域模型先被抽象成 Domain / DecisionPoint / Strategy / *Binding / PublishOrder / AuditLog 之后，后台页面和操作流应该怎样承载它。`

因此阅读顺序也是固定的：
- 先看抽象概念
- 再看这些概念通常如何落到页面
- 最后才看 `Scene / Recipe` 这一类具体实现名

## 1. 先给出总索引

| 抽象概念 | 页面上通常长成什么 | 页面真正要支持的任务 |
|---------|------------------|----------------------|
| `Domain` | Project / 业务边界页 | 定义治理边界与外部系统范围 |
| `DecisionPoint` | 一级对象列表与详情页 | 定义一个需要做业务决策的触达时点 |
| `Strategy` | 二级方案编辑器 | 定义命中后采用哪种方案 |
| `ExperimentBinding` | GrowthBook 绑定区 | 让业务对象进入实验分流 |
| `ContentBinding` | CMS 绑定区 | 让业务方案引用内容主实体 |
| `DeliveryBinding` | Journey / 外部对象绑定区 | 让业务对象连接外部执行系统 |
| `PublishOrder` | Publish 页面 | 冻结、审批、发布、回滚 |
| `AuditLog` | Audit 页面 | 追踪变更责任 |
| `ExecutableTarget` | Preview / Dry Run 输出 | 解释运行时最终会执行什么 |

后面的 IA 和流程，都是这张表的展开。

## 2. 页面结构先围绕抽象概念组织

推荐的一组稳定一级菜单：

```text
/projects       Domain / 业务边界
/decision-points
/preview
/publish
/audit
```

只有在项目已经确定落成某种具体术语时，才把它翻译成：

```text
/projects
/scenes
/recipes        仅当该项目存在 Admin-native Strategy
/preview
/publish
/audit
```

这里的关键不是页面名，而是顺序：
- 先有 `DecisionPoint`
- 再判断是否需要单独展示 `Strategy`
- 再把 `Binding` 放回详情页或专门区域

## 3. 页面模块如何对应抽象概念

### 3.1 Domain 页

页面职责：
- 定义治理边界
- 配置外部系统范围
- 承接权限和发布范围

常见展示：
- 项目名称
- 关联 GrowthBook project
- 默认 CMS 空间
- 运行环境与发布状态

如果落到 SmartPopup 这种实现里，这一页通常被叫作 `Project`。

### 3.2 DecisionPoint 列表页

页面职责：
- 管理一组需要做业务决策的触达时点
- 作为后台一级对象列表

常见展示：
- id / 名称 / 类型
- owner
- status
- 绑定状态
- 最近发布时间

如果落到 SmartPopup，这一页通常被叫作 `Scene 列表页`。

### 3.3 DecisionPoint 详情页

这是主工作台。它首先是 `DecisionPoint` 页面，其次才可能是某种 `Scene 详情页`。

推荐模块：
1. 基本信息
2. `ExperimentBinding` 状态
3. `Strategy` 或 `DeliveryBinding`
4. Preview / Dry Run
5. `PublishOrder` 摘要
6. 最近 `AuditLog`

也就是说，详情页应围绕：

```text
DecisionPoint
  + related Bindings
  + related Strategy
  + related Publish/Audit
```

而不是围绕数据库表单字段堆积。

### 3.4 Strategy 编辑器

只有在该渠道属于 Admin-native 时才出现。

页面职责：
- 定义候选方案
- 编辑资格规则与曝光约束
- 维护内容和实验映射

常见字段：
- `strategy_id`
- `EligibilityRule`
- `ExposurePolicy`
- `ContentBinding`
- `ExperimentBinding`

如果落到 SmartPopup，这一页或区域通常被叫作 `Recipe 编辑器`。

### 3.5 Binding 区域

这一块不要和 `Strategy` 混写。

页面职责：
- 展示外部系统映射关系
- 做状态校验
- 提供 deep-link

建议拆成三个区域：
- `ExperimentBinding`
- `ContentBinding`
- `DeliveryBinding`

这样页面结构才和领域模型一一对应。

### 3.6 Preview / Dry Run

页面职责：
- 解释一次业务决策为什么命中或未命中
- 展示最终形成的 `ExecutableTarget`

输出至少回答：
- 是哪条 `EligibilityRule` 起作用
- 是不是 `ExposurePolicy` 拦截了
- 当前 `Binding` 是否有效
- 运行时最终会执行哪个对象

### 3.7 Publish

发布页不是“发布按钮页”，而是 `PublishOrder` 工作台。

至少要有：
- 发布范围
- snapshot 摘要
- 外部引用校验结果
- staging / prod 状态
- 审批状态
- 回滚入口

### 3.8 Audit

审计页不是简单日志页，而是 `AuditLog` 解释页。

至少支持：
- 按 `Domain / DecisionPoint / actor` 查询
- 看改前改后
- 看关联发布单
- 看 binding 变化

## 4. 操作流先写抽象动作，再落到具体术语

### 4.1 创建一个 DecisionPoint

抽象动作：

```text
填写基本信息
  -> 保存 DecisionPoint
  -> 创建或校验 ExperimentBinding
  -> 进入详情页
```

如果落到 SmartPopup，对应才是：

```text
填写 Scene
  -> 保存 Scene
  -> 创建或校验 GrowthBook feature shell
  -> 返回 Scene 详情页
```

### 4.2 在 DecisionPoint 下新增一个 Strategy

抽象动作：

```text
进入 DecisionPoint 详情页
  -> 添加 Strategy
  -> 编辑 EligibilityRule / ExposurePolicy
  -> 维护 ContentBinding
  -> 保存 Strategy
  -> 同步或校验 ExperimentBinding
```

如果落到 SmartPopup，对应才是：

```text
进入 Scene 详情页
  -> 添加 Recipe
  -> 编辑 condition / fatigues / cms_id
  -> 保存 Recipe
  -> 同步或校验 GrowthBook variant
```

### 4.3 只绑定外部执行对象，不新增 Strategy

这类流通常出现在 Push 一类场景。

抽象动作：

```text
进入 DecisionPoint 详情页
  -> 维护 DeliveryBinding
  -> 拉取外部对象摘要
  -> 保存 Binding
  -> 校验外部对象状态
```

如果落到 Dittofeed，对应才是：

```text
进入 push scene 详情页
  -> 选择 dittofeed_journey_id
  -> 拉取 journey 摘要
  -> 保存 binding
  -> 校验 journey 状态
```

这里不要重新建本地 `Strategy` 编辑器。

### 4.4 Preview / Dry Run

抽象动作：

```text
选择 DecisionPoint
  -> 选择 Strategy 或 Binding
  -> 输入模拟事件或历史样本
  -> 执行预览
  -> 查看命中、拦截、引用有效性和 ExecutableTarget
```

如果落到 SmartPopup，对应才是：

```text
选择 Scene
  -> 选择 Recipe
  -> 输入模拟事件
  -> 执行 Dry Run
  -> 查看命中、fatigue、cms 引用和 resolve 结果
```

### 4.5 发起 PublishOrder

抽象动作：

```text
选择发布范围
  -> 组装 snapshot
  -> 校验 Binding
  -> 提交审批
  -> 发布到目标环境
  -> 记录 AuditLog
```

如果落到 SmartPopup，对应才是：

```text
选择 Scene / Recipe 变更
  -> assemble snapshot
  -> 校验 GrowthBook / CMS
  -> staging
  -> approval
  -> prod publish
```

## 5. SmartPopup 这类实现名应该如何使用

在这类 IA 文档里，推荐始终使用两层表达：

- 第一层写抽象概念：`DecisionPoint / Strategy / *Binding / PublishOrder`
- 第二层再补实现名：`Scene / Recipe / cms_id / publish order`

原因是：
- 这样 `admin-ia-and-flows` 能和 `domain-model.md` 强对齐
- 也能避免把某个项目的术语误当成通用后台模型

因此，后续如果要再补触点、Push、差评拦截这类案例，也都应先回到抽象概念，再映射到各自落地名。
- 验证 `Strategy`
- 验证 `Binding`
- 解释 `ExecutableTarget`

### 6.5 发起发布

```text
选择发布范围
  -> 组装 snapshot
  -> 校验 GrowthBook / CMS / DeliveryRef
  -> 创建 PublishOrder
  -> 发布到 staging
  -> 审批
  -> 发布到 prod
```

对应建模动作：
- 冻结 `PublishOrder`
- 将 `DecisionPoint / Strategy / Binding` 收敛成发布态结果

### 6.6 回滚

```text
查看历史 PublishOrder
  -> 选择回滚目标
  -> 二次确认
  -> 重新发布历史 snapshot
  -> 记录 audit
```

对应建模动作：
- 使用旧 `PublishOrder` 重新激活发布态结果
- 补充新的 `AuditLog`

## 7. SmartPopup 作为当前样例

从 `customer-care` 当前实现可直接抽出的可复用经验：

- 一级结构可以从 `facts / scenes / publish / audit` 起步
- `Scene 详情页 + inline Strategy 编辑器` 很适合 Admin-native 规则对象；在 SmartPopup 中该 Strategy 落成 Recipe
- Dry Run 贴近编辑页，有助于减少“改完再跳去别处验证”的割裂
- Publish 采用 `staging -> approval -> prod` 是很稳的默认模式
- deep-link 到 GrowthBook 和 CMS 比在 Admin 里硬复制外部系统更稳

如果映射回抽象模型，可直接读成：
- `Scene` = `DecisionPoint`
- `Recipe` = `Strategy`
- GrowthBook / CMS 深链和状态校验 = `*Binding`
- Publish / Audit = `PublishOrder / AuditLog`

所以这份 IA 不是独立的产品稿，而是领域模型的一种 UI 投影。

## 8. 反模式

### 8.1 按表名堆页面

后果：
- 用户看不出主工作流
- 高频操作被切碎

### 8.2 把 push 做成和 popup 完全一样的详情页

后果：
- 会复制 Dittofeed 主权
- 让用户误以为 Admin 才是 trigger / fatigue 的真实来源

### 8.3 只做 deep-link，不做状态镜像和校验

后果：
- Admin 退化成链接集合
- 无法支撑发布前校验

## 实战补充模式 (from Engagement Admin)

以下模式是 Engagement Admin 在实际落地过程中沉淀出的可复用 UX 约定，适用于任何运营台。每条都是一句可直接 copy 的设计规则，不展开讲原理。

### 1. List-page tabs by type

当 DecisionPoint 天然分 2–3 个离散类型（例如 `FEATURE_GATE` vs `PROACTIVE`）时，用 Tab 而不是堆叠分组区块承载。当前激活 tab 必须镜像到 URL `?tab=<key>`，用 `router.replace` 而不是 `push`，避免污染浏览器后退栈。每个 tab 触发器旁渲染计数。这样列表页既可深链也可分享。

### 2. URL sync for client-side filters

任何服务端一次性返回全集、由客户端做筛选的页面（如 Audit、Dependencies）都必须把筛选状态镜像到 URL。标准写法：`useEffect` 监听筛选 state → 拼 `URLSearchParams` → `router.replace(qs ? pathname + '?' + qs : pathname, { scroll: false })`。处于默认值的 key 要省略，保持 URL 干净。

### 3. Two-panel navigation around an action

标准布局：顶部 header nav + 左侧 AppSidebar + 面包屑 + 右上 user-menu。user-menu 必须走真实的 `/api/me` 接口拿当前用户，不要硬编码。登出按钮一定是下拉里的一个 POST form，不是 GET link——否则 tab 预取会误触发登出。

### 4. No dead buttons

没有 `onClick` / `render={<Link/>}` / `type="submit"` 的 `<Button>` 就是 UX bug。必须替换成链接、接 API 或直接删。Engagement v1 的反例：「创建发布单」渲染出来但没挂 handler；「归档」「发布到 prod」「回滚」「取消发布单」全都作为死按钮上线，直到 phase-2 才补齐。

### 5. Copy-button UX for cross-system strings

任何会被运营同学粘贴到别的系统的字符串（`variantKey` / feature id / checksum 等），旁边都要挂一键复制按钮。复制的是这根原字符串本身，不是 JSON 结构体。

### 6. Deep-link from admin to every external system it integrates with

Admin 集成的每一个外部系统（CMS entry、GrowthBook feature、paywall 配置等）都要在绑定的标识符旁渲染一个 inline 的 `Open in <system>` 链接。反向导航不做硬性要求，见 `cross-system-variant-key.md` 里 bookmarklet 方案。

### 7. 422 validation error surfaces as inline field errors

后端 POST/PATCH 返回 `{ error: { fieldErrors: { ... } } }` 形式的 422 响应时，前端 drawer 的 submit handler 解开 `fieldErrors` 到 `setErrors()` state，按 input 映射渲染成字段下方的红字，而不是一个笼统的 toast。这样可以彻底消除「[object Object]」这种降级 toast。

### 8. Self-healing test fixtures

Playwright 的 `globalSetup` 要主动归档上一次崩溃跑残留的 `e2e_*` 前缀 touchpoint。这样 E2E 测试天然顺序无关、可重复执行，不会因为前一轮脏数据失败。

## 测试分层与依赖边界矩阵 (from customer-care)

ops console 的测试策略核心 artifact 不是"写多少用例"，而是**把每一层允许 mock 哪些依赖、必须用真实哪些依赖显式列成一张表**，否则 L3 会逐渐被稀释成"L2 套个浏览器壳"。customer-care `docs/testing/services/admin/strategy.md` 的做法可直接抄，四层划分如下：

| Layer | 目标 | 工具 | 依赖 | 真实 or mock | 反馈速度 | 触发时机 |
|-------|-----|------|------|-------------|---------|---------|
| L1 unit | 保护 schema / helper / 纯函数不变量 | Vitest | 自己 | 全真（函数本身就是被测对象） | 秒级 | 每次提交 |
| L2 integration | 验证 API route / repo layer / 发布装配 | Vitest + test DB | DB + filesystem | DB 真、外部系统 mock | 分钟级 | MR |
| L3 smoke (local) | 浏览器黑盒，覆盖发布闭环与 drift detection | Playwright + docker-compose | GB / Payload / MySQL / S3-compatible | 全真但容器化本地启动 | 分钟级 | MR / nightly |
| L4 staging | 真实 staging 环境验收 | QA / Playwright / 手工 | 真实 staging 集群 + 真实外部系统 | 全真 | 小时级 | 每周发布前 |

### 依赖边界表

每一层对"外部依赖"的处理方式必须显式声明。customer-care 的 admin 策略矩阵（节选）：

| 依赖 | L1 | L2 | L3 | L4 |
|------|----|----|----|----|
| MySQL / Prisma | mock | real | real | real |
| GrowthBook | mock | mock / contract | **real**（docker-compose 起 GB 官方镜像） | real |
| S3-compatible | mock | mock | **real**（MinIO） | real |
| Feishu / 审批系统 | mock | mock | stub | real |
| Dagster / 离线 orchestrator | mock | mock | stub / deferred | real |

三条方法论原则：

1. **L3 禁止 mock Admin API、GrowthBook、S3 publisher、Prisma**。只允许对"不可控系统"（飞书、Dagster）用 stub。违反这条就是 L2 套 Playwright，不是 L3。
2. **L3 的真实依赖栈由 `docker-compose.yml` 管理**，不要让测试自己 spawn 野进程。customer-care 最小 L3 栈：`mysql + mongodb + growthbook + minio`。`make dev` 和 `make test-l3-admin*` 共享同一份 compose。
3. **每一层对应一个 Makefile target**，跨团队可复用。customer-care：
   - `npm test` — L1 + L2 route
   - `make test-l3-admin-smoke` — L3 关键路径 smoke（MR 阻塞）
   - `make test-l3-admin` — L3 全量（nightly）
   - `make test-l4-admin` — L4 staging 验收（release）

### 什么允许、什么不允许

| 能力 | L1 | L2 | L3 | L4 |
|------|----|----|----|----|
| 碰网络 | 禁止 | 允许 mock 层 HTTP | 允许 container 内部 | 允许真实公网 |
| 写 DB | 禁止 | 允许 test DB | 允许 compose DB | 允许 staging DB |
| 写外部 API（GB / CMS） | 禁止 | mock 侧 contract | **真写** container 实例 | 真写 staging |
| 共享 state 跨测试 | 禁止 | 禁止 | 允许但需 globalSetup 归档 | 允许但需清理 |
| 依赖外网 | 禁止 | 禁止 | 禁止（compose 内置） | 允许 |

**"L3 禁止依赖外网" 是一条硬约束**：外网抖动会污染 CI 信号，CI 绿灯不该取决于今天 GB SaaS 是否可用。正确做法是把 GB / Payload / S3 全部用自托管镜像（`growthbook/growthbook`、`minio/minio`）进 compose。

### 向 engagement 反向指导

engagement 目前做到了 L1 + L3，没有系统性的 L2。补 L2 的收益：

- API route 的 request/response schema 不回退 —— L2 route 契约测试能在不起容器的前提下秒级跑完。
- Publish assembler 的装配逻辑复杂，L2 用真实 DB + mock GB 能快速发现装配边界 bug。
- 本矩阵照抄即可，只把 "GrowthBook / MinIO" 换成 engagement 的具体外部依赖列表。

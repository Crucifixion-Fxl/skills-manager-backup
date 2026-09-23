# Issue Template — 5 段落标准模板

## 模板

```markdown
## 背景

[为什么有这个 issue。链接上游设计文档 / User Story / 事故报告 / 决策记录。
2-5 句话说清楚 "不做会怎样"。]

## 范围 (Scope)

[具体要做什么。列表或表格形式。不要写背景，不要写实现细节，只写交付物。]

- 要做 A
- 要做 B
- 不做 C（明确排除）

## 依赖

- 阻塞于 #N（如适用）
- 外部依赖：[团队 / 数据 / 运维 / 合作方]
- 无依赖（如适用，也要显式写）

## 验收 (Definition of Done)

- [ ] 代码合并到 master 且部署到目标环境
- [ ] 单元测试覆盖 core logic
- [ ] 文档更新（架构 / 用户手册）
- [ ] [功能特定的验证标准]

## 相关文档

- 设计文档: [link]
- 架构文档: [link]
- MR: [会在 comment 里追加]
```

## 每段内容指南

### 背景 — 写 "Why"

- 引用上游决策（设计文档 / US / 事故 / RFC）
- 不重复已经在上游文档里写过的细节，链接过去就行
- 如果没有上游文档，需要在这里把业务价值说清楚（可能说明设计环节欠缺）

**反模式：** 背景直接写实现 — "我们要在 API 加一个 /metrics 端点"。这是范围，不是背景。背景是 "为什么需要 metrics 端点"。

### 范围 — 写 "What"

- **列具体交付物**，不是实现步骤
- 每条可独立验证
- 明确**不做什么**（排除范围），避免 scope creep

**反模式：** 把技术实现步骤写在范围里。实现怎么做归 MR / 代码 review，issue 只说做什么。

### 依赖 — 写阻塞关系

- 有则明列，无则写 "无"（显式表达比缺段更好）
- 依赖 issue 要在 API 层加 `relates_to` link（见 [linked-items.md](linked-items.md)）
- 阻塞外部（数据 / 运维）时，写清对接人或对接渠道

**反模式：** 依赖只在评论里提。依赖关系是 issue 的一等属性，读描述就该看到。

### 验收 — 可勾选 checklist

- `- [ ]` Markdown checkbox，GitLab 会渲染成交互式勾选
- 每条独立、可验证、不主观
- 覆盖：代码、测试、部署、文档、监控（如适用）

**反模式：**
- "功能正常" — 不可验证
- "review 通过" — 这是过程不是 DoD
- 没有 checklist — issue close 时无据可依

### 相关文档 — 跨链接

- 设计文档、架构文档、User Story
- MR 链接在 comment 中追加（见 [progress-comments.md](progress-comments.md)）
- 不重复描述，仅指向权威来源

## 完整示例（SmartPopup #19 风格）

```markdown
## 背景

SmartPopup 埋点事件 `smart_popup_*`（impression / interaction / exposure / feedback_submit）尚未在 Tracker Manager 注册。
埋点不注册会导致：
1. 数仓 ETL 无法把原始事件流归档到 `dwd_*` 表
2. GrowthBook 实验分析无法聚合到这些事件
3. 数据合规审计缺失

上游设计见 [observability data-pipeline design](docs/plans/2026-04-10-smart-popup-observability-data-pipeline-design.md)。

## 范围 (Scope)

在 Tracker Manager 注册 4 个 SmartPopup 埋点事件：

| 事件名 | 说明 |
|--------|------|
| `smart_popup_impression` | 弹窗展示 |
| `smart_popup_interaction` | 用户点击 CTA / 关闭 |
| `smart_popup_exposure` | 引擎决策日志（不一定触发展示） |
| `smart_popup_feedback_submit` | 反馈表单提交 |

每个事件需包含字段: `recipe_id, scene_id, device_sn, ts_utc, client_version`。

不做:
- 事件上报 SDK 代码（归 SDK 仓库的 MR）
- dwm 宽表建设（#20 处理）

## 依赖

- 无阻塞依赖
- 对接人：数据团队 @zhangsan

## 验收 (Definition of Done)

- [ ] 4 个事件在 Tracker Manager 审核通过并发布
- [ ] Schema 定义含 5 个必填字段
- [ ] 测试环境事件上报可落到 `dwd_smart_popup_*` 表
- [ ] 文档更新：docs/architecture/smart_popup/observability.md 列出埋点 schema

## 相关文档

- 设计文档: [observability data-pipeline](../docs/plans/2026-04-10-smart-popup-observability-data-pipeline-design.md)
- Tracker Manager: https://us-analytics-management.theunismart.com
```

## 反模式

### 标题不清晰

- 坏：`埋点` / `可观测性改进`
- 好：`在 Tracker Manager 注册 4 个 smart_popup_* 埋点事件`

规则：标题 = 动词 + 对象 + 限定。读标题能知道做什么。

### 没有验收 checklist

close 时无依据。每个 issue 必须有 DoD 段，哪怕只有一条。

### 依赖只藏在评论里

依赖关系是 issue 一等属性。放到描述里的专门段，别人打开 issue 一眼看到阻塞情况。

### 范围写成实现步骤

- 坏：`1. 新建 /metrics 路由；2. 调用 prometheus_client；3. 写单测`
- 好：`在 API 暴露 Prometheus /metrics 端点，包含 http_requests_total 和 http_duration_seconds`

实现步骤归 MR，issue 只说产出。

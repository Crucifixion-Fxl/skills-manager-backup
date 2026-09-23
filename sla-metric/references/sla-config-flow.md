# SLA 指标配置端到端流程

## 概述

本文档描述 SLA 指标监控配置的完整流程，覆盖新增、修改、查看/搜索三个场景。

## Step 0: 权限校验（所有操作前必须完成） {#权限校验}

```
0.1 Superset 权限验证
    - 要求用户提供 Superset 凭据（用户名/密码）
    - 调用 Superset login 接口验证 → 获取 JWT Token
    - 失败时：提示检查凭据或联系管理员

0.2 dapp API 权限验证
    - 如用户已配置环境变量 DAPP_TOKEN → 直接使用
    - 否则要求用户提供用户名（邮箱），调用 POST /api/v1/auth/token/session 生成会话 Token
    - 调用 POST /api/v1/sla_metric/list 验证 Token 可用性
    - 失败时：检查用户名是否正确、Token 是否过期

✅ 两个系统都验证通过后才允许进入工作流
```

---

## 新增 SLA 指标 {#新增}

### 流程总览

```
[Step 0] 权限校验
  → [Step 1] 确认/创建指标图表（Superset）
  → [Step 2] 自定义过滤条件（可选）
  → [Step 3] 创建 SLA 指标（dapp API）
  → [Step 4] 确认并后续操作
```

### Step 1: 确认指标图表（Superset）

确保目标指标在 Superset 中有对应图表。详细操作参见 [superset-ops.md](superset-ops.md)。

```
1. 与用户确认监控的指标是什么
2. 使用 list_charts 搜索是否已有相关图表
3. 如已有 → 确认图表满足 SLA 要求（见 superset-ops.md）
4. 如没有 → 与用户确认数据源，创建数据集和图表（名称以 sla_metric_ 为前缀）
5. 获取图表链接：https://superset-us.addx.live/explore/?slice_id={id}
```

### Step 2: 自定义过滤条件（可选，Superset）

如用户需要自定义过滤条件但不改变图表已保存状态：

```
1. 在 Superset 图表中修改过滤条件
2. 点击【Update chart】（注意：不要点保存图表）
3. 复制当前 URL（包含新的 form_data_key 参数）
4. 在 dapp 配置时使用此链接的过滤条件作为快照
```

### Step 3: 创建 SLA 指标（dapp API）

通过后端 API 完成 SLA 指标创建：

```
3.1 查询分类选项
    - 调用 GET /api/v1/sla_metric/biz_domains 获取业务域列表
    - 调用 GET /api/v1/sla_metric/biz_processes?biz_domain_id={id} 获取业务过程列表
    - 调用 GET /api/v1/sla_metric/app_domains 获取应用域列表
    - 展示给用户选择

3.2 构建配置
    - name: SLA 指标唯一标识（如 sla_daily_order_count）
    - title: 显示名称（如 每日VIP订单增量）
    - time_grain: 时间粒度（hour / day / week）
    - metric_config: 从 Step 1/2 的图表信息构建（JSON 字符串）
    - threshold_config: 与用户确认阈值设置（JSON 字符串）
    - biz_domain_id / biz_process_id: 用户选择的分类
      → 无匹配分类时，提示用户前往 dapp Web UI 新增
    - app_domain_id: 可选，建议填写

3.3 展示配置摘要，获得用户确认

3.4 调用 POST /api/v1/sla_metric/create 创建指标
```

### Step 4: 确认并后续操作

```
1. 创建成功后，可选配置飞书通知群：
   - 调用 GET /api/v1/sla_metric/feishu_group_chats 获取可用群列表
   - 调用 POST /api/v1/sla_metric/update 设置 feishu_group_chats 字段
   - 不设置则使用业务域默认群
2. 输出详情页链接：https://dapp.addx.live/sla_metric?sub=metric&id={Id}&action=view
3. 提示用户在详情页点击【构建 Grafana 资源】生成监控看板和告警规则
```

---

## 修改 SLA 指标 {#修改}

### 流程总览

```
用户提出修改需求
  → [dapp API] 定位目标 SLA 指标
  → [dapp API / Superset] 查看当前配置
  → [dapp API] 调用 POST /update 更新指标
  → [dapp Web] 必要时重新构建 Grafana 资源
```

### 操作步骤

```
1. 定位指标
   - 调用 POST /api/v1/sla_metric/list 搜索（关键字/业务域/应用域）
   - 或调用 GET /api/v1/sla_metric/get?name={name} 按名称查询
   - 或由用户提供指标名称/ID

2. 查看当前配置
   - 使用 GET /get 获取完整配置（/list 不含配置字段）
   - 如需确认 Superset 侧配置，使用 MCP 工具查询图表详情

3. 更新指标
   - 构建更新 payload（仅包含需要修改的字段）
   - 展示修改前后的差异摘要
   - 用户确认后调用 POST /api/v1/sla_metric/update

4. 后续操作
   - 输出详情页链接：https://dapp.addx.live/sla_metric?sub=metric&id={Id}&action=view
   - **修改了 metric_config 或 threshold_config 时，必须提示用户重新构建 Grafana 资源**（幂等操作）
   - 仅修改 title、分类等非核心字段时无需重新构建
```

### 边界情况

- **无权修改：** 只有 `owners` 中的用户可修改，否则 API 返回错误。提示用户联系 owners 操作
- **业务域/应用域不存在：** 提示用户前往 [dapp Web UI](https://dapp.addx.live) 新增分类
- **创建失败 `SLA指标已存在`：** name 重复，需换一个唯一 name
- **更新失败 `SLA指标不存在`：** 检查 name 拼写是否正确

---

## 查看/搜索 SLA 指标 {#查看搜索}

### 流程总览

```
用户提出查看/搜索需求
  → [dapp API] 搜索 SLA 指标
  → [dapp API] 查看指标详情
  → [Superset] 可选：验证指标数据
```

### 操作步骤

```
1. 搜索指标
   - 调用 POST /api/v1/sla_metric/list 搜索
   - 支持：关键字搜索（name/title 模糊匹配）、业务域筛选、应用域筛选

2. 查看详情
   - 调用 GET /api/v1/sla_metric/get?name={name} 获取完整配置
   - 查看内容：
     • 基础信息（名称、应用域、业务域）
     • 阈值配置
     • 监控维度设置
     • 指标趋势看板（指标看板、版本看板、灰度看板）
     • Grafana 面板链接

3. 数据验证（可选）
   - 如用户需要验证指标数据准确性
   - 使用 mcp__superset-mcp__execute_sql 执行查询
   - 对比 SQL 结果与 SLA 指标展示值

4. 看板模式
   - 入口：SLA 指标列表页右上角【SLA看板】
   - 三种看板：
     • 指标看板：所有 SLA 主指标趋势
     • 版本看板：各版本指标趋势
     • 灰度看板：主指标与灰度指标的偏离关系
```

---

## Agent 协作模式

当执行 SLA 配置时，Agent 可实现全自动化：

```
主 Agent（编排）
  ├── Superset 操作：使用 Superset MCP 工具查询/创建图表
  ├── 权限校验：调用 auth API 获取 Token
  ├── 分类查询：调用 GET /biz_domains、/biz_processes、/app_domains
  ├── 指标管理：调用 POST /create 或 POST /update
  └── 飞书群配置：调用 GET /feishu_group_chats + POST /update
```

**关键协作点：**
- Superset 操作通过 MCP 工具自动完成
- dapp 指标 CRUD 通过后端 API 自动完成
- 仅「构建 Grafana 资源」和「删除」需要用户在 Web UI 手动操作
- 引导时应提供具体的参数值（如阈值、应用域），减少用户手动输入

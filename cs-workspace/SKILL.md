---
name: cs-workspace
description: 客服工作台 (CS Workspace) API 操作助手。通过 REST API 完成客服工单管理、用户问题排查、FAQ 知识库维护、邮件营销、设备查询、AI 智能代理等任务。当用户提到客服工单、support ticket、CS 工作台、cs.addx.live、工单搜索、FAQ 管理、邮件模板、设备排查、AI 客服代理，或任何与客户服务运营相关的查询时使用此 Skill。
---

# cs-workspace

客服工作台 (cs.addx.live) 的 API 操作助手。系统基于 FastAPI (Python) 构建，前端使用 React + MUI，提供完整的 OpenAPI 3.1 规范。

## Description

CS Workspace 是公司内部的客服运营平台，整合了多个核心功能模块：

| 模块 | API 前缀 | 核心功能 |
|------|----------|---------|
| 工单管理 | `/api/v1/ticket/` | Zendesk 工单同步、批量处理、自动化触发、评论队列 |
| 工单搜索 (ES) | `/api/v1/es/` | ElasticSearch 全文检索工单、标签建议、问题分类 |
| FAQ 知识库 | `/api/v1/faq/` | FAQ 创建/审核/发布、聚类分析、相似 FAQ 推荐 |
| FAQ 生成 | `/api/v1/faq-generation/` | AI 自动生成 FAQ、合并、参数确认 |
| 邮件管理 | `/api/v1/email/` | 邮件账户(含 OAuth)、模板(多语言/版本)、收件人组、发送任务 |
| AI 助手 | `/api/v1/ai/` | AI 对话、模型选择 |
| 智能代理 | `/api/v1/agents/` | 自定义 AI Agent (支持 MCP)、代理模板 |
| 数据库查询 | `/api/v1/db/` | 设备查询、用户查询、SQL 模板管理、多环境支持 |
| 设备查询 | `/api/v1/device/` | 设备画像查询 |
| MCP 任务 | `/api/v1/mcp_tasks/` | SOP 管理、任务执行器、流式执行、执行历史 |
| 管理后台 | `/api/v1/admin/` | 用户/组管理、权限管理、工具权限、活动日志、Celery 监控 |
| LLM 统计 | `/api/v1/llm-usage/` | 用量统计、配额管理、对话设置 |
| 系统管理 | `/api/v1/system/` | Embedding 模型管理、健康检查 |

## Rules

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `CS_WORKSPACE_URL` | 服务地址，默认 `https://cs.addx.live` | 是 |
| `CS_WORKSPACE_TOKEN` | Bearer Token (JWT) | 是 |

### 认证方式

系统使用 OAuth2 Password Bearer 认证，Token 存储在 `localStorage("token")` 中。

```bash
# 获取 Token（用户名密码登录）
curl -X POST "$CS_WORKSPACE_URL/api/v1/auth/token" \
  -d "username=YOUR_USERNAME&password=YOUR_PASSWORD" \
  -H "Content-Type: application/x-www-form-urlencoded"
# 返回: {"access_token": "eyJ...", "token_type": "bearer"}

# 后续请求携带 Token
curl -H "Authorization: Bearer $CS_WORKSPACE_TOKEN" "$CS_WORKSPACE_URL/api/v1/auth/me"
```

也支持 JSON 格式登录：`POST /api/v1/auth/login`。

### API Reference

完整 OpenAPI 3.1 规范：`https://cs.addx.live/api/v1/openapi.json`

> **重要**：直接访问上述 URL 获取最新 API 定义，不要下载到本地。Swagger UI 地址为 `https://cs.addx.live/api/docs`（需浏览器渲染）。

### 操作流程

#### 工单搜索与排查

1. 搜索工单：`GET /api/v1/es/tickets/search?q={keyword}&size=10`
   - 支持参数：`created_at_start/end`、`updated_at_start/end`、`requester_id`、`tags`、`issue_category_tag`、`issue_category_hierarchy`、`comment_turn_count_min/max`
2. 获取工单详情：`GET /api/v1/es/tickets/{ticket_id}`
3. 从 Zendesk 拉取最新：`GET /api/v1/ticket/fetch/{zendesk_id}`
4. 获取标签建议：`GET /api/v1/es/tickets/suggest/tags?prefix={prefix}&size=25`
5. 获取问题分类：`GET /api/v1/es/tickets/suggest/structured-issue-categories`

#### 工单批量处理

1. 启动批量任务：`POST /api/v1/ticket/start`
2. 查看任务状态：`GET /api/v1/ticket/batch/status/{task_id}`
3. 暂停/恢复评论队列：`POST /api/v1/ticket/pause` / `POST /api/v1/ticket/resume`
4. 停止任务：`POST /api/v1/ticket/stop`
5. 触发工单自动化：`POST /api/v1/ticket/automation/trigger`

#### FAQ 知识库管理

1. 搜索 FAQ：`POST /api/v1/faq/search`
2. 按状态查询：`GET /api/v1/faq/by-status/{status}`（分页）
3. 查看 FAQ 详情：`GET /api/v1/faq/{faq_id}`
4. 创建 FAQ：`POST /api/v1/faq/`
5. 审核流程：`POST /api/v1/faq/{faq_id}/approve` → `/confirm` → `/reject`
6. FAQ 聚类分析：`POST /api/v1/faq/cluster-analysis`
7. 查找相似 FAQ：`GET /api/v1/faq/{faq_id}/similar`

#### AI 生成 FAQ

1. 第一步生成：`POST /api/v1/faq-generation/step1`
2. 确认参数：`POST /api/v1/faq-generation/confirm_parameters`
3. Celery 异步生成：`POST /api/v1/faq-generation/generate`
4. 查看状态：`GET /api/v1/faq-generation/status/{task_id}`
5. 合并 FAQ：`POST /api/v1/faq-generation/merge`

#### 设备与用户查询

1. 设备画像：`POST /api/v1/device/figure`（传入设备标识）
2. 设备详情：`POST /api/v1/db/device/details`
3. 用户查询：`POST /api/v1/db/user-query`
4. 直接 SQL 查询：`POST /api/v1/db/direct-query`（需权限）
5. 查看可用环境：`GET /api/v1/db/environments`

#### 邮件管理

1. 查看邮件账户：`GET /api/v1/email/accounts`
2. 管理模板（含多语言翻译和版本控制）：`GET/POST /api/v1/email/templates`
3. 管理收件人组：`GET/POST /api/v1/email/recipient-groups`
4. 创建发送任务：`POST /api/v1/email/tasks`
5. 查看发送进度：`GET /api/v1/email/tasks/{task_id}/progress`

### 公司规则

#### 操作红线
- **禁止**直接执行未经确认的 SQL 查询（`/db/direct-query`），必须先展示 SQL 给用户确认
- **禁止**在未确认的情况下触发工单自动化（`/ticket/automation/trigger`）
- **禁止**删除生产环境的 FAQ（仅可标记为 reject 状态）
- 批量工单操作前必须告知用户影响范围并等待确认

#### 权限模型
- 普通用户：工单搜索、FAQ 查看、AI 对话、设备查询
- 管理员：用户/组管理、工具权限配置、SQL 直接查询、Celery 监控
- 工具权限通过 `GET /api/v1/tools/available` 获取当前用户可用工具列表

#### 数据安全
- 用户查询结果中的手机号、邮箱等 PII 信息展示时需脱敏
- SQL 查询结果限制返回行数，避免大数据量导出

## Examples

### Bad

```
用户：帮我删掉所有过期的 FAQ
AI：好的，我直接批量删除...
# 错误：禁止未经确认的批量删除操作
```

```
用户：查一下设备 ABC123 的信息
AI：curl -X POST "$CS_WORKSPACE_URL/api/v1/db/direct-query" -d '{"sql": "SELECT * FROM devices"}'
# 错误：应使用专用的设备查询端点，不应直接 SQL 查询全表
```

### Good

```
用户：帮我搜索最近一周关于连接问题的工单
AI：curl -H "Authorization: Bearer $CS_WORKSPACE_TOKEN" \
  "$CS_WORKSPACE_URL/api/v1/es/tickets/search?q=connection+issue&size=20&created_at_start=2026-02-24&created_at_end=2026-03-03"
# 使用 ES 搜索端点，指定时间范围和关键词
```

```
用户：查一下设备 DEVICE_ID_123 的详细信息
AI：curl -X POST -H "Authorization: Bearer $CS_WORKSPACE_TOKEN" \
  -H "Content-Type: application/json" \
  "$CS_WORKSPACE_URL/api/v1/db/device/details" \
  -d '{"device_id": "DEVICE_ID_123"}'
# 使用专用设备查询端点
```

```
用户：帮我生成关于固件升级的 FAQ
AI：我来通过 FAQ 生成流程帮你创建：
1. 先调用 step1 分析固件升级相关工单...
2. 确认生成参数后再正式生成...
3. 生成完成后你可以审核并决定是否发布。
# 遵循多步骤生成流程，每步确认
```

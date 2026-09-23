---
name: feishu-project
description: Use when interacting with Feishu Project (飞书项目) APIs — creating/querying/updating work items, managing workflows, searching with MQL, configuring spaces, or needing Feishu Project authentication. Triggers on keywords like 飞书项目、工作项、需求、缺陷、迭代、MQL、meego.
---

# feishu-project

飞书项目（Meego）操作技能，支持 Plugin Token（Python Client / HTTP API）和 MCP 两条执行路径。

## Description

| 项目 | 说明 |
| --- | --- |
| 执行路径 | Plugin Token（HTTP API 直调）+ MCP 双路径 |
| Plugin 鉴权 | Plugin Token 静默鉴权，无需用户交互 |
| MCP 鉴权 | OAuth 浏览器授权 |
| Plugin Base URL | `https://project.feishu.cn/open_api/` |

**不适用：** 飞书文档/多维表格/消息等非项目管理 API（使用 lark skill）

## Rules

### Rule 1 — 路径选择

本 skill 支持两条执行路径，按以下规则选择：

```
检查环境变量 FEISHU_PLUGIN_ID / FEISHU_PLUGIN_SECRET / FEISHU_USER_KEY 是否完整
  ├─ 完整：
  │   ├─ Python Client 支持的能力 → 优先 Plugin
  │   └─ Python Client 不支持但 MCP 支持 → 走 MCP
  └─ 不完整：
      ├─ MCP 可用 → 走 MCP
      └─ MCP 不可用 → 提示配置环境变量
如果目标能力两条路径都不支持 → 明确提示不支持
```

**能力矩阵：**

| 能力 | Plugin | MCP | 策略 |
| --- | :---: | :---: | --- |
| 空间列表/详情 | Y | Y | 有环境变量优先 Plugin |
| 工作项搜索（条件筛选） | Y | Y | 有环境变量优先 Plugin |
| 工作项详情/创建/更新 | Y | Y | 有环境变量优先 Plugin |
| 工作项删除/终止/冻结 | Y | - | 仅 Plugin |
| 工作流/节点操作 | Y | Y | 有环境变量优先 Plugin |
| 评论 | Y | Y | 有环境变量优先 Plugin |
| 子任务创建/删除 | Y | - | 仅 Plugin |
| 子任务更新 | Y | Y | 有环境变量优先 Plugin |
| 附件上传/下载/删除 | Y | - | 仅 Plugin |
| 工时记录读取 | Y | Y | 有环境变量优先 Plugin |
| 工时记录写入 | Y | - | 仅 Plugin |
| 评审结论 | Y | Y | 有环境变量优先 Plugin |
| 交付物查询 | Y | Y | 有环境变量优先 Plugin |
| 用户查询 | Y | Y | 有环境变量优先 Plugin |
| 用户组管理 | Y | - | 仅 Plugin |
| 空间关联 | Y | Y | 有环境变量优先 Plugin |
| 字段/角色/配置查询 | Y | Y | 有环境变量优先 Plugin |
| MQL 搜索 | - | Y | 仅 MCP |
| 视图管理 | - | Y | 仅 MCP |
| 图表/度量 | - | Y | 仅 MCP |
| 团队列表 | - | Y | 仅 MCP |
| 排期查询 | - | Y | 仅 MCP |
| 待办/已办 | - | Y | 仅 MCP |

**Plugin Token 环境变量：**

```bash
# ── 飞书项目 API（必需）──
export FEISHU_PLUGIN_ID="MII_63E9xxx82xxxx"
export FEISHU_PLUGIN_SECRET="D01B5F1xxx620D133xxxx"
export FEISHU_USER_KEY="731189198150710xxxx"

# ── 可选 ──
export FEISHU_PROJECT_KEY="your_project_key"
export FEISHU_OPEN_APP_ID="cli_xxxx"        # 飞书开放平台 API 所需
export FEISHU_OPEN_APP_SECRET="xxxx"         # 飞书开放平台 API 所需
```

注意：
- Plugin Token 有效期 2 小时，客户端自动缓存并在过期前刷新。但 **401 不会自动重试**（直接抛 `FeishuAuthError`，继承自 `FeishuApiError`），需调用方捕获后重新实例化 client 或手动调用。`FeishuTimeoutError` 单独用于 HTTP 超时（默认 30s，可在业务层重试）
- `X-USER-KEY` 决定数据访问范围，该用户必须对目标空间有权限
- 权限 = 插件权限 ∩ 空间安装插件 ∩ 代理用户权限
- MCP OAuth 通过 `mcp__feishu-project-mcp__authenticate` 或 `mcp__feishu-project__authenticate` 拉起浏览器授权（两个 MCP 前缀均可用，后者额外支持 `list_todo`）

### Rule 2 — Python Client 使用

`scripts/feishu_client.py` 封装了鉴权和所有 HTTP API 调用。

**type_key 自动解析**：所有接受 `work_item_type_key` 的方法都支持传 `api_name`（如 `"feedback"`、`"story"`）或实际的 `type_key`（如 `"65eae566afd3b8c58f85d808"`），客户端内部自动转换。优先用 `api_name`，更易读。

使用前先检查环境变量：

```python
from scripts.feishu_client import FeishuProjectClient, is_plugin_auth_available

if not is_plugin_auth_available():
    # 环境变量不完整，走 MCP 路径
    ...

client = FeishuProjectClient()  # 自动从环境变量读取凭证

# 空间
detail = client.get_project_detail(["project_key"])

# 搜索（注意：work_item_type_keys 是列表）
items = client.search_work_items("project_key", ["story"], page_size=50)

# 创建（template_id 通过 get_work_item_meta 获取，部分类型必传）
client.create_work_item("project_key", "story", template_id=145405865, fields=[
    {"field_key": "field_name", "field_value": "标题"},
])

# 评论（work_item_id 为字符串）
client.add_comment("project_key", "story", "6918187271", "评论内容")
```

**关键方法返回值结构：**

| 方法 | 返回类型 | 说明 |
| --- | --- | --- |
| `get_projects()` | `List[str]` | project_key 字符串列表，不是对象列表 |
| `get_project_detail([keys])` | `Dict[str, Dict]` | 以 project_key 为键，值含 `name`, `simple_name`, `administrators` |
| `get_work_item_types(pk)` | `List[Dict]` | 每项含 `type_key`, `name`, `api_name` |
| `get_field_config(pk, tk)` | `List[Dict]` | 每项含 `field_key`, `field_name`, `field_type_key`, `options` |
| `search_work_items(pk, [tk], **filters)` | `List[Dict]` | 工作项对象列表（非分页包装），每项含 `id`(int), `name`(str), `fields`, `sub_stage`(str), `current_nodes`, `work_item_status` |
| `get_work_item_detail(pk, tk, [ids])` | `List[Dict]` | 同上，工作项对象列表。**ids 必须是 `List[int]`**（传字符串会 400） |
| `get_work_item_meta(pk, tk)` | `Dict` | 含 `template_id`, `fields`（字段元数据） |
| `list_comments(pk, tk, wid)` | `List[Dict]` | 每项含 `content`(str), `id`, `created_at`, `creator` |
| `add_comment(pk, tk, wid, content)` | `Dict` | 创建的评论对象。wid 为字符串 |
| `get_workflow(pk, tk, wid)` | `Dict` | 含 `nodes` 列表，每个 node 有 `id`, `name`, `owners`, `state_key` |

**工作项对象结构（search / detail 返回的每一项）：**

```
{
  "id": 6953243072,           // 顶层：工作项 ID（int）
  "name": "标题",              // 顶层：标题
  "sub_stage": "WDQf4OUFH",   // 顶层：当前子阶段 ID
  "current_nodes": [           // 顶层：当前节点（含名称和负责人）
    {"id": "state_0", "name": "待FAE分析", "owners": ["user_key"]}
  ],
  "work_item_status": {...},   // 顶层：状态信息
  "fields": [                  // 字段数组：自定义字段在这里
    {"field_key": "field_xxx", "field_alias": "alias", "field_value": ...}
  ]
}
```

> `name`、`sub_stage`、`current_nodes` 是**顶层字段**，不在 `fields` 数组内。

**注意：** Windows 终端运行时中文可能乱码，建议在脚本开头加：
```python
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
```

### Rule 3 — 工作项操作流程

**创建工作项：**

1. `client.get_work_item_types(project_key)` → 获取 work_item_type_key
2. `client.get_work_item_meta(project_key, type_key)` → 获取模板 ID 和字段元数据
3. `client.create_work_item(project_key, type_key, template_id=xxx, fields=[...])` → 创建
   - template_id 从 meta 返回值获取，部分工作项类型必传
   - fields 中每项须使用 field_key（非字段名）和正确的值格式

**更新工作项字段：**

1. `client.get_work_item_detail(project_key, type_key, [work_item_id])` → 查当前值（参数为列表）
2. `client.update_work_item(project_key, type_key, work_item_id, fields=[...])` → 更新

**节点流转（节点流）：**

1. `client.get_workflow(project_key, type_key, work_item_id)` → 获取节点状态
2. `client.node_operate(project_key, type_key, work_item_id, node_id, action="confirm")` → 完成

**状态流转（状态流，如缺陷）：**

1. `client.state_change(project_key, type_key, work_item_id, transition_id)` → 流转

### Rule 4 — 字段值格式

**写入格式**（创建/更新工作项时）：

| 字段类型 | 写入格式 | 示例 |
| --- | --- | --- |
| text, number, bool, link | 字面值字符串 | `"测试"`, `"3"`, `"true"` |
| user | 单个 userkey | `"7509072868295085608"` |
| multi-user | userkey 数组 | `["key1","key2"]` |
| select, radio | 枚举 option_id | `"437794"` |
| multi-select | option_id 对象数组 | `[{"option_id":"111"}]` |
| multi-text | markdown 格式 | `"**bold** text"` |
| date | 毫秒时间戳 | `"1722182400000"` |
| schedule | 时间区间数组 | `[1722182400000,1722355199999]` |

**读取格式**（从 `fields[].field_value` 提取值时）：

| 字段类型 | 读取格式 | 提取方式 |
| --- | --- | --- |
| text, number, link | 字符串 | 直接取值 |
| select, radio | `{"label": "显示名", "value": "option_id"}` | 取 `.label` 获取显示名 |
| multi_select | `[{"label": "名称", "value": "id"}, ...]` | 遍历取每项 `.label` |
| multi_text | markdown 字符串 | 直接取值 |
| date | 毫秒时间戳（int） | `datetime.fromtimestamp(v / 1000)` |
| role_owners | `[{"role": "role_xxx", "owners": ["user_key"]}]` | 遍历匹配 `role` 后取 `owners` |

### Rule 5 — 分页

搜索类接口直接返回当前页的工作项列表（`List[Dict]`）。客户端 `_request()` 已剥离 HTTP 响应，只保留 `data` 字段，**分页元信息（如 total）不可见**。翻页方式：递增 `page_num`，直到返回空列表或长度小于 `page_size`。

```python
page = 1
all_items = []
while True:
    items = client.search_work_items("pk", ["story"], page_size=50, page_num=page)
    if not items:
        break
    all_items.extend(items)
    page += 1
```

支持分页的方法：`search_work_items`, `search_work_items_across`, `search_work_items_complex`, `query_user_group_members`

**`search_work_items` 常用 filter 参数（通过 `**filters` 透传）：**

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `sub_stages` | `List[str]` | 按子阶段 ID 筛选（如 `["WDQf4OUFH"]`）。注意：不支持角色过滤，需客户端自行从 `fields` 中 `role_owners` 字段筛选 |

> **获取 sub_stage ID**：从已知工作项的 `sub_stage` 顶层字段获取，或通过 `get_workflow()` 查看节点的 `state_key`

### Rule 6 — MQL 查询指引

MQL 搜索通过 MCP 工具 `search_by_mql` 调用，适合灵活查询。以下是避免踩坑的关键规则：

**字段名：**
- MQL 中字段名是 `field_name`（中文显示名），不是 `field_key`
- 大小写敏感：`工作项id`（小写）不是 `工作项ID`（大写）
- 所有字段名必须用反引号包裹：`` `字段名` ``
- 不确定字段名时，先用 `get_field_config(pk, tk)` 查 `field_name`

**常用 SELECT 字段：**

| 字段名 | 含义 | 类型 |
| --- | --- | --- |
| `工作项id` | 工作项 ID | long |
| `名称` | 标题 | varchar |
| `状态` | 工作流状态 | key_label |
| `当前负责人` | 当前节点负责人 | array(varchar) |
| `创建时间` | 创建时间 | date |
| `创建者` | 创建人 | varchar |
| `优先级` | 优先级 | varchar |

**角色查询：**
- 角色字段使用双下划线前缀：`` `__角色名` ``，如 `` `__FAE` ``、`` `__RD` ``、`` `__QA` ``
- 不确定角色名时，先从工作项详情的 `current_nodes` 或工作流节点名推断

**用户匹配：**
- 用 user_key 匹配用户：`'<id:7313853433780420609>'`
- 当前登录用户：`current_login_user()`（仅 MCP OAuth 登录时有效）

**MQL 示例：**

```sql
-- 查询某角色是指定用户的工单
SELECT `工作项id`, `名称`, `状态` FROM `空间名`.`工作项类型名`
WHERE `__角色名` = '<id:user_key>'

-- 按状态筛选
SELECT `工作项id`, `名称`, `状态` FROM `空间名`.`类型名`
WHERE `状态` = '待处理'

-- 节点负责人包含某人
SELECT `工作项id`, `名称` FROM `空间名`.`类型名`
WHERE array_contains(get_node_attribute('节点名','负责人'), '<id:user_key>')
```

**MQL 返回结构：**
```json
{
  "list": [{"group_infos": [...], "count": 11}],
  "session_id": "...",
  "data": {
    "1": [
      {"moql_field_list": [
        {"key": "work_item_id", "name": "工作项id", "value_type": "long_value", "value": {"long_value": 123}},
        {"key": "name", "name": "名称", "value_type": "string_value", "value": {"string_value": "标题"}},
        {"key": "work_item_status", "name": "状态", "value_type": "key_label_value_list",
         "value": {"key_label_value_list": [{"key": "state_0", "label": "待处理"}]}}
      ]}
    ]
  }
}
```
- `list[0].count` — 匹配总数
- `data["group_id"]` — 工作项数组，每项的 `moql_field_list` 包含请求的字段
- `session_id` — 翻页时传入，无需重新解析 MQL

### Rule 7 — 推荐工作流

首次对一个空间/工作项类型进行操作时，建议先执行以下探查：

```python
# 1. 确认空间
keys = client.get_projects()                     # → List[str]
detail = client.get_project_detail(keys)          # → 空间名、simple_name

# 2. 确认工作项类型
types = client.get_work_item_types(project_key)   # → type_key, name, api_name

# 3. 确认字段（MQL 字段名 = field_name，Python Client 字段 key = field_key）
fields = client.get_field_config(project_key, type_key)

# 4. 确认工作流节点和角色（从某个工作项实例获取）
workflow = client.get_workflow(project_key, type_key, work_item_id)
```

拿到 field_name / 角色名 / 节点名后再写 MQL 或构造 API 调用，避免猜测。

### Rule 8 — 常见错误

| 错误 | 原因 | 修复 |
| --- | --- | --- |
| 创建工作项失败 | 未传 template_id | 先调 get_work_item_meta 获取模板 ID，作为 template_id 参数传入 |
| 字段不匹配 | 用字段名而非 field_key | 先查字段配置获取 field_key |
| 401 Unauthorized | 环境变量未配置，或 Token 已过期但客户端未触发预刷新 | 抛 `FeishuAuthError`（`FeishuApiError` 子类）。检查环境变量；客户端仅在 Token 过期前**预刷新**，401 发生后**不自动重试**，需重新实例化 client 或在捕获 `FeishuAuthError` 后手动重试 |
| 请求超时 | 网络抖动或 API 网关慢 | 抛 `FeishuTimeoutError`。默认超时 30s；业务层可按需退避重试，不会自动重试 |
| 角色更新失败 | 创建用 role_owners，更新用 role_operate | 更新角色走单独的 role_operate 参数 |
| 评论 404 | work_item_type_key/work_item_id 是 URL 路径参数 | 路径：`/:pk/work_item/:tk/:wid/comment/create` |
| search_work_items 返回异常 | 传了字符串而非列表 | work_item_type_keys 参数必须为列表，如 `["story"]` |
| get_role_config 400 | work_item_id value invalid | 此接口不稳定，建议改从 `get_work_item_detail` 返回的 `fields` 中 `role_owners` 字段提取角色信息，或从 `get_workflow` 的 `nodes[].owners` 获取 |

## Examples

### Bad

```
search_work_items 传字符串而非列表：
client.search_work_items("pk", "story")
→ "story" 被逐字符迭代，应传 ["story"]
```

```
get_work_item_detail 传单个 ID 或字符串列表：
client.get_work_item_detail("pk", "story", "12345")
client.get_work_item_detail("pk", "story", ["12345"])
→ 第三参数应为 int 列表：[12345]（与代码签名 List[int] 一致）
```

```
创建工作项不查 meta：
client.create_work_item("pk", "story", fields=[...])
→ 缺少 template_id，必须先调 get_work_item_meta 获取
```

```
环境变量未配置时实例化 Client：
client = FeishuProjectClient()
→ 抛 EnvironmentError，应先 is_plugin_auth_available() 检查
```

```sql
-- MQL 字段名大小写错误：
SELECT `工作项ID` FROM `空间`.`类型`
→ attribute key or value error，正确写法是 `工作项id`（小写）
```

```python
# 把 get_projects() 返回值当 dict 处理：
projects = client.get_projects()
projects[0].get("name")   # AttributeError
# → 返回的是 List[str]（project_key 列表），不是对象列表
```

```python
# 把 search_work_items() 返回值当分页 dict 处理：
result = client.search_work_items("pk", ["story"])
result.get("total_count")  # AttributeError
# → 返回的是 List[Dict]（工作项列表），不是分页包装对象
```

### Good

```python
# 查询工作项（Plugin 路径）
types = client.get_work_item_types("project_key")  # 确认类型 key
items = client.search_work_items("project_key", ["story"], page_size=50)
detail = client.get_work_item_detail("project_key", "story", [12345])
```

```python
# 创建工作项（Plugin 路径）
meta = client.get_work_item_meta("project_key", "story")  # 获取模板和字段
client.create_work_item("project_key", "story", template_id=145405865, fields=[
    {"field_key": "field_name", "field_value": "需求标题"},
    {"field_key": "priority", "field_value": "437794"},
])
```

```python
# 环境变量未配置（MCP 路径）
if not is_plugin_auth_available():
    # 调 mcp__feishu-project-mcp__authenticate 授权
    # 然后用 MCP 工具操作
    pass
```

```sql
-- MQL 查询角色工单（仅 MCP）
-- 先确认角色名和用户 user_key，再构造 MQL
SELECT `工作项id`, `名称`, `状态` FROM `客户成功`.`客户反馈`
WHERE `__FAE` = '<id:7313853433780420609>'
```

## References

- [能力参考](references/api-reference.md) — HTTP API 端点 + MCP 能力映射
- [Python 客户端](scripts/feishu_client.py) — Plugin Token 鉴权的 Python 客户端

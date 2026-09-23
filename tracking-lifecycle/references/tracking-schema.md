# 埋点 Schema 规范

## Contents
- SPM 命名规范
- 事件类型枚举
- 追踪类型枚举
- 埋点设计文档 YAML Schema
- AI 评审红线清单

## SPM 命名规范

事件层级结构：Application → Page → Module → Component → SelfDefine

| 级别 | EventType 值 | 父级要求 | point 命名 |
|------|-------------|---------|-----------|
| Application | 0 | 无 | 由平台分配 |
| Page | 1 | applicationId | snake_case，如 store_page |
| Module | 2 | pageId | snake_case，如 product_detail |
| Component | 3 | pageId + moduleId | snake_case，如 buy_btn |
| SelfDefine | 4 | applicationId | snake_case，如 app_launch |

完整 SPM 格式：`{application_point}.{page_point}.{module_point}.{component_point}`

命名规则：
- 只使用小写字母、数字、下划线
- 语义清晰，反映业务含义
- 同一 Application 下 point 不可重复

### 命名约束详情

**正则校验**（前端 validator / 后端略有差异）：
```
前端: ^[a-z][a-z0-9]*(_[a-z0-9]+)*$   ← tracking-spec-validator.js（更严格，设计阶段用这个）
后端: ^[a-z]+([_a-z0-9]+)*$            ← ParamCheckUtil.java（允许尾部下划线，但不推荐）
```
- 必须以小写字母开头
- 只允许小写字母、数字、下划线
- 不能以数字或下划线开头
- 下划线后必须跟字母或数字（前端规则，推荐遵守）

**长度限制**：每个 point 字段最大 **255 字符**（数据库 `VARCHAR(255) NOT NULL`）

**唯一性约束**：应用层校验（非数据库唯一索引），按 `type + point + parentApplicationId + parentPageId + parentModuleId` 组合判重

**已通过工单发布的 point 不可修改**（R7）：
- 通过工单发布后的 point 是不可变的
- 如果旧 point 不符合 snake_case 命名，通过 `alias` 字段提供合规别名
- 新建事件的 `point` 和 `alias` 都必须符合 snake_case
- 编辑已有事件时，`point` 或 `alias` 至少一个符合 snake_case

**迁移策略**：需要改名时，创建新 point + 新事件，旧事件保留但停止使用（平台不支持 rename）

## 事件类型枚举 (EventType)

| 名称 | 值 | 说明 | 父级字段 |
|------|---|------|---------|
| APPLICATION | 0 | 应用根容器 | 无 |
| PAGE | 1 | 页面 | parentApplicationId |
| MODULE | 2 | 模块 | parentPageId |
| COMPONENT | 3 | 组件 | parentPageId + parentModuleId |
| SELF_DEFINE | 4 | 自定义事件 | parentApplicationId |

## 追踪类型枚举 (TrackerType)

| 名称 | 值 | 说明 | 典型场景 |
|------|---|------|---------|
| BASE | 0 | 基础 | 页面浏览 (PV)、模块展示 |
| CLK | 1 | 点击 | 按钮点击、链接点击 |
| EXP | 2 | 曝光 | 区域可见性曝光 |

EventType 与 TrackerType 的推荐组合（其他组合需人工确认）：
- PAGE + BASE: 页面浏览
- MODULE + BASE: 模块展示
- MODULE + EXP: 模块曝光
- COMPONENT + CLK: 组件点击
- COMPONENT + EXP: 组件曝光
- SELF_DEFINE + BASE/CLK: 自定义事件

## 埋点设计文档 YAML Schema

```yaml
# tracking-spec 格式定义
application: string       # 应用名称（如 vicohome, FAPP）
events:
  - name: string          # 事件中文名
    type: enum            # PAGE | MODULE | COMPONENT | SELF_DEFINE（只接受字符串名称，不接受数字值；APPLICATION 由平台管理，不可在此使用）
    tracker_type: enum    # BASE | CLK | EXP（只接受字符串名称）
    parent_page: string   # 父页面 point 名称（条件必填，见下表）。创建时需自行将 point 名称解析为平台数字 ID
    parent_module: string # 父模块 point 名称（条件必填，见下表）。同上
    point: string         # SPM 事件标识（snake_case）
    description: string   # 事件描述
    category: string      # 分类标签（可选，仅 PAGE/SELF_DEFINE）。有效值从平台获取：GET /api/eventTag/list?applicationId={id}
    base_schemas: list    # 继承的 base schema 名称（可选，如 ["user_info", "device_info"]）。有效值从平台获取：GET /api/baseSchema/list?applicationId={id}
    parameters:           # 事件参数列表
      - name: string      # 参数名（snake_case）
        value_type: enum  # string | integer | float | boolean | array | object
        is_required: bool # 是否必填
        description: string # 参数描述
```

### parent 条件必填矩阵

| type | parent_page | parent_module |
|------|-------------|---------------|
| PAGE | 不填 | 不填 |
| MODULE | 必填 | 不填 |
| COMPONENT | 必填 | 必填 |
| SELF_DEFINE | 不填 | 不填 |

注意：
- `type` 和 `tracker_type` 只接受上述字符串枚举值，不接受数字
- `APPLICATION (0)` 是平台内部类型，用户不可在 tracking-design.md 中使用
- `parent_page` / `parent_module` 使用 point 名称引用（非数字 ID），平台创建时由 AI 解析为 ID
- `category` 和 `base_schemas` 是平台托管的值，使用前需通过平台 API 查询可用列表（参见 [api-reference.md](api-reference.md)）

## AI 评审红线清单

| 编号 | 红线规则 | 级别 |
|------|---------|------|
| R1 | 每个指标必须定义清晰的计算公式（分子/分母） | 阻断 |
| R2 | 每个指标所需的事件必须全部设计 | 阻断 |
| R3 | point 在同一 Application 下必须唯一 | 阻断 |
| R4 | COMPONENT 必须有 parent_page + parent_module | 阻断 |
| R5 | MODULE 必须有 parent_page | 阻断 |
| R6 | version 号只能递增，不能回退 | 阻断 |
| R7 | 已通过工单发布的 point 不可修改 | 阻断 |
| R8 | 参数 name 使用 snake_case | 警告 |
| R9 | 关键归因参数（如 ID 类）应设为 is_required: true | 警告 |
| R10 | TrackerType 应与事件语义匹配（点击=CLK，曝光=EXP） | 警告 |

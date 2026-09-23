---
name: lingxing-api
description: 领星 API 路径录入 NocoDB。验证领星 API 文档、自动生成数仓表名并录入 NocoDB 表 AmazonApi.lingxing。当需要新增领星 API 数据接入配置、查看已录入 API、或批量录入 API 路径时使用。
---

# lingxing-api

领星（LingXing）API 路径录入工具，自动扫描文档站 API、和 NocoDB 去重后批量录入。

## Description

将领星 ERP 的 API 路径录入 NocoDB 表 `AmazonApi.lingxing`，用于数仓数据接入配置管理。

| 系统 | 地址 | 用途 |
|------|------|------|
| 领星 API 文档站 | `https://apidoc.lingxing.com` | Docsify SPA，扫描全量 API 并解析参数 |
| NocoDB | `https://nocodb.addx.live` | 存储已录入的 API 路径配置（通过 nocodb skill 操作） |

**依赖 Skill**：NocoDB 的认证、CRUD 操作和安全规范由 `nocodb` skill 提供，本 skill 不重复实现。

## Rules

### 认证

- **NocoDB**：依赖 nocodb skill（`xc-token: $NOCODB_TOKEN`）
- **领星文档站**：Cookie 中设置 Access Key `yrUxaGnbto`

### 只录入读取类 API

**绝对禁止录入写入/修改/删除类 API。** 判定规则：

1. API 名称含「创建」「编辑」「修改」「删除」「更新」「提交」「作废」「取消」→ 排除
2. API 名称含「查询」「获取」「列表」→ 录入
3. 不确定时展示给用户判断

### 领星文档验证

通过 WebFetch 获取文档源文件验证 API 存在性：

```bash
# 获取文档索引
WebFetch https://apidoc.lingxing.com/_sidebar.md

# 获取具体 API 文档（Docsify .md 源文件可直接访问）
WebFetch https://apidoc.lingxing.com/docs/{Category}/{Endpoint}.md
```

从文档中解析：`name`（中文名称）、`path`、`method`、`req_body_var`（必填参数）。

解析细节（HTML 标签处理、分页参数排除等）参见 [references/doc-parsing.md](references/doc-parsing.md)。

验证失败时（文档不可达或格式异常），告知用户并允许手动确认后录入。

### table 名生成规则

格式：`ods_lx_` + 路径缩写 + 后缀，总长 ≤ 45 字符，全小写下划线连接，禁止截断。

**后缀由 `req_body_var` 决定**（与 `schedule` 字段无关）：
- 含时间参数 → `_di`（增量），不含 → `_df`（全量）
- 数据按小时更新 → `_hi`，按周汇总 → `_wi`
- 不确定时默认 `_df`

**schedule 字段**：统一填 `di`，可选值仅 `di`/`hi`/`wi`，与表名后缀无关。

缩写规则（路径前缀缩写表、常用词缩写表）参见 [references/table-naming.md](references/table-naming.md)。

### NocoDB 表操作

- **项目和表**：`AmazonApi.lingxing`
- **business 默认值**：`f,c,b`，用户指定其他值时以用户为准
- 完整字段定义和操作示例参见 [references/nocodb-table.md](references/nocodb-table.md)

### 操作红线

| 红线 | 说明 |
|------|------|
| **禁止删除** | 不得执行 DELETE 操作，即使用户要求 |
| **table 名不可重复** | 写入前必须查重，path 和 table 均不可重复 |
| **禁止跳过验证** | 必须先验证文档再录入，验证失败需用户手动确认 |
| **禁止跳过审阅** | 写入前必须整理文档并获用户确认 |

### 工作流一：全量扫描录入（主流程）

用户未指定具体 API 时，默认使用此流程：

1. **扫描文档站** — WebFetch `_sidebar.md` 获取全部 API 列表，过滤掉写入类
2. **拉取 NocoDB 已录入记录** — 查询全部 path
3. **计算差集** — 读取类 API 减去已录入 path
4. **逐条解析** — WebFetch 文档页解析 Path/Method/Required Params
5. **生成表名** — 按规则生成 table 名
6. **整理文档** — 结构化表格展示给用户（含差集统计）
7. **用户审阅确认** — 等待确认后才写入
8. **批量写入 NocoDB**
9. **展示写入结果**

### 工作流二：指定 API 录入

1. **验证** — WebFetch 文档验证存在性，解析参数
2. **防重** — 查询 NocoDB 确认 path 和 table 均未录入
3. **生成表名 → 整理文档 → 用户审阅确认 → 写入 → 展示结果**

### 输入兼容

| 用户输入 | 解析方式 |
|----------|----------|
| （无具体 API） | 触发工作流一，全量扫描 |
| `/erp/sc/data/seller/lists` | 直接作为 API Path，触发工作流二 |
| `docs/BasicData/SellerLists` | 文档路径，需 WebFetch 获取实际 API Path |
| `查询亚马逊店铺列表` | 中文名称，从 sidebar 搜索匹配文档路径 |
| `录入基础数据下的所有 API` | 按分类过滤后触发工作流一的子集 |

## Examples

### Bad

```
❌ 不扫描文档站，直接问用户要录哪个 → 应先自动扫描并去重展示差集
❌ 扫描后不去重就全部录入 → 必须先查询已录入记录，只录入差集
❌ 未整理文档就直接录入 → 必须先展示表格让用户审阅
❌ 录入写入类 API → 含「创建/编辑/删除」等的 API 应在过滤阶段排除
```

### Good

```
✅ 用户: 录入 /erp/sc/data/seller/lists

1. [验证] WebFetch 文档，确认存在（查询类 ✓）
2. [防重] 查询 NocoDB，path 未录入 ✓
3. [审阅]
   | 字段 | 值 |
   |------|-----|
   | name | 查询亚马逊店铺列表 |
   | path | /erp/sc/data/seller/lists |
   | table | ods_lx_erp_d_seller_lists_df |
   | method | GET |
   | req_body_var | — |
   | schedule | di |
   请确认？→ 用户确认 → 写入成功 ✓
```

## References

- [NocoDB 表结构与操作示例](references/nocodb-table.md)
- [table 名缩写规则](references/table-naming.md)
- [文档解析注意事项](references/doc-parsing.md)
- NocoDB 认证和通用操作规范：依赖 `nocodb` skill

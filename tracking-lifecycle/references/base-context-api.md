# Base Schema 与 Context API

核对日期：2026-10-03；来源为 tracker-management commit
`d61e1692c3d28224e4d642f7bdf765a6a982ab0d`。
完整路由和 DTO 定义见 [Controller 清单](platform-api-catalog.md)，
鉴权见 [平台鉴权](platform-auth.md)。本文件没有执行平台写入。

## Base Schema：复用事件 data 参数

| 方法 | 路径 | 请求绑定 |
|---|---|---|
| GET | `/api/baseSchema/list` | query `applicationId` |
| GET | `/api/baseSchema/detail` | query `id` |
| POST | `/api/baseSchema/add` | JSON `BaseSchemaParam` |
| POST | `/api/baseSchema/update` | JSON `BaseSchemaParam`，带现有 `id` |
| DELETE | `/api/baseSchema/delete` | query/form `id`、`parentApplicationId`；不是 JSON body |

`BaseSchemaParam`：`applicationId`、`id`（创建时省略）、`name`、
`description`、完整 `parameters`。参数沿用 `EventParameterParam`：
`id`、`eventId`、`trackerType`、`name`、`alias`、`valueType`、
`valueConfig`、`parseConfig`、`isRequired`、`privateLevel`、
`securityLevel`、`description`。`trackerType=0` 为 BASE；必填用
`isRequired=1`。`valueType` 必须是 JSON Schema 类型名称，不能传数字码。
新建 Base/Context 参数的所属 ID 由服务层赋值，不能伪造已有实体 ID。

当前实现需要活跃工单；Base 修改会影响引用它的事件 Schema，
删除会检查事件依赖。修改前读取完整详情，更新后按名称/应用回读
实际 ID 及完整参数，再检查每个引用事件的实际 Iglu 产物。
POST add/update 返回 `Result.success()`，没有返回新建 ID，不能从
空响应猜 ID、借用旧 ID 或只凭 HTTP 200 宣称创建成功。

重要区别：平台 `SchemaFileUtil` 将 Base 的 `properties` 和 `required`
合并进事件自身的 JSON Schema，SDK 必须把这些字段放入事件 `data`。
平台 Base 关联本身不会自动产生 Snowplow Context，也不会替 SDK
采集字段。事件 `baseSchemas` 写入用数值 ID 列表；详情回读若是
对象列表须提取 ID，不能把 name 当成平台 ID。

来源：
[BaseSchemaController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java)、
[BaseSchemaServiceImpl](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/service/impl/BaseSchemaServiceImpl.java)、
[SchemaFileUtil](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/common/SchemaFileUtil.java)。

## Context：独立 Iglu 实体

| 方法 | 路径 | 请求绑定 |
|---|---|---|
| GET | `/api/context/list` | query `applicationId` |
| GET | `/api/context/detail` | query `id` |
| GET | `/api/context/schema` | query `id`；响应为拼接后的 URL 字符串 |
| POST | `/api/context/save` | JSON `ContextParam`；创建不带 `id`，更新带现有 `id` |

`ContextParam`：`applicationId`、`id`、`name`、`schemaUrl`、
`description`、完整 `parameters`。参数形状同上；更新会删除原 Context
参数再 upsert 请求列表，必须做完整 round-trip。当前同名校验可能使
改名失败，应保留原名称，并以实际结果为准。

`ContextService.save()` 保存 DB 元数据及参数；`contextSchema()` 只将
配置中的 Micro `schema-base-url` 与 `schemaUrl` 拼接。`schemaUrl`
应是实际 resolver 下的相对 Schema 路径，不能用一个尚不存在的
候选 URL 宣称 Schema 可用。该 Controller/Service 中没有 Context
文件上传接口，生产发布链的事件 Schema 上传不能自动算作独立
Context 已上传；其它部署工具的能力需要另行核验。

因此验收分成三项：

1. DB 注册：save 后按名称/应用读取真实 ID，再逐字段读取 detail。
2. 文件发布：使用已批准的 resolver 上传/部署工具保存独立 Iglu Self
   JSON Schema；HTTP GET 确认文件存在，核对 vendor/name/format/version
   与 `properties`/`required`。只拿到 URL 字符串不是文件证据。
3. SDK：真实业务调用携带独立 Context URI/payload；sandbox good/bad
   单独断言 URI、字段、类型、次数及隐私。事件通过不代表 Context 通过。

来源：
[ContextController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java)、
[ContextService](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/service/impl/ContextService.java)、
[ContextParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ContextParam.java)。

## 自动化流程与当前 CLI 边界

查询可以使用 Project Token；上述 POST/DELETE 必须用有权限的账号
Session/PAT，Project Token 无法写入。Base 创建/关联及 Context DB
注册有 REST API，不要求日常操作全部使用 Chrome。

设计已审定、L1/L2 通过后，按现有 Skill 完整 B/L/P 和唯一活跃工单
规则执行：写前全量回读；先创建 Base 再取得真实 ID；关联事件用完整
参数列表；每次写后回读；独立 Context 文件部署后才用真实 SDK 验证。
迁移涉及已发布字段移除、类型/必填性变化时，先满足已发布不可变
规则和受审的新 model 迁移契约，不能跳过 CLI 的 `IMMUTABLE_FIELD`。

当前专用 `PlatformClient` 没有 Base/Context 方法；通用
`AdministrativeClient` / `api-call` 已覆盖上述 CRUD，使用正式鉴权和
固定请求绑定。change 投影仍只保存 Base ID，通用 sandbox 校验不
解析 Context payload，不能把 API 响应 ACK 冒充完整发布门禁。若要端到端
支持，应另补独立 before/after、参数内容摘要、Context 文件哈希与
SDK 校验、部署回读及失败恢复测试。

生产工单发布仍使用受保护发行分支和受限 CI 身份；平台业务校验、
发布状态 3、全量树和 prod Iglu 回读均通过后才能称为发布完成。

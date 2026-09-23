# 平台 API

生产 Project 契约为 `audience-project-v3`。每个 Project 操作的 `path.project_id`
须与已认证 self-context 或可信宿主配置一致。原生宿主使用已安装工具的输入 schema 和已验证响应。
完整仓库 clone 还包含 `contracts/project-control-plane.openapi.json` 和
`contracts/project-operation-registry.json`，提供精确的封闭 schema。
语义指引包不含契约文件或脚本，使用原生宿主等价的契约校验。

<a id="operations"></a>
## 操作

| 操作 | 方法与路径 | 必需 body 字段 |
| --- | --- | --- |
| `list_project_audience_assets` | `GET /api/platform/v3/projects/{project_id}/audience-sync/audience-assets` | 无 |
| `list_project_syncs` | `GET /api/platform/v3/projects/{project_id}/audience-sync/syncs` | 无 |
| `get_project_sync` | `GET /api/platform/v3/projects/{project_id}/audience-sync/syncs/{sync_request_id}` | 无 |
| `get_project_sync_capabilities` | `GET /api/platform/v3/projects/{project_id}/audience-sync/capabilities` | 无 |
| `get_query_capabilities` | `GET /api/platform/v3/projects/{project_id}/audience-sync/query-capabilities` | 无 |
| `validate_audience_query` | `POST /api/platform/v3/projects/{project_id}/audience-sync/queries/validate` | `criteria` |
| `create_audience_query` | `POST /api/platform/v3/projects/{project_id}/audience-sync/queries` | `criteria`, `idempotency_key`, `name` |
| `get_audience_query` | `GET /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}` | 无 |
| `preview_audience_query` | `POST /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}/preview` | 无 |
| `materialize_audience_query` | `POST /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}/materializations` | `expected_member_count`, `idempotency_key`, `preview_attestation_id` |
| `get_audience_query_materialization` | `GET /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}/materializations/{request_id}` | 无 |
| `sync_audience_query` | `POST /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}/syncs` | `confirmed`, `destination_id`, `destination_revision`, `expected_member_count`, `idempotency_key`, `materialization_request_id`, `materialization_run_id` |
| `get_audience_query_sync` | `GET /api/platform/v3/projects/{project_id}/audience-sync/queries/{plan_id}/syncs/{request_id}` | 无 |
| `get_project_personal_key_context` | `GET /api/platform/v3/personal-key` | 无 |


自身 key 发现操作为 `get_project_personal_key_context`：
`GET /api/platform/v3/personal-key`，不带选择参数或 body。
本地 `summarize_project_keys` 对每个注入别名分别执行检查。输入与诊断输出见[宿主配置](host-configuration.md)。

<a id="evidence-and-outputs"></a>
## 证据与输出

创建操作返回不可变圈人计划；保留 `project_id`、`binding_revision` 和 `plan_id`。
物化使用fresh `preview_attestation_id` 和 `expected_member_count`；
同步使用确切成功的 `materialization_request_id`、`materialization_run_id` 和人数，
以及公布的 `destination_id`、`destination_revision` 和布尔值 `confirmed=true`。

读取 `get_query_capabilities` 获取执行标志，读取 `get_project_sync_capabilities` 获取目标语义。
圈人查询同步要求公布的目标为 `kind=brevo`、`target_type=folder`，用于创建受管理的子 List。
使用相应在线开关，详见 [Project 圈人查询](project-query.md)。

轮询返回的精确请求 ID，并在内部保留响应中的 Project、plan、request、run binding。
这些执行标识和 preview attestation 不属于默认用户输出。默认展示名称、实际条件、已知数据快照分区、
分阶段人数和原生状态；同步分别展示配置的 Folder 目标名称与后端已验证返回的实际 Brevo List 名称，
后者不得从前者、ID 或网址推测。分别标注聚合 `added_count`、`removed_count`、
`skipped_count`。需要解释结果时使用可用跳过原因及 `safe_error_code`。
不得把同步动作计数解释为圈定人群总人数、List 最终总人数或营销送达人数。
缺失信息保持未知；可按完成回复规则单次读取精确同步详情获取 List 名，或单次读取精确计划恢复 Audience 链接。
除此之外，不为补齐展示字段增加 API 调用或写操作。
诊断时仅披露必要的非秘密标识。
详见[完成回复](project-query.md#completion-replies)。
仅当成功响应的 binding 匹配时展示 Platform 返回的网址；不得自行构造。
缺少链接不改变操作已经成功的事实。

<a id="existing-assets-and-sync-tasks"></a>
## 现有资产与同步任务

`list_project_audience_assets` 接受可选 `query`（最多 100 字符）、`limit`（1–100，默认 20）
和不透明的 `cursor`（最多 1024 字符）。其 `resource.items` 只含 `asset_id`、可空的 `name`
和 `source`（`native_audience` 或 `query_plan`）。此列表覆盖所选所有者的资产，不是 Project 内各所有者的资产。

`list_project_syncs` 接受可选 `limit`（1–100，默认 20）、`cursor` 和 `status`
（`queued`、`running`、`succeeded`、`failed`、`reconcile_required`）。
它覆盖已认证 Project 内各所有者的任务。将返回的 `sync_request_id` 传给 `get_project_sync`，
精确回读聚合状态。历史数据中可空的来源信息和计数保持未知，不得编造。
这些读取操作不会开启效果，也不会授予其他所有者的圈人计划权限。

查询参数放在 CLI 请求的 `query` 对象中，例如：
`{"path":{"project_id":"kiwibit"},"query":{"limit":"20"}}`。
每个 CLI `query` 值都必须为字符串，包括数值：使用 `"limit":"100"`，不能使用 `"limit":100`。
参数值仍须符合操作的范围与允许参数列表。这条传输规则不适用于 `body`：
其数字、布尔值、数组和对象保持 schema 定义的类型。
两个列表均返回 `resource.items` 和 `resource.next_cursor`。
即使中间页的 `items` 为空，也要继续翻页直到 `next_cursor` 为 null；翻页时保持同一 Project、key 和筛选条件。
缺少原生工具、请求被拒绝或 API 不可用属于能力或访问失败，绝不证明资产数量为零。
不要回退到 Admin、直接服务商调用或旧版搜索。

### 实际 Brevo List 名称

`get_project_sync` 的 `resource.destination.target.display_name` 来自该精确同步请求存储的 `list_name`，
表示实际 List 名；`resource.destination.display_name` 表示配置的目标名称。目标为 `type=list` 时，
`resource.destination.target.external_url` 是返回的 List 链接。名称和链接分别按实际可用字段展示，
保持完整名称（包括后端追加的唯一后缀），不在 Skill 内重建命名规则。
精确请求读取及默认业务回复规则见[完成回复](project-query.md#completion-replies)。

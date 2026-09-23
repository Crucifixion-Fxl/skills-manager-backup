> 可选启动操作 `get_project_personal_key_context` 是独立且不限定 Project 的自身 key 读取，
> 不属于 Project 操作。下方表格列出十项圈人执行主链操作；另有 `list_project_audience_assets`、
> `list_project_syncs` 和 `get_project_sync` 三项资产与同步任务读取，共十三项 Project 操作，
> 完整清单见[平台 API](platform-api.md#operations)。认证后的 Project 发现、显式 Project binding 和隔离的多 key 选择，
> 参见[宿主配置](host-configuration.md#project-bootstrap)。已授予的操作权限不能替代在线执行开关。

# Project 原生结构化圈人查询

<a id="human-navigation-links"></a>
## 人工访问链接

圈人计划创建、读取和精确同步回读可能返回 `audience_detail_url`；
同步结果还可能返回指向实际 Brevo List 的 `provider_resource_url`。
存在时展示返回的网址。链接缺失或为 null 表示不可用；不要编造链接，也不要将链接存在解释为执行成功。
排队任务也可能已有 List，应根据原生状态和计数判断结果。
打开链接仍需用户具备平台或 Brevo 的登录状态和权限。
不得把 API key 放进链接，也不得调用服务商来补查其浏览器网址。

固定的上游快照包含这些可选模型字段和可空的结构化条件回读，其他未知字段仍会被拒绝。
Audience 网址只允许一个包含 Project 圈定人群路径的 `aw_target` 查询参数；任意其他查询参数、凭据和片段仍被拒绝。

<a id="completion-replies"></a>
### 完成回复

这些说明适用于原生具名工具与随仓公共 CLI，描述当前任务的回复，不是后台工作流。
客户端响应是执行证据；Agent 不应默认把响应 JSON 或内部标识直接展示给用户。
按下列业务格式说明原生状态、聚合人数和安全错误码，并保留实际证据含义。

- 默认展示圈人计划名称，以及实际条件的字段、操作符和值，保留 AND/OR、否定和量词语义。
  用已验证的结构化条件说明范围，不得仅凭名称推测条件，也不得把条件概括成更宽或更窄的人群。
- 数据快照分区单独标注为“数据快照分区”，采用计划返回的确切值；它不是筛选日期。
  条件中的日期仍属于筛选条件，不能用快照分区代替。
- 人数注明阶段：“预览人数”来自该计划的预览；“已物化人数”来自精确物化成功回读。
  不得将预览人数当作已物化人数，或将已物化人数当作已同步人数。始终保留实际原生状态；
  `queued`/`running` 仍表示未完成，失败或需要对账也不得描述为成功。
- 同步回复在已知时沿用名称、实际条件和数据快照分区，使用已验证的目标显示名称。
  配置的 Folder 目标名称与同步实际生成的 Brevo List 名称分别展示。List 名称仅采用后端已验证返回的
  实际 List 名称。`get_project_sync` 的 `resource.destination.target.display_name` 是实际 List 名，
  `resource.destination.display_name` 是配置的目标名，两者不能互换。
  精确同步回读未包含 List 名时，可使用同一已验证 key 和 Project，对返回的确切同步请求 ID
  调用一次 `get_project_sync`（`path.sync_request_id`）；只使用同一请求、来源计划和已验证目标对应的结果。
  读取失败、目标或名称缺失时保留原同步状态，说明 List 名暂不可用，不影响已确认的同步成功。
  不得从 Folder 名称、ID、网址或命名惯例推测名称。实际返回的 List 名可能带唯一后缀，
  展示时保留完整名称，不将后缀另列为请求 ID。
  分别标注新增（`added_count`）、移除（`removed_count`）和跳过（`skipped_count`）人数；
  它们是本次同步动作的聚合计数，不是圈定人群总人数，也不是营销送达人数。
  不得将这些计数相加或相减推算服务商 List 的最终总人数。需要解释跳过或失败时，
  使用可用的安全跳过原因及 `safe_error_code`，不输出自由格式的服务商诊断。
- 精确 `get_audience_query_materialization` 回读成功后，只有此前圈人计划创建或读取返回的
  `audience_detail_url` 的已验证 binding 属于同一 Project、圈人计划和 key 上下文时，才将其附在回复中。
  不得复用其他 key、Project 或圈人计划的网址。若没有匹配网址，最多对该精确计划调用一次
  `get_audience_query`，保持相同的已验证 key 上下文（使用别名时也须保留）。
  只有响应 binding 验证通过后才附上返回的网址。链接缺失或为 null、工具不可用或读取失败，
  表示链接不可用，不代表已经成功的物化失败；不要为链接重试或自行构造。
- 精确 `get_audience_query_sync` 回读到终态后，对返回的 `audience_detail_url`、`provider_resource_url`，
  分别在存在且响应 binding 通过验证时附上。服务商链接指向实际返回的 List，不是配置的 Folder。
  链接缺失或为 null 仍表示不可用；失败或需要对账的任务不会因为有链接就变成成功。
  非终态回读仍为 queued/running，不代表完成。
- 名称、条件、分区、目标名称或计数缺失时保持未知，不猜测、不用内部 ID 冒充显示名称。
  使用当前任务中已有的已验证业务证据；除上述单次精确同步详情读取外，不得仅为补齐业务展示字段增加 API 调用或写操作。
  上述已有的单次精确计划读取规则仅用于恢复 Audience 链接，保持不变。
  完成链接展示不增加新效果、轮询循环或异步通知。
  不得为获取链接重新提交物化或同步、切换 key、调用服务商或构造网址。
  现有任务授权与精确回读规则保持不变。
- `plan_id`、物化/同步请求 ID、`materialization_run_id`、`preview_attestation_id`、
  binding、revision 和 idempotency_key 均作为内部执行与恢复证据保留，不默认列在进度、确认或完成回复中。
  用户明确要求诊断或确有排障需要时，仅展示必要的非秘密标识；preview attestation 的值不作为用户诊断材料展示。任何情况下都不得输出凭据、收件人明细或其他秘密。

以下仅为格式示例，尖括号内容均是说明性占位符，不是真实执行结果或网址：

```text
圈人计划：<实际名称>
筛选条件：<实际字段> <实际操作符> <实际值>
数据快照分区：<计划返回的分区>
预览人数：<预览人数>；已物化人数：<成功物化人数>
状态：succeeded（物化成功）
Audience：<返回且 binding 已验证的 Audience URL>
```

```text
圈人计划：<实际名称>；筛选条件：<实际字段/操作符/值及逻辑关系>
数据快照分区：<已知的计划分区>
配置的 Folder 目标：<已验证的目标显示名称>
实际 Brevo List：<后端已验证返回的实际 List 名称>
状态：succeeded（同步成功）；已物化人数：<该精确成功物化的成员人数>
新增：<added_count>；移除：<removed_count>；跳过：<skipped_count>
Audience：<返回且 binding 已验证的 Audience URL>
Brevo List：<返回且 binding 已验证的 provider List URL>
```

<a id="project-execution-contract"></a>
## Project 执行契约

使用 `audience-project-v3` API。通过 self-context 发现 key 对应的 Project，
或使用宿主匹配的 `AUDIENCE_PROJECT_ID`，在 `path` 中传入相同的 `project_id`。
服务端认证该 Project 并推导 tenant 和 bundle；两者都不是请求字段。
宿主配置不能替代服务端授权。

原生宿主通过已安装操作的 schema 提供精确请求结构。
完整 Skill 安装（例如 `skills/audience-sync`）或完整仓库 clone 另含
`contracts/project-control-plane.openapi.json` 和 `contracts/project-operation-registry.json`。
这些路径相对于当前 `SKILL.md` 所在的 Skill 根目录，无需另行 Git clone；
不要求仅有语义指引包的宿主去打开不存在的文件。
以下所有路径均相对于 `/api/platform/v3/projects/{project_id}/audience-sync`。

| 操作 | 方法与路径 | Body |
| --- | --- | --- |
| `get_project_sync_capabilities` | GET `/capabilities` | 无 |
| `get_query_capabilities` | GET `/query-capabilities` | 无 |
| `validate_audience_query` | POST `/queries/validate` | criteria |
| `create_audience_query` | POST `/queries` | name, criteria, idempotency_key |
| `get_audience_query` | GET `/queries/{plan_id}` | 无 |
| `preview_audience_query` | POST `/queries/{plan_id}/preview` | 无 |
| `materialize_audience_query` | POST `/queries/{plan_id}/materializations` | preview_attestation_id, expected_member_count, idempotency_key |
| `get_audience_query_materialization` | GET `/queries/{plan_id}/materializations/{request_id}` | 无 |
| `sync_audience_query` | POST `/queries/{plan_id}/syncs` | materialization_request_id, materialization_run_id, expected_member_count, destination_id, destination_revision, confirmed, idempotency_key |
| `get_audience_query_sync` | GET `/queries/{plan_id}/syncs/{request_id}` | 无 |


<a id="discovery-and-unsupported-paths"></a>
## 发现与不支持的路径

DataHub schema 搜索是可选的只读候选发现（URN、字段、类型、预期语义和粒度），不是前置依赖。
元数据或血缘不代表权限。将每个逻辑关系、字段、操作符以及边和量词映射到同一份当前查询能力。
适配器在 validate/create 前重新读取同一 Project 的能力，并在提交条件前拒绝不存在的 ID。
Platform 会再次执行权威验证；Skill 不编译 SQL。

已审阅的 [Project 查询白名单基线](project-query-whitelist.md)有助于离线转换要求，
但既不是在线能力，也不是授权。运行时同一 Project 的 `query-capabilities`
才是字段、操作符、逻辑关系、边和执行开关的唯一权威依据。

Registry 版本与字段清单由能力响应决定，并可能演进。
不要写死画像字段数量，或复用在线目录中不存在的字段、操作符：
只提交当前同一 Project 的 `query-capabilities` 响应公布的逻辑关系、字段与操作符。
除非响应公布，否则扩展关系和边为空。
最近一次订阅取消或到期需要经过审阅的语义路径，在此之前不受支持。

遇到 `query_path_onboarding_required` 时，不要提交、创建、物化或同步该候选。
返回供 Audience 仓库 MR 使用的接入提案：可用时的 DataHub 引用、缺少的逻辑字段与边或指标、预期类型和操作符、
粒度、最新行含义、基数、新鲜度及范围要求。不要包含可执行 SQL、ON 表达式或范围值。
当前物理字段与口径由获授权的白名单维护者以 Superset 为主要信源复核；
本 Skill 不索取 BI 账密、不直接查询仓库，也不因 DataHub 缺失而阻止提交语义接入提案。
普通的已批准 registry 新增项不需要修改 DATA 业务白名单；DATA 仍独立校验 profile 主表的
tenant+bundle、直接 user_id 关联及执行安全约束。子表缺少 tenant/bundle 或需求涉及已支持的
命名聚合，不单独构成上游建模阻塞；先核对当前能力与来源语义。

验证 body 示例（Project 放在 path，不在 body）。将 `<live registry_version>` 替换为
`get_query_capabilities` 返回值；只有该响应公布示例字段和操作符时才可使用：

```json
{"criteria":{"schema_version":"audience-criteria-v1","registry_version":"<live registry_version>","where":{"kind":"field","relation_id":"audience_profile","field_id":"country","operator":"eq","value":"VN"}}}
```

<a id="exact-evidence-and-effects"></a>
## 精确证据与效果

<a id="read-and-rebuild-historical-criteria"></a>
### 读取与重建历史条件

精确 `get_audience_query` 和创建响应包含可空 `criteria`，即存储的结构化 `AudienceCriteriaSpec`，
不是可执行 SQL。旧服务端可能省略此字段，客户端会将缺失规范化为 `null`。
历史存储缺失或格式错误时也可能返回 `null`。
在原有已授权 Project 和 key 上下文中读取；发现能力不授予其他所有者的计划权限。
客户端拒绝凭据、邮箱地址和未声明网址等不安全标量内容，不会通过脱敏或改写历史谓词来处理。

遇到 HTTP 409 `project_query_plan_stale`，读取该精确计划一次。
历史上有效的计划，即使预览、物化和同步已被阻止，仍可能可读。
将返回条件视为历史意图，而非当前执行权限。
若 criteria 为 null 或精确 GET 失败，请求原始条件；不得根据名称或 hash 重建。

重建前调用同一 Project 的 `get_query_capabilities`。
对照当前响应检查每个逻辑关系、字段、值类型、操作符、边和量词，
并确认其业务含义仍与用户要求的圈定人群一致。不得盲目替换 `registry_version` 或静默丢弃不支持的谓词。
若语义已变化或无法确定等价性，先解释差异并请求用户澄清，再创建替代计划。
确认等价后，使用当前 registry 版本构造新候选，调用 `validate_audience_query`，
再使用新的 idempotency_key 调用 `create_audience_query`。
这些操作会重新读取能力，Platform 会执行权威验证。
不得修改旧计划或自动重放其效果。任何已授权同步都要求新计划有自己的fresh preview attestation、
物化以及确切成功的 run；旧证据不能转移复用。

<a id="current-execution-evidence"></a>
### 当前执行证据

`validate_audience_query` 只验证并编译候选条件，产生安全证据。
其响应特意固定 `materialization_available=false`；该字段**不是**在线执行开关，
不能用于阻止或授权效果。查询能力独立公布 `structured_criteria_available`、
`preview_available`、`materialization_available` 和 `brevo_sync_available`；
在对应效果执行前，使用同一 Project 的 `query-capabilities` 中的相应标志。
开关缺失或为 false 时停止该操作；适配器离线能力不代表在线就绪。
独立的 `/capabilities` 提供同一 Project 下活跃且就绪的逻辑目标及不可变 `destination_revision`；
其中单独的 `materialization_available=false` 不会禁用新的圈人查询操作。
适配器会重新检查这些原生开关。
创建受管理 Brevo 子 List 的 Project 圈人查询同步，要求目标明确公布 `kind=brevo` 和 `target_type=folder`；
`target_type=list`、`null` 或缺失值均有歧义，必须在同步前停止。
不得从 ID、标签或默认标志推断这一语义。

创建操作返回一个冻结分区和 hash 的不可变圈人计划。
保留确切的 `project_id`、`binding_revision` 和 `plan_id`。
**Project 物化要求确切且 fresh preview attestation 与人数**；不得编造，也不得使用其他计划的凭证。
已有精确请求回读在新效果开关关闭时仍可用。

只有 `status=succeeded`，并且拥有确切、非空的物化 run 和有效成员人数，才允许同步。
请求确认时，冻结该请求、run、人数和已公布的同一 Project 目标及 revision。
`confirmed` 必须是 JSON 布尔值 `true`，不能是 `1` 或字符串。
对这些未变的输入保留同一 idempotency_key。在同一计划下轮询返回的精确请求 ID；
不得使用 latest、切换目标或重放不明确的效果。 binding 缺失、过期或被撤销时拒绝执行。
`queued` 不代表服务商侧成功；`outcome_unknown`、`reconcile_required` 需要对账，而不是发起新请求。

成功同步回读只可能返回聚合 `added_count`、`removed_count`、`skipped_count` 和四项安全跳过原因
（`missing_alias_count`、`malformed_alias_count`、`unapproved_domain_count`、`duplicate_alias_count`）。
若原因元组存在，四个值必须全为非负整数，且总和严格等于 `skipped_count`；
部分缺失或合计不符时拒绝响应。全为 null 的原因元组仅兼容早于这些列的历史行。
物化与同步失败回读只可能返回受限的 `safe_error_code`，绝不返回自由格式的服务商诊断。

成员与同步效果仅返回聚合结果；诊断限于受限的 `safe_error_code` 和必要的非秘密标识。
用户回复按[完成回复](#completion-replies)展示业务信息，内部保留精确执行证据。
Platform 负责执行、服务商凭据和成员数据。

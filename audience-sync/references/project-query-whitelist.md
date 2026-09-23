# Project 查询白名单基线

这是已审阅的 MR 目标参考，不是在线能力或执行授权。
构造条件前，调用已认证 Project 的 `get_query_capabilities`，使用当前 `registry_version`、
字段类型、操作符、边和执行开关。若部署与此基线不同，以返回能力为准；不得强行将下方版本填入请求。

Source repository: `services/audiences`.
Source commit: `364aa5e5607e8695b3f2643b000b53e3836f59f9`.
Source artifact: `audience-workflow/backend/audience_workflow/resources/audience_query_registry.v1.json`.

<a id="available-logical-relation-and-fields"></a>
## 可用逻辑关系与字段

本基线只启用内置 `audience_profile` 逻辑关系。扩展业务表白名单为空，没有已批准的 join 边。
下方是完整的受支持标量清单，不代表每个物理画像列都可筛选。字段拼写必须精确。

<!-- whitelist-snapshot:start -->
Registry: `audience-query-v1-profile-scalars-v2`.
Approved join edges: 0.

| Logical field | Value type | Allowed operators |
| --- | --- | --- |
| `audience_profile.app_score` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.app_type` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.country` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.current_effective_free_trial` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.current_effective_sku_id` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.current_effective_sku_name` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.current_effective_tier_service_type` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.device_share_type` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.email_domain` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.first_bind_city` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.first_bind_request_complete_timestamp` | `timestamp` | `eq`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.first_bind_serial_number` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.first_purchase_time` | `timestamp` | `eq`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.is_current_day_effective` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.is_feeder_bird_device_user` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.is_real_paid_user` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.language` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.last_active_time` | `timestamp` | `eq`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.last_purchase_time` | `timestamp` | `eq`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.os_timezone` | `string` | `eq`, `in`, `is_null` |
| `audience_profile.regist_time` | `timestamp` | `eq`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.total_paid_amount` | `number` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.total_paid_cnt` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.total_refund_amount` | `number` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.total_refund_cnt` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
| `audience_profile.user_device_quantity_lastest` | `integer` | `eq`, `in`, `gte`, `lte`, `between`, `is_null` |
<!-- whitelist-snapshot:end -->

字符串支持等值、集合成员和 null 检查。整数与数字还支持包含边界的比较与区间。
时间戳支持等值、包含边界的比较、区间和 null 检查，但本基线不支持 `in`。
此处 `is_*` 字段是整数，不是 JSON 布尔值；不得仅凭名称推断枚举值或业务含义。
使用 [Project 圈人查询](project-query.md)和随仓操作契约中的请求结构。

<a id="scope-and-unsupported-paths"></a>
## 范围与不支持的路径

Platform 从可信 Project binding 注入 tenant 和 bundle 限制。
不得向 criteria 添加范围覆盖值、物理表名、身份字段投影、分区覆盖值、SQL 或 join 键。
这份标量清单不隐含支持数组字段或任意画像列。内部身份列和 Project 范围列不是逻辑谓词字段。

DataHub 发现不等于执行授权。发现的订阅表或血缘路径，在 Platform 公布相应逻辑关系、字段、
操作符和边前不能 join。使用运行时逻辑关系公布的 `datahub_urn` 匹配发现证据；搜索命中本身不授予访问权限。
本基线不暴露最近一次订阅的取消状态、取消时间或到期时间；
不得将当前有效订阅或购买字段当成相同含义的替代。
对不支持的要求返回接入提案，不要生成静默丢弃该条件的不完整查询。

<a id="maintainer-drift-check"></a>
## 维护者漂移检查

在明确指定、包含固定提交的本地 Audience checkout 上运行：

```sh
python3 scripts/check_project_query_whitelist.py --platform-root /path/to/audiences
```

只读检查器将此表与不可变源文件的确定性公开投影进行比较。
它不会拉取仓库、加载 token、授权查询或添加跨仓库 CI 任务。
更新基线时，一并审阅源 revision 并重新生成公开表格；即使检查通过，运行时能力仍是权威依据。

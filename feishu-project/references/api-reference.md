# Feishu Project 能力参考

本文件同时包含 HTTP API 端点和 MCP 能力映射：
- **有方法和路径的条目** — HTTP API，可通过 Python Client 或直接 HTTP 调用
- **仅列 MCP 工具名的条目** — MCP-only 能力，需通过 MCP 工具调用
- 标注 **[MCP-only]** 的章节整体为 MCP 专属能力

HTTP API Base URL: `https://project.feishu.cn/open_api/`

所有 HTTP 请求需携带 Header: `X-PLUGIN-TOKEN` + `X-USER-KEY`

## 鉴权

| API      | 方法   | 路径                          | 说明                                     |
| -------- | ---- | --------------------------- | -------------------------------------- |
| 获取插件凭证   | POST | `/authen/plugin_token`      | plugin_id + plugin_secret → token (2h) |
| 获取用户凭证   | POST | `/authen/user_plugin_token` | authcode → user_access_token           |
| 刷新 Token | POST | `/authen/refresh_token`     | refresh_token → 新 token                |

## 用户 & 用户组

| API      | 方法    | 路径                                       | 说明          |
| -------- | ----- | ---------------------------------------- | ----------- |
| 获取用户详情   | POST  | `/user/query`                            | 获取指定用户的详细信息 |
| 搜索用户列表   | POST  | `/user/search`                           | 模糊搜索租户内用户   |
| 创建自定义用户组 | POST  | `/:project_key/user_group`               | 创建用户组       |
| 更新用户组成员  | PATCH | `/:project_key/user_group/members`       | 更新用户组成员     |
| 查询用户组成员  | POST  | `/:project_key/user_groups/members/page` | 分页查询用户组成员   |

## 空间

| API    | 方法   | 路径                 | 说明             |
| ------ | ---- | ------------------ | -------------- |
| 获取空间列表 | POST | `/projects`        | 获取用户有权限访问的空间列表 |
| 获取空间详情 | POST | `/projects/detail` | 获取空间详情信息       |

## 工作项搜索

| API         | 方法   | 路径                                                         | 说明        |
| ----------- | ---- | ---------------------------------------------------------- | --------- |
| 搜索工作项（单空间）  | POST | `/:project_key/work_item/filter`                           | 单空间条件搜索   |
| 搜索工作项（跨空间）  | POST | `/work_items/filter_across_project`                        | 跨空间搜索     |
| 搜索工作项（复杂筛选） | POST | `/:project_key/work_item/:type_key/search/params`          | 复杂条件搜索    |
| 全局搜索        | POST | `/compositive_search`                                      | 跨空间和类型搜索  |
| 搜索关联工作项     | POST | `/:project_key/work_item/:type_key/:id/search_by_relation` | 获取关联工作项列表 |

## 工作项实例读写

| API      | 方法     | 路径                                            | 说明            |
| -------- | ------ | --------------------------------------------- | ------------- |
| 获取工作项详情  | POST   | `/:project_key/work_item/:type_key/query`     | 获取单个工作项详情     |
| 获取创建元数据  | GET    | `/:project_key/work_item/:type_key/meta`      | 获取创建所需最小数据    |
| 创建工作项    | POST   | `/:project_key/work_item/create`              | 新增工作项         |
| 更新工作项    | PUT    | `/:project_key/work_item/:type_key/:id`       | 修改工作项         |
| 删除工作项    | DELETE | `/:project_key/work_item/:type_key/:id`       | 删除工作项         |
| 终止/恢复工作项 | PUT    | `/:project_key/work_item/:type_key/:id/abort` | 终止或恢复工作项      |
| 获取操作记录   | POST   | `/op_record/work_item/list`                   | 获取多个工作项的操作记录  |
| 批量查询评审意见 | POST   | `/work_item/finished/batch_query`             | 批量查询节点评审意见和结论 |
| 修改评审结论   | POST   | `/work_item/finished/update`                  | 更新或置空评审意见和结论  |
| 查询评审结论标签 | POST   | `/work_item/finished/query_conclusion_option` | 查询节点配置的评审结论标签 |
| 冻结/解冻工作项 | PUT    | `/work_item/freeze`                           | 冻结或解冻工作项      |
| 交付物批量查询  | POST   | `/work_item/deliverable/batch_query`          | 查询交付物详情       |

## 工时登记

| API    | 方法     | 路径                                                       | 说明         |
| ------ | ------ | -------------------------------------------------------- | ---------- |
| 获取工时记录 | POST   | `/work_item/man_hour/records`                            | 获取工时登记记录列表 |
| 新增工时记录 | POST   | `/:project_key/work_item/:type_key/:id/work_hour_record` | 添加工时记录     |
| 更新工时记录 | PUT    | `/:project_key/work_item/:type_key/:id/work_hour_record` | 更新工时记录     |
| 删除工时记录 | DELETE | `/:project_key/work_item/:type_key/:id/work_hour_record` | 删除工时记录     |

## 流程与节点

| API          | 方法   | 路径                                                           | 说明            |
| ------------ | ---- | ------------------------------------------------------------ | ------------- |
| 获取工作流详情      | POST | `/:project_key/work_item/:type_key/:id/workflow/query`       | 获取工作流信息       |
| 获取工作流详情(WBS) | GET  | `/:project_key/work_item/:type_key/:id/wbs_view`             | 获取 WBS 工作流信息  |
| 更新节点/排期      | PUT  | `/:project_key/workflow/:type_key/:id/node/:node_id`         | 更新节点负责人、排期、表单 |
| 节点完成/回滚      | POST | `/:project_key/workflow/:type_key/:id/node/:node_id/operate` | 完成或回滚节点       |
| 状态流转         | POST | `/:project_key/workflow/:type_key/:id/node/state_change`     | 状态流工作项流转      |
| 获取流转必填信息     | POST | `/work_item/transition_required_info/get`                    | 获取流转所需必填信息    |

## 子任务

| API        | 方法     | 路径                                                                      | 说明         |
| ---------- | ------ | ----------------------------------------------------------------------- | ---------- |
| 搜索子任务（跨空间） | POST   | `/work_item/subtask/search`                                             | 跨空间搜索子任务   |
| 获取子任务详情    | GET    | `/:project_key/work_item/:type_key/:id/workflow/task?node_id=:node_id`  | 获取子任务详情    |
| 创建子任务      | POST   | `/:project_key/work_item/:type_key/:id/workflow/task`                   | 在指定节点创建子任务 |
| 更新子任务      | POST   | `/:project_key/work_item/:type_key/:id/workflow/:node_id/task/:task_id` | 更新子任务      |
| 子任务完成/回滚   | POST   | `/:project_key/work_item/:type_key/:id/subtask/modify`                  | 完成或回滚子任务   |
| 删除子任务      | DELETE | `/:project_key/work_item/:type_key/:id/task/:task_id`                   | 删除子任务      |

## 附件

| API     | 方法   | 路径                                                    | 说明             |
| ------- | ---- | ----------------------------------------------------- | -------------- |
| 添加附件    | POST | `/:project_key/work_item/:type_key/:id/file/upload`   | 在附件字段中添加附件     |
| 上传文件/图片 | POST | `/:project_key/file/upload`                           | 通用文件上传（富文本图片等） |
| 下载附件    | POST | `/:project_key/work_item/:type_key/:id/file/download` | 下载指定附件         |
| 删除附件    | POST | `/file/delete`                                        | 删除附件字段中的附件     |

## 空间关联

| API       | 方法   | 路径                                                    | 说明         |
| --------- | ---- | ----------------------------------------------------- | ---------- |
| 获取关联规则列表  | POST | `/:project_key/relation/rules`                        | 获取空间关联规则   |
| 获取关联工作项列表 | POST | `/:project_key/relation/:type_key/:id/work_item_list` | 获取空间关联的工作项 |

## 视图 [MCP-only]

| API     | MCP 工具                 |
| ------- | ---------------------- |
| 按名称搜索视图 | `search_view_by_title` |
| 获取视图详情  | `get_view_detail`      |
| 创建固定视图  | `create_fixed_view`    |
| 更新固定视图  | `update_fixed_view`    |

## 评论

| API    | 方法   | 路径                                                                    |
| ------ | ---- | --------------------------------------------------------------------- |
| 查询评论列表 | GET  | `/:project_key/work_item/:work_item_type_key/:work_item_id/comments`        |
| 添加评论   | POST | `/:project_key/work_item/:work_item_type_key/:work_item_id/comment/create`  |

## 度量 [MCP-only]

| API    | MCP 工具             |
| ------ | ------------------ |
| 查询图表列表 | `list_charts`      |
| 查询图表详情 | `get_chart_detail` |

## 配置

| API       | 方法   | 路径                                       | MCP 工具                             | 备注       |
| --------- | ---- | ---------------------------------------- | ---------------------------------- | -------- |
| 获取工作项类型列表 | GET  | `/:project_key/work_item/all-types`      | `list_workitem_types`              | 双路径      |
| 查询字段配置    | POST | `/:project_key/field/all`                | `list_workitem_field_config`       | 双路径      |
| 查询角色配置    | POST | `/:project_key/work_item/:type_key/role` | `list_workitem_role_config`        | 双路径      |
| 查询节点字段配置  | -    | -                                        | `list_node_field_config`           | MCP-only |
| 查询工作项关联关系 | -    | -                                        | `list_workitem_relations`          | MCP-only |
| 查询资源库配置   | -    | -                                        | `get_resource_work_item_type_conf` | MCP-only |

## 团队 [MCP-only]

| API    | MCP 工具              |
| ------ | ------------------- |
| 查看团队列表 | `list_project_team` |
| 查看团队成员 | `list_team_members` |

## 排期 & 待办 [MCP-only]

| API     | MCP 工具          |
| ------- | --------------- |
| 查询人员排期  | `list_schedule` |
| 查询待办/已办 | `list_todo`     |

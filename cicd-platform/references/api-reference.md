# A4x CICD API 参考

## 目录

- [约束](#约束)
- [读取接口](#读取接口)
- [上线单写接口](#上线单写接口)
- [部署与灰度接口](#部署与灰度接口)
- [字段与状态](#字段与状态)

## 约束

- 生产前端快照：2026-07-20，前端入口 `index.c8544a8f.js`，配置显示 API Base 为同源 `/api`。
- 登录使用 GitLab OAuth 浏览器 session。不要导出或保存 session cookie/token。
- UI 是写操作事实源。下表用于理解页面和只读诊断；不得用 POST 绕过权限、状态机、disabled 按钮或确认弹窗。
- 请求参数由前端放在 query params；响应由前端请求层解包。

## 读取接口

| 方法 | 路径 | 常用参数 | 用途 |
|---|---|---|---|
| GET | `/tasks` | `showActive,showRecent,showDisable` | 上线单列表 |
| GET | `/task` | `taskId` | 上线单详情 |
| GET | `/task/getTaskProjectList` | — | 可添加项目列表 |
| GET | `/task/builds` | `taskId` | 上线单项目/构建列表 |
| GET | `/task/build/deploy` | `buildId,env` | 某环境最近部署 |
| GET | `/task/project/deploy/status` | `deployId` | 部署状态 |
| GET | `/task/project/deploy/logs` | `deployId,logIndex` | 增量部署日志 |
| GET | `/project/repository/branchs` | `projectId` | 分支及最新 commit |
| GET | `/project/getProjectList` | 查询条件 | 项目列表 |
| GET | `/task/review/apollo` | `taskId` | Apollo Review 预览 |
| GET | `/task/publish/apollo/pre` | `taskId` | Apollo Pre 发布预检 |
| GET | `/task/publish/apollo/prod` | `taskId` | Apollo Prod 发布预检 |
| GET | `/apollo/gray/config` | `region,env,namespace` | 当前灰度配置 |
| GET | `/apollo/gray/ramp/status` | `region,namespace` | 预扩容状态 |
| GET | `/apollo/gray/ramp/logs` | `rampId,logIndex` | 预扩容日志 |
| GET | `/project/get_gray_project_name` | — | 可写灰度项目 |
| GET | `/project/get_gray_readonly_project_name` | — | 灰度只读项目 |
| GET | `/project/get_non_cancelable_project_name` | — | 不可取消部署项目 |

## 上线单写接口

| 方法 | 路径 | 关键参数 | 页面动作 |
|---|---|---|---|
| POST | `/task/create` | `publishAt,description,testScope,metricUrls,hasSQL,hasApollo,apolloDescription,isShort,onlyApollo` | 创建上线单 |
| POST | `/task/update` | 上述字段 + `taskId` | 编辑上线单 |
| POST | `/task/add/project` | `taskId,projectId` | 添加项目 |
| POST | `/task/verify` | `taskId` | 完成测试 |
| POST | `/task/check` | `taskId` | 自动化校验 |
| POST | `/task/audit` | `taskId` | 审批 |
| POST | `/task/gray` | `taskId` | 灰度验证 |
| POST | `/task/close` | `taskId` | 验收/完成 |
| POST | `/task/disable` | `taskId` | 作废 |
| POST | `/task/unlock` | `taskId` | 特批 |
| POST | `/task/review/apollo` | `taskId,apolloReviewReport` | 批准 Apollo Review |
| POST | `/task/publish/apollo/pre` | `taskId` | 发布 Apollo Pre |
| POST | `/task/publish/apollo/prod` | `taskId` | 发布 Apollo Prod |
| POST | `/task/verify/apollo/change` | `taskId,ApolloChangeVerifyContent,ApolloChangeVerifyImage` | 上传验证结果 |
| POST | `/task/suspend` | `taskId,reason` | 挂起 |
| POST | `/task/resume` | `taskId` | 恢复 |
| POST | `/task/step/skip` | `taskId,reason` | 跳过当前步骤 |
| POST | `/task/step/rollback` | `taskId,targetStatus,reason` | 回退工作流步骤 |

## 部署与灰度接口

| 方法 | 路径 | 关键参数 | 页面动作 |
|---|---|---|---|
| POST | `/task/project/deploy` | `buildId,env,gitBranch,gitCommit` | 部署 |
| POST | `/task/project/cancel/deploy` | `deployId` | 取消部署 |
| POST | `/task/project/rollback` | `buildId,env` | 回滚上一成功版本 |
| POST | `/apollo/gray/config` | `region,env,namespace,enabled,ratio,strategy,whitelist,blacklist,taskId` | 保存灰度配置 |
| POST | `/apollo/gray/ramp` | `region,env,namespace,ratio` | 预扩容 |
| POST | `/apollo/gray/ramp/confirm` | `region,env,namespace` | 保存流量配置后确认预扩容 |
| POST | `/apollo/gray/ramp/descale` | `region,env,namespace` | 灰度缩容 |
| POST | `/task/jmeter/run` | `taskId,env` | 自动化测试 |
| POST | `/task/jmeter/gray/run` | `taskId,env` | 灰度自动化测试 |

## 字段与状态

- 上线单：`Status 1..6 = 新建、测试、校验、审批、灰度、完成`。
- 部署：`DeployStatus 1..4 = 成功、失败、进行中、中止`。
- 环境：`pre/gray/prod` 为 CN，`*-us` 为 US，`*-eu` 为 EU。
- 灰度网关：CN=`gateway-cn`，US=`gateway-us`，EU=`gateway-eu`；灰度配置读取使用 `env=prod`。当前前端对 US 和 EU 网关都传 `region=US`，CN 传 `region=CN`。
- PROD 比例输入会反转成实际 gray ratio：`gray = 100 - prod`。
- `whitelist` / `blacklist` 是用户 ID 数组；UI 输入为逗号分隔文本。更新时保留未要求修改的现有成员。
- 预扩容状态包含 `idle/running/success/failed`；只有 `success` 且 `targetRatio` 覆盖目标实际 gray ratio 时才能切流。

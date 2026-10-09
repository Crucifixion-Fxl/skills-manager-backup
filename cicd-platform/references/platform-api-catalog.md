# CICD source API catalog

固定源码提交：[`9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1`](https://gitlab.addx.ai/DEV/cicd-server/-/tree/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1)。部署提交未知；下面是源码路由，不是已上线能力或 OpenCLI command registry。

机器清单 [source-api-catalog.json](source-api-catalog.json) 保存 74 条注册的方法、路径、handler、请求/响应类型源文件、service 调用入口、角色/状态门禁证据和回读要求。模型仅提供源入口，不能用空 body 替代模型。所有路由 executionGate 都保留目标授权、身份/资源/状态与部署核验。

离线复扫（不登录、不调业务 API）：

```bash
python3 skills/delivery/cicd-platform/scripts/scan_platform_api.py --root /tmp/internal-business-source/cicd-server --frontend-root /tmp/internal-business-source/cicd-fe --kind go --output skills/delivery/cicd-platform/references/source-api-catalog.json
python3 -m unittest discover -s skills/delivery/cicd-platform/tests -p test_platform_api_catalog.py
```

Snapshot 元数据为该目录 manifest.json；Go 读取全部 handler/router/request/response/model 文件，Java 读取完整 src/main/java（不读配置、.env）。snapshotCoverage 缺文件时不可声明完整。反射、框架生成、Actuator/Swagger/第三方组件端点不属于静态业务 Controller 清单；使用各组件官方文档，不复制成平台自有 API。

业务覆盖：上线单列表/详情/创建/编辑/作废/解锁/添加项目，用户及项目列表、分支与配置，PRE/GRAY/PROD 部署/取消/回滚/状态/日志，测试/校验/审批/灰度/完成，Apollo Review/Pre/Prod 发布、变更验证、JMeter，灰度配置/比例/开关/白名单/预扩容/切流/缩容、管理员挂起/恢复/跳步/回退。所有 74 个实际注册路由列于下表。项目页面模板是否能执行仍需 UI 证据，不能据后端注册宣称页面已实现。

权限源：handler/common.go 的 verifyPermissions 按当前用户角色精确匹配；每个 handler 的 permissionEvidence 给出角色列表。状态与锁检查不止路由中间件，必须检查 gateEvidence 对应完整 handler 与 db/release_mutation.go；不以 admin 代替状态门禁。模型状态 1→2→3→4→5→6 是普通模式，快捷/OnlyApollo 分支有独立路径。挂起、作废、不可取消项目、stage overlap、branch/commit、部署当前/历史版本及灰度预扩容目标均需资源回读。

源码已发现 FE 定义 /project/getProjectList、/task/getTaskProjectList、/task/publish/apollo/gray，而该 Go 注册均没有对应路由；是否死代码需由 FE owner 核验，这三项 blocked，不能生成直接请求命令。Apollo Gray 的 GET/POST 都未注册。工作流回退不撤销下游部署/发布。现有 SOP 的授权与灰度护栏仍适用。

只读业务通常经 GET，但 /logout、OAuth login/callback 为认证转换，不作为只读探测。写后按同 taskId 调 /api/task、/api/task/project/deploy/status 与 logs，灰度回读 /api/apollo/gray/config 或 ramp/status；检查实际部署 commit、步骤和比例，不盲重试失败 POST。其他写入须先选同资源的查询端点，未建立回读则 blocked。

实际路由索引（完整请求/响应、权限与 service 源入口见 JSON）：

| Method | Path | Handler/source | Effect evidence |
|---|---|---|---|
| POST | `/api/login` | [login](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | write-or-auth-transition |
| GET | `/api/logout` | [logout](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | write-or-auth-transition |
| GET | `/api/oauth/gitlab/login` | [GitLabOAuthLogin](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/oauth.go) | write-or-auth-transition |
| GET | `/api/oauth/gitlab/callback` | [GitLabOAuthCallback](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/oauth.go) | write-or-auth-transition |
| GET | `/api/user/info` | [CurrentUser](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | read |
| GET | `/api/users/qa` | [GetQAUsers](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | read |
| GET | `/api/users/rd` | [GetRDUsers](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | read |
| GET | `/api/users/op` | [GetOPUsers](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | read |
| POST | `/api/group/create` | [CreateGroup](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | write-or-auth-transition |
| POST | `/api/group/add/user` | [GroupAddUser](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | write-or-auth-transition |
| GET | `/api/groups` | [GetGroups](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/user.go) | read |
| POST | `/api/project/create` | [CreateProject](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| POST | `/api/project/add/group` | [ProjectAddGroup](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| POST | `/api/project/config/update` | [ProjectConfigUpdate](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| POST | `/api/project/config/update/custom` | [ProjectConfigUpdateCustom](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| POST | `/api/project/pipeline/update` | [ProjectPipelineUpdate](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| POST | `/api/project/resource/update` | [ProjectResourceUpdate](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| GET | `/api/project/repository/branchs` | [GetProjectBitbucketRepositoryTags](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | read |
| GET | `/api/project/get_gray_project_name` | [GetProjectWithGrayName](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | read |
| GET | `/api/project/get_gray_readonly_project_name` | [GetGrayReadonlyProjectName](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | read |
| GET | `/api/project/get_non_cancelable_project_name` | [GetNonCancelableProjectName](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | read |
| GET | `/api/projects` | [GetProjects](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | read |
| POST | `/api/pipeline/create` | [CreatePipeline](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/project.go) | write-or-auth-transition |
| GET | `/api/gitlab/groups` | [GetGitLabGroupList](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/gitlab.go) | read |
| GET | `/api/gitlab/projects` | [GetGitLabProjectList](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/gitlab.go) | read |
| POST | `/api/task/create` | [CreateTask](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/update` | [UpdateTask](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/disable` | [DisableTask](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/unlock` | [UnlockTask](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/add/project` | [TaskAddProject](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| GET | `/api/task` | [GetTaskByID](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/task/projects` | [GetTaskProjects](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/task/builds` | [GetTaskBuilds](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/task/build/deploy` | [GetTaskBuildDeployByEnv](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/task/project/build` | [GetTaskBuildByProject](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/task/project/deploy` | [TaskProjectDeploy](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/deploy.go) | write-or-auth-transition |
| POST | `/api/task/project/cancel/deploy` | [TaskProjectCancelDeploy](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/deploy.go) | write-or-auth-transition |
| GET | `/api/task/project/deploy/status` | [GetTaskProjectDeployStatus](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/deploy.go) | read |
| GET | `/api/task/project/deploy/logs` | [GetTaskProjectDeployLogs](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/deploy.go) | read |
| POST | `/api/task/project/rollback` | [TaskProjectRollback](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/deploy.go) | write-or-auth-transition |
| POST | `/api/task/verify` | [TaskVerify](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/cancel/verify` | [TaskCancelVerify](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/audit` | [TaskAudit](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/check` | [TaskCheck](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/gray` | [TaskGray](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/close` | [TaskClose](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/suspend` | [TaskSuspend](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task_admin.go) | write-or-auth-transition |
| POST | `/api/task/resume` | [TaskResume](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task_admin.go) | write-or-auth-transition |
| POST | `/api/task/step/skip` | [TaskStepSkip](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task_admin.go) | write-or-auth-transition |
| POST | `/api/task/step/rollback` | [TaskStepRollback](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task_admin.go) | write-or-auth-transition |
| GET | `/api/task/review/apollo` | [TaskGetReviewApollo](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/task/review/apollo` | [TaskReviewApollo](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| GET | `/api/task/publish/apollo/pre` | [TaskGetPublishApolloPre](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/task/publish/apollo/pre` | [TaskPublishApolloPre](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| GET | `/api/task/publish/apollo/prod` | [TaskGetPublishApolloProd](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/task/publish/apollo/prod` | [TaskPublishApolloProd](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/verify/apollo/change` | [TaskVerifyApolloChange](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/jmeter/run` | [TaskJmeterRun](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/task/jmeter/gray/run` | [TaskJmeterGrayRun](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| GET | `/api/tasks` | [GetTasks](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/apollo/gray/config` | [GetGrayConfig](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/apollo/gray/config` | [SetGrayConfig](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/ratio` | [UpdateGrayRatio](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/ramp` | [GrayRamp](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| GET | `/api/apollo/gray/ramp/status` | [GrayRampStatus](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| GET | `/api/apollo/gray/ramp/logs` | [GrayRampLogs](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | read |
| POST | `/api/apollo/gray/ramp/confirm` | [GrayRampConfirm](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/ramp/descale` | [GrayRampDescale](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/whitelist/add` | [AddGrayWhitelist](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/whitelist/remove` | [RemoveGrayWhitelist](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/release` | [GrayRelease](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/release/batch` | [BatchGrayRelease](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/enable` | [EnableGray](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |
| POST | `/api/apollo/gray/disable` | [DisableGray](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task.go) | write-or-auth-transition |

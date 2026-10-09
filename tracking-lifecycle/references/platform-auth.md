# 平台鉴权与 API 自动化

核对日期：2026-10-03；平台源码 commit
`d61e1692c3d28224e4d642f7bdf765a6a982ab0d`。
来源是后端源码，不能据此宣称目标环境已经部署同一版本。
路由全集见 [Controller 清单](platform-api-catalog.md)。

## 当前支持什么

| 凭据 | 请求方式 | 已核对边界 |
|---|---|---|
| 飞书账号登录 Session | 服务端 Session Cookie | OAuth code/state 回调将用户存入 HttpSession；角色与应用权限仍有效 |
| Personal Access Token | `Authorization: Bearer $TMT_TOKEN`，值为 `tmt_…` | 继承创建者当前角色/权限，写操作仍受应用 owner 等校验 |
| Project Access Token | 同一 Bearer 头，值为 `tmp_…` | Filter 仅允许 GET，校验启用状态/有效期；可配置 appId 绑定 |
| MCP API Key | `/api/mcp/**` 的 `X-MCP-API-Key` | 独立 Filter；不是 PAT、Project Token 或 JWT，不作为绕过权限的替代入口 |
| JWT | 无已核对的入口 | 当前源码没有 JWT 签发、验签或 refresh 实现；Bearer 是传输头，不代表 JWT |

直接证据：
[ApiTokenAuthFilter](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/config/ApiTokenAuthFilter.java)、
[LoginInterceptor](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/config/LoginInterceptor.java)、
[LoginServiceImpl](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/service/impl/LoginServiceImpl.java)、
[McpApiKeyFilter](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/config/McpApiKeyFilter.java)。

任意 JWT 字符串不会被该 Bearer Filter 认证；请求会继续进入其它处理，
没有有效账号 Session 的请求不能因此获得登录状态。飞书自身的
access_token 是后端调用飞书的凭据，不能用作埋点平台 API JWT。

## Skill 已有能力与缺口

`cli/src/platform.js` 的 `fetchTransport()` 只将传入 token 放入 Bearer
header，不识别 token 类型、不提供登录/刷新、Cookie jar 或 Project
Token 的客户端 GET-only guard。Project Token 的只读限制来自服务端；
业务项目若有 GET-only wrapper，应保留。不得以更换 token 绕过
`check-contract`、完整树漂移、sandbox 或生产发布门禁。

已有专用 `PlatformClient` 方法：读取工单、完整树与事件详情；创建/
更新事件、创建工单、查询未通过事件、校验、发布。
本次增加 `AdministrativeClient` 与 `api-catalog`、`api-plan`、`api-call`
统一入口；Base/Context 操作使用固定 operation 名称及 JSON 请求文件，
不要求逐步浏览器操作。它支持显式 PAT、Project、Session、MCP 模式，
在发送前检查凭据类型、只读及 Session-only 边界，关闭重定向，脱敏
响应并将凭据创建结果保存到私有文件。没有新增 JWT 登录。
`getUnpassedEvents`、`getReleaseEvents` 等名称虽像查询，HTTP 方法仍为
POST，Project Token 不能调用。GET 的 loginRedirect、feishuLogin、logout
会改变登录状态，也不应纳入通用只读枚举。

appId 绑定在 Filter 中设置 `apitoken.project.appId`，实际归属限制还
取决于各 Controller/Service 的使用；不能只凭 token 创建时 appId
非空就声称所有 GET 都已实现完整应用隔离。

## 凭据获取与日常使用

PAT 与 Project Token 创建接口分别为 `POST /api/access-tokens` 和
`POST /api/project-tokens`。两者都要求交互式账号 Session；Controller
拒绝已经通过 API token 认证的请求签发新 token。PAT 的 `expiryDays`
仅支持 7、14、30；Project Token 请求模型另见 [清单](platform-api-catalog.md)。
创建新凭据会新增持久访问能力，应按照宿主权限规则确认后在官方
登录页面完成，或使用已批准、保留 OAuth state 的登录工具；不从
浏览器存储导出 Cookie，不将登录响应或 token 明文打印出来。

日常 API 自动化优先用用户已授权的凭据，从受控环境/本机私有文件
注入目标进程，变量名使用 `TMT_TOKEN`。只读任务用 Project Token；
写入用具备目标应用权限的 PAT。正式发布身份按 CI 规则受限注入，
不能把个人交互凭据或 Project Token当作发布身份。

下面示例仅展示请求形状；前提是 token 已由受控来源注入。目标 origin
必须是本次实际平台，关闭 shell trace，禁止 token 出现在参数、URL、
仓库、issue、报告、错误日志或聊天中。需要 curl 的场景用 stdin
传递 header，避免将展开后的 header 放入进程 argv：

```bash
set +x
printf 'Authorization: Bearer %s\n' "$TMT_TOKEN" |
  curl --fail-with-body --silent --show-error --header @- \
  "$TRACKING_PLATFORM_BASE_URL/api/baseSchema/list?applicationId=$TRACKING_APPLICATION_ID"
```

写前检查当前用户/角色、目标应用 owner、唯一活跃工单和完整树。
HTTP 成功仍需检查 `code=200` 且 `success` 不为 false；401 也可能
封装在 HTTP 200 中。写入超时或结果不明先回读，不能盲重试。

## 如何更新此结论

若平台日后增加 JWT/OIDC，先固定新的后端与部署修订，核对 issuer、
audience、签名/有效期、角色和应用权限，再补获取/刷新入口及测试。
不要自行签造 token，不复制其它平台 JWT，也不把 API 文档更新当作
鉴权功能已实现。

## 通用 CLI 操作

要求 Node.js 20+，在 Skill 目录运行。先安装锁定依赖 `npm ci --ignore-scripts`；三个命令使用同一固定目录：

```bash
node cli/bin/events-tdd.js api-catalog
node cli/bin/events-tdd.js api-plan --operation=baseSchema.getDetailByBaseSchemaId --request=detail.json
node cli/bin/events-tdd.js api-call --operation=baseSchema.getDetailByBaseSchemaId --request=detail.json --auth=pat
```

`detail.json` 为 `{"query":{"id":103}}`；`--operation` 必须是目录中的完整 ID。`api-plan` 离线且不需要凭据；`api-call` 使用实际 `TRACKING_PLATFORM_BASE_URL` HTTPS origin，并通过进程环境注入凭据。默认 `--auth=project`，其余显式为 `pat`、`session`、`mcp`。

| 模式 | 唯一凭据环境变量 | 适用范围 |
|---|---|---|
| `project` | `TMT_TOKEN`（tmp_） | GET 且无登录状态副作用 |
| `pat` | `TMT_TOKEN`（tmt_） | 获授权的平台操作，应用/角色权限仍由后台检查 |
| `session` | `TMT_SESSION_COOKIE` | 已授权的官方登录会话；不得从浏览器存储导出 |
| `mcp` | `TMT_MCP_API_KEY` | 仅 `/api/mcp`，不能替代平台凭据 |

进程只提供一个模式对应的凭据，清除其他模式变量，混用即报错。请求 JSON 仅允许 `path`、`query`、`body`：

- 路径参数：`{"path":{"id":3}}`，ID 必须是正整数规范形式。
- 查询参数：`{"query":{"applicationId":3396}}`；未声明的参数会被拒绝，必填性和业务值由对应 DTO/后台检查。
- JSON 请求体：Base 新建形状 `{"body":{"applicationId":3396,"name":"shared","parameters":[]}}`；真实写入前核对应用及完整参数，不复制示例盲写。
- 路径与请求体结合：Token 状态操作形状 `{"path":{"id":3},"body":{"status":0}}`。

创建 PAT/Project Token 等秘密响应还必须传入 `--private-output=<新文件>`：其父目录须已存在、非符号链接且权限仅限所有者；文件排他创建为 0600，不覆盖既有文件。先准备私有目录，再按官方 Session 流程调用创建操作；stdout 只报告私有保存状态。目录或清理失败不回显原始路径，API 结果不明或凭据保存失败时先核对状态，不重复签发。

事件创建/更新、飞书表格批量导入、工单创建与生产发布保留 `post`、`create-workorder`、`publish` 门禁，通用 `api-call` 拒绝直接调用。该入口不声称已实现专用批量导入投影；目录和离线计划仍可用于审查接口。其余 API 写入响应的 `API_ACKNOWLEDGED` 与 `readback:PENDING` 仅表示收到响应，必须通过对应读取操作核对结果。

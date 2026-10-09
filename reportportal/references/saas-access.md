# ReportPortal 实例、认证与能力入口

唯一owner：`reportportal`。仓库消费者声明生产入口 `https://reportportal.builder.addx.live`，staging入口 `https://reportportal-staging.builder.addx.live`；二者来自设备云脚本，不证明当前Ingress可达。部署版本、实际SSO、subject/项目角色和线上认证均pending，本次未登录、读凭据或调用业务API。部署证据见[source-discovery.json](source-discovery.json)。

## 认证申请、消费与撤销

[官方认证文档](https://reportportal.io/docs/log-data-in-reportportal/HowToGetAnAccessTokenInReportPortal/)区分UI用户access token与agent API key，均通过Bearer header认证。个人Profile → API Keys可创建和撤销key；key只在创建时显示。同一用户的多个key权限等价，命名不形成独立scope，创建新key不会自动撤销旧key。申请最小project角色后，由宿主安全注入官方MCP所需`RP_API_TOKEN`；endpoint/project采用非敏感`RP_HOST`/`RP_PROJECT`。巡检消费者现有`SMOKE_RP_TOKEN`/`SMOKE_RP_ENDPOINT`/`SMOKE_RP_PROJECT`只是CI变量别名，不能据此认定token身份、权限或过期策略。

实际AddX SSO提供者、允许的登录方式、key是否启用、期限/轮换策略、撤销回执和UI token刷新行为未核验；不使用公开文档的password grant绕过实际SSO。日常登录/隧道只调用`web-access`。获明确授权后由本人按部署版本的Profile管理key；撤销后以相同身份/项目的授权小范围验证记录结果，不重新发key或换身份绕过拒绝。

## 身份、资源和业务路由

先从当前UI用户Profile和项目成员/角色的非敏感信息确认subject及scope，且需与宿主凭据申请记录匹配；不得导出key值。匹配版本后可用官方MCP列出指定project的一条launch，或[官方MCP README](https://github.com/reportportal/reportportal-mcp-server/blob/main/README.md)记录的GET `/api/v1/{project}/launch`，带小分页/已知ID过滤。成功只证明该凭据当前可读该资源，不证明人名、管理员角色或写权限；空结果也不证明不存在其它项目。UI登录成功不证明API key有效。

优先[官方ReportPortal MCP Server](https://github.com/reportportal/reportportal-mcp-server)及[releases](https://github.com/reportportal/reportportal-mcp-server/releases)。其环境输入是`RP_HOST`/`RP_API_TOKEN`与可选`RP_PROJECT`；project可被工具参数覆盖，执行时必须逐次核对授权目标。main README声明MCP 1.x要求ReportPortal ≥25.1且API service ≥5.14.0；AddX版本未知，不能直接认定兼容或已部署MCP。当前公开查询能力涵盖launch、item、日志/附件和历史；force-finish、delete、import、分析及defect更新都有副作用，按owner写门禁执行。以安装版本README及实际tools/list为准，不把develop分支未来能力当线上能力。本次检索未定位ReportPortal发布的独立SKILL.md包，不采用第三方Skill冒充官方入口。

API能力从[官方自动生成service-api文档](https://reportportal.io/docs/category/api/service-api)与[开发者指南](https://reportportal.io/docs/developers-guides/)按部署版本发现；[Reference API](https://developers.reportportal.io/api-docs/api-design/reportportal-reference-api/)只覆盖预设计endpoint，不能当完整API清单。发现读写路径不等于获权，创建/finish/更新/删除/导入、自动分析及管理操作须核对目标和资源角色，完成后回读；不会复制整套公开API。

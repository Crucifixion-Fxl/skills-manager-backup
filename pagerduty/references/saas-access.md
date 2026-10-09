# PagerDuty 实例与认证

唯一owner：`pagerduty`。声明入口：`https://addx-oncall.pagerduty.com`。provider保留delegated，只委派本契约，不表示统一runtime adapter或MCP已接入；部署版本/实际subject/权限均pending。本次未登录、读凭据或调用业务API。

## 认证申请、消费与撤销

现有helper宿主PAGERDUTY_API_TOKEN，以Authorization: Token token=访问https://api.pagerduty.com。官方区分account token与user token，前者可选read-only/full，后者随用户权限；不能从变量名推断token类型/subject。官方账号API Access Keys及用户API设置管理申请/撤销，操作须明确授权。实际AddX SSO、token类别/权限、生命周期和账号归属pending。

## 身份与能力路由

优先官方hosted remote MCP https://mcp.pagerduty.com/mcp（API token或OAuth）及REST API docs；官方local MCP仓库现已deprecated，不能当首选新安装。实际tools/list和当前账户scope决定可用能力。现有incident helper保持业务consumer；小范围读取精确incident/account关联证据不证明写权限。acknowledge/resolve、通知/派单、service/schedule/escalation修改与事件发送需明确授权和SRE门禁；REST与Events API分开。

访问恢复只调用`web-access`。认证/版本漂移交由`platform-onboarding`更新本owner；source-only发现不得升级为线上验收。业务gate见[原消费者契约](../../../infrastructure/sre-agent/SKILL.md)；写必须明确目标/payload、最小权限、独立业务确认/发布门禁及写后回读，结果未知先对账。

## 官方或私有source入口

- [https://docs.pagerduty.com/developer/authentication](https://docs.pagerduty.com/developer/authentication)
- [https://docs.pagerduty.com/account-admin/api-access-keys](https://docs.pagerduty.com/account-admin/api-access-keys)
- [https://docs.pagerduty.com/developer/rest-api-overview](https://docs.pagerduty.com/developer/rest-api-overview)
- [https://docs.pagerduty.com/developer/mcp-tooling-remote-server](https://docs.pagerduty.com/developer/mcp-tooling-remote-server)
- [https://github.com/PagerDuty/pagerduty-mcp-server](https://github.com/PagerDuty/pagerduty-mcp-server)

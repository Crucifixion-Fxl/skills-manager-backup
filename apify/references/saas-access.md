# Apify 实例与认证

唯一owner：`apify`。声明入口：`https://console.apify.com`。provider保留delegated，只委派本契约，不表示统一runtime adapter或MCP已接入；部署版本/实际subject/权限均pending。本次未登录、读凭据或调用业务API。

## 认证申请、消费与撤销

现有VOC使用宿主APIFY_TOKEN。官方Console API & Integrations提供API token；只检查是否已注入，不输出值或读.env。官方hosted MCP https://mcp.apify.com 支持OAuth或Bearer token；OAuth与现有脚本token是独立消费路径，未连接验证。不得沿用消费者token query/echo示例；请求凭据放受保护header，且不记录header。账号/组织、token权限、有效期、轮换/撤销均pending。

## 身份与能力路由

Apify 官方 API 完整，已具备 API Token 时直接通过 API 验收身份及获权只读资源，不要求并行网页登录或 Cookie 转发。宿主未注入 Token 时请求凭据注入；不自动打开网页登录页。用户明确需要申请凭据或处理登录时，才使用辅助浏览器。

优先官方agent-skills及hosted MCP，再按API v2文档选择确切只读资源。Actor运行、build、schedule和创建/改删storage不仅是查询，可能产生费用或外部采集，需明确范围、成本/资源上限和授权。先核对subject与已有run/dataset归属；读取限已知ID和小分页，不为了验证登录启动Actor。

访问恢复只调用`web-access`。认证/版本漂移交由`platform-onboarding`更新本owner；source-only发现不得升级为线上验收。业务gate见[原消费者契约](../../../customer-care/voc-analysis/SKILL.md)；写必须明确目标/payload、最小权限、独立业务确认/发布门禁及写后回读，结果未知先对账。

## 官方或私有source入口

- [https://github.com/apify/agent-skills](https://github.com/apify/agent-skills)
- [https://docs.apify.com/integrations/mcp](https://docs.apify.com/integrations/mcp)
- [https://docs.apify.com/api/v2](https://docs.apify.com/api/v2)

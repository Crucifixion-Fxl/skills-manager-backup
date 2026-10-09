---
name: pagerduty
description: PagerDuty平台唯一接入owner，维护真实入口、身份权限、官方能力或私有源码路由与凭据生命周期；业务调用遵守消费者门禁，未知运行态明确pending。
---

## Description

本平台接入唯一正本为[saas-access.md](references/saas-access.md)，认证与源码事实见[auth-profile.json](references/auth-profile.json)、[auth-discovery.json](references/auth-discovery.json)、[source-discovery.json](references/source-discovery.json)。日常访问调用`web-access`；首次/认证或部署变化调用`platform-onboarding`更新本owner，不复制通用登录、隧道或浏览器教程。

## Rules

1. 先核对实例、版本、subject与目标资源scope；source-verified不代表runtime-verified。未知事实先按reference研究，不猜API、不换身份或套其它平台认证。
2. 凭据仅由宿主注入；不读Secret、vault、.env或token/session缓存，不在argv、URL、日志、仓库、聊天或回执输出凭据。申请/轮换/撤销需明确授权。
3. 默认小范围获权读取；业务写需本次明确授权、完整目标/payload与权限，保留消费者发布/审批/生产门禁。写后回读，结果未知先对账不重放。工具存在不证明只读或获权。
4. 保留[业务消费者门禁](../../infrastructure/sre-agent/SKILL.md)并按接入reference路由；认证、版本或权限变化使相关证据失效。

## Steps

1. 读取references，区分source-only与runtime事实；补齐缺失scope/版本后才调用。
2. 优先官方Skill/MCP/API或私有service源码入口，复用现有业务执行器，不复制全API。
3. 记录环境、版本、subject/资源的非敏感证据、授权范围、时间/退出码、回读和pending能力。

## Native OAuth 与只读消费者

- 保留官方 REST API Token 与 OAuth 两种认证；网页登录并存，可辅助正常授权，但没有明确官方互换契约时，不能把浏览器 Cookie 当 REST Token/Bearer。已有获权凭据通过宿主私有 stdin、进程或加密 SSH 注入，不在聊天、argv、文件或输出中暴露。
- 优先已有注册客户端的用户授权码流程。Classic User OAuth 的 `read` 范围与用户实际权限取交集；原生 non-confidential 客户端不使用 client secret，必须 PKCE，选择 S256。Scoped OAuth 要求 confidential client、client secret 与 PKCE；用户授权与 client_credentials 机器身份分别核验。请求 scopes 不等于实际授予 scopes，需私下验证实际 grants、回调和端点支持。
- 当前官方未广告标准 device grant/device authorization endpoint，不能臆造 CLI device 流程。Discovery 中的 registration endpoint 不证明 DCR 可用；官方托管 MCP 当前不支持 DCR，未知 existing client/scopes 时不自动注册或创建 Token。
- 托管 MCP 包含读取和管理写工具，当前没有服务端工具过滤。以只读 OAuth scopes 加客户端读取动作白名单及本机 native 只读门禁限制执行；旧归档本地 MCP 的默认只读不能作为托管服务保证。
- `GET /users/me` 仅使用用户级 API Token 或用户 OAuth，account Token 不能证明本人身份。私下核验预期 name/email，role 不等于目标资源权限；只输出获权的 name/email/role。本人响应可能自动含日历 bearer URL，即使未添加 include 参数；禁止输出、落盘或转发这些 URL。投影不等于服务端最小响应，先确认上游读取范围，再执行；不得为探针调用日历密钥轮换。
- 固定官方区域/API origin、HTTPS、拒绝重定向，限制时间与响应大小，不自动重试。公开文档或静态 source-only 合同不计真实登录/两端验收；网页登录退出不证明原生 OAuth/API Token 已撤销，按实际临时资源清理记录。

官方合同：[OAuth](https://docs.pagerduty.com/developer/oauth-functionality)、[当前托管 MCP](https://support.pagerduty.com/main/docs/pagerduty-mcp-server)、[Current user](https://docs.pagerduty.com/developer/api/reference/rest/users/get-current-user)、[API Changelog](https://docs.pagerduty.com/developer/api-changelog)。

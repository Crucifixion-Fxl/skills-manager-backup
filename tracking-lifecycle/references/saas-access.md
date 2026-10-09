# 埋点平台：SaaS 接入入口

认证与业务能力正本：[references/platform-auth.md](platform-auth.md)。平台 owner：`tracking-lifecycle`；索引 profile：[auth-profile.json](auth-profile.json)。

当前依据：Session / PAT / Project Token。策略：试点：本机代理 + 既有 REST；Project Token GET-only。已完成源码分档与 [device-cloud-host 只读线上试点](live-device-host-acceptance.json)：本人身份/App 核验、7 个 OpenCLI 查询、跨 App/原始写入/撤销后请求拒绝均通过。完整功能、写入与角色覆盖仍待实测；不得把该 reference 视为已登录或全站命令可用。

日常使用：先按原认证契约复用官方工具/已有凭据并验证 identity 与目标权限；需要本机浏览器或远程开发时使用 [web-access](../../../web-access/SKILL.md)。首次发现功能缺口或可复现漂移时，使用 [platform-onboarding](../../../platform-onboarding/SKILL.md)，把命令、sitemap/覆盖、输入/输出、角色/范围和验证回执维护在本平台 reference 中。现有业务审批/发布门禁继续适用。

埋点 REST 源码清单见 [platform-api-catalog.md](platform-api-catalog.md)，OpenCLI 与网站入口/缺口见 [opencli-access.md](opencli-access.md)。当前 Session 不接受飞书 OpenAPI Token；PAT/Project Token 与 Session 是独立消费面。

平台 Token 申请/私有保存/消费/撤销见 [统一 Token 生命周期](../../../web-access/references/token-lifecycle.md)。Token 的 userId 探针使用 `/api/role/current`，当前 Session 探针仍为 `/api/user/getCurrentUser`。issuer 仅有源码与本地 fixture 证据，本轮没有线上申请。

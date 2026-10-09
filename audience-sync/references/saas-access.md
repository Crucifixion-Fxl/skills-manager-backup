# audience-sync：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：Audience Project Personal key (not browser session / service provider key)。

复用 AUDIENCE_SYNC_API_KEY 或多别名 AUDIENCE_SYNC_API_KEYS；只读验证每把用户已提供 key 的 Project 与允许操作。缺少/无效 key 仅返回原生 Audience 入口，由用户在应用内申请 Project Personal key。

Bearer Project Personal key；不能用 Brevo/Athena/平台服务 secret 替代；别名不是权限证据。

身份与权限：GET /api/platform/v3/personal-key（get_project_personal_key_context），然后目标 Project capabilities；Project 匹配、owner scope、allowed operations 按返回值。

失效与撤销：key expiry/revoke UI 与服务端实现未在发布契约中说明；需平台 owner 提供 key 生命周期/撤销回读，失效时返回入口不循环重试。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

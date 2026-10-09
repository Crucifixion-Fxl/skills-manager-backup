# qatools：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[references/authentication-and-environments.md](authentication-and-environments.md)。

认证路由：QA Tools platform API key first; Micro App Feishu JWT fallback。

复用 QA_TOOLS_TOKEN → password store qatools.staging-api-key → qatools.staging；缺失时管理员在 /api-keys 创建/refresh 30 天 key。JWT 仅无 key 或用户选择时由用户在 Micro App qa-tools-staging 入口登录并本机安全保存，不程序化 SSO。

Bearer qatools_ API key 或 SSO JWT；key 30 天，JWT 数小时；不得静默降级凭据类型。

身份与权限：源码 apiKeyAuth/auth 中绑定 key owner 或 JWT user；健康检查不证明 identity。需补部署已支持的只读 identity/permission probe，业务 mutation 不作认证试验。

失效与撤销：API_KEY_INVALID/EXPIRED/REVOKED 停止交管理员更新；refresh key 会撤销旧 key；JWT expiry 由用户重新获取。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

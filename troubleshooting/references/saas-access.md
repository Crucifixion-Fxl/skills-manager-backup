# troubleshooting：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[references/auth_flow.md](auth_flow.md)。

认证路由：TROUBLESHOOTING_TOKEN → get_token.mjs region cache → business Feishu OAuth。

复用注入变量；缺失时用 owner get_token.mjs us/eu（stdout 仅捕获到进程变量）。它验证区域缓存、打开飞书业务授权，prepare/confirm 换取平台 JWT。普通 lark-cli 不提供此 JWT。

Bearer JWT；owner 契约记载 24 小时；缓存 ~/.troubleshooting-token-{region}。跨环境兼容是既有契约，仍按目标环境验证。

身份与权限：GET /api/current，success=true，result.data.userid；业务权限另核对目标 OpenAPI。

失效与撤销：401 后清除失效变量/缓存并恢复一次；JWT 服务端撤销入口未找到，不能把删本地缓存称撤销。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

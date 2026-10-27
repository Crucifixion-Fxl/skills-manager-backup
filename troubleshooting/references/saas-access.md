# troubleshooting：认证与源码入口

日常认证行为正本：[auth_flow.md](auth_flow.md)；机器发现摘要：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。

认证路由：宿主注入 TROUBLESHOOTING_TOKEN（PAT / JWT）→ 目标环境核验 → 业务 API；无凭据且选择交互 JWT 登录时才进入 get_token.mjs → business Feishu OAuth。

已有 PAT 不调用登录脚本；长期 PAT 从生产微应用个人 API Access Token 页面创建，经宿主安全注入。get_token.mjs 只获取 JWT，stdout 仅捕获到变量，不直接展示；参数按目标选 us/eu/staging-us/staging-eu。缓存和新 JWT 均检查目标 HTTP、业务成功及 userid，已确认 JWT 401 时用 --force-login 跳过旧缓存；403/503/网络或业务失败不自动登录。详见认证正本；普通 lark-cli 不提供业务 JWT。

Bearer PAT 无需交换/刷新 JWT，期限以创建时设置和服务端上限为准；业务 JWT 既有契约为 24 小时。仅 JWT helper 使用 ~/.troubleshooting-token-{region}，PAT 不进入该缓存。共享认证平台不等于所有环境或资源均有权限。

身份与权限：目标环境 GET /api/current，同时核验 HTTP、业务成功及 result.data.userid；业务权限另核对目标 OpenAPI。auth-profile 当前仅配置 Prod-US，响应假设 success=true，runtimeVerification 仍 pending；不可将该 profile 的成功当作其他环境验收，响应不匹配时停止并核对目标契约，不跳过业务校验。

失效与撤销：PAT 401 停止并检查到期/禁用/撤销/环境支持，不自动换 JWT；来源明确的 JWT 401 才重新登录一次，仅安全可重放请求可重试；未知类型先确认来源。403 检查账号/权限，503/超时检查依赖，不换凭据绕过。创建、轮换、禁用或撤销 PAT 需明确授权；清理本地缓存不是服务端撤销。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

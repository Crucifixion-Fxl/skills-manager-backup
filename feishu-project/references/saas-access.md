# feishu-project：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：Feishu Project Plugin Token + authorized X-USER-KEY OR official Project OAuth MCP。

复用 FEISHU_PLUGIN_ID/SECRET/USER_KEY 且代理用户已授权 → owner Python/local MCP；不完整则仅使用已授权官方 Project OAuth MCP authenticate。普通 lark-cli 与业务 feishu-auth 都不能替代 Project 身份。

Plugin token 客户端内存缓存 2h，X-USER-KEY 是实际用户；Plugin 权限 ∩ 安装空间 ∩ 用户权限；local MCP 固定 FEISHU_PROJECT_KEY。

身份与权限：Plugin 先核对授权 user key 并读取目标空间/字段；OAuth MCP current_login_user；不同路径 identity 不能互换。

失效与撤销：预过期刷新；401 不自动重试，调用方重建 client/受控刷新一次；插件凭据由开放平台管理，OAuth 撤销走官方授权管理。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

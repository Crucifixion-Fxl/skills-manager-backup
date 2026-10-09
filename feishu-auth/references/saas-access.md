# feishu-auth：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：@a4x/feishu-auth-cli business OAuth (not @larksuite/cli)。

复用该 CLI status；业务 owner 明确依赖且已授权时调用其 login/refresh。使用 SKILL 指定内部 npm registry 与版本；不为普通飞书资源或消息改用此身份。

效能业务 OAuth access/refresh token；stdout 仅捕获到进程变量，禁止终端/日志输出。

身份与权限：CLI status 只证明本地认证状态；目标业务平台 identity 与 permission 必须由业务 owner 契约验证。

失效与撤销：用 CLI refresh 恢复失效会话；logout/revoke 的版本支持需读 CLI --help 和发布源码后确定，不能虚构命令。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

# device-cloud-test-management：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[references/cli-contract.md](cli-contract.md)。

认证路由：DeviceCloudAuthClient Casdoor SSO / operating-system refresh credential。

优先 owner CLI 的同进程 session、系统 keyring refresh；缺失由 CLI 读取 /auth/cli/config，仅一个进程打开用户授权浏览器，校验 loopback state 后 POST /auth/cli/token。缺少安全 keyring 直接失败。

Access token 仅内存；refresh token 只存系统 keyring/Windows Credential Manager，轮换原地更新；不降级明文。PAT 是另一候选自动化链，不擅自切换。

身份与权限：GET /auth/userinfo 校验 email；服务端签名/iss/aud/exp 验证；GraphQL 资源容量与 Job 权限分别核验；GitLab 用例仓只读权限另验证。

失效与撤销：POST /auth/cli/refresh 复用 refresh；撤销/CLI logout 尚需确认可支持版本，不虚构命令。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

# device-setting-override：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：revenue-sharing /skill-api LDAP token proxy (default); explicitly authorized AK/SK only。

优先复用 SKILL_LDAP_TOKEN；缺失由本机安全凭据注入 POST <rs-host>/skill-api/login {ldapCn,password}，不将密码放命令行/聊天。生产 RS host 在 owner 契约中待部署，不能自动切换 AK/SK。

raw Authorization LDAP token，3 天；RS 服务端托管 PaasConfig AK/SK 与节点路由；显式 AK/SK 模式 IOT_ADMIN_AK/SK 做 URL HmacSHA1。

身份与权限：login 返回 ldapCn/expiresInSeconds 是申请回执；代理审计归属 LDAP cn；目标 node/environment 和只读设置回读单独验证。稳定独立身份探针尚缺。

失效与撤销：POST /skill-api/logout raw Authorization 幂等撤销；过期重新 LDAP 登录，生产部署与 proxy ACL 需核实。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

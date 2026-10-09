# firmware-mgr：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：Firmware native SSO browser cookie token → token HTTP header。

复用 FIRMWARE_MGR_TOKEN/FIRMWARE_MGR_TEST_TOKEN；缺失由用户在正确域网页 SSO 登录，宿主安全注入 token cookie 到进程变量，禁止 echo/聊天索要明文。现有契约没有证明 IdP 是 Casdoor。

token header（不是 Authorization Bearer）；Cookie 本身不足 API 认证；所有业务 POST JSON {}。

身份与权限：POST /api/get_models 或 get_versions，result=0 是有效凭据/该读取权限；不能当稳定 subject 证明。get_user_info 既有 header/body bug 不能直接作探针。admin/developer/product 权限单独核验。

失效与撤销：result 10001/no auth 失效或权限不足；不盲重试；expiry/revoke 与稳定 subject 字段待源码/部署确认。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

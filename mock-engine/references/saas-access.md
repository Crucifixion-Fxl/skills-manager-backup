# mock-engine：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：local Docker Compose infrastructure; no SaaS login。

复用本地 Docker 权限、已隔离项目 compose 与受控本地配置；检查 docker compose version 与项目 mock/Makefile。没有需要登录的 SaaS 页面，不走飞书/Casdoor。

本地 fixture 配置仅限隔离 mock；云端或生产凭据不得导入 mock。

身份与权限：mock-status/容器健康只验证依赖环境；业务应用身份由目标应用自有测试 fixture 验证。

失效与撤销：停止 mock-down；删除 volume/reset 属独立有副作用操作；外部 key 的撤销归原平台。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

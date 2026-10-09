# lingxing-api：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：LingXing docs-site access session + NocoDB API token (two independent systems)。

先复用合法文档浏览器 session 与 NOCODB_TOKEN；文档站缺访问权限时由用户通过实际网页入口/平台 owner 申请，不能复制旧 SKILL 硬编码 Access Key。NocoDB 原生登录后 API Tokens 页面申请最小权限。

文档站 Cookie access mechanism 的发行/expiry 尚未确认；NocoDB xc-token。此 skill 录入路径配置，不直接申请 ERP 业务 app secret。

身份与权限：文档 sidebar/目标 markdown 可读，NocoDB AmazonApi.lingxing 目标表只读去重；两者分别验证，不假设共享身份。

失效与撤销：文档 key 撤销入口待厂商明确；NocoDB Token 页面 revoke。录入仅允许读取类 API 配置，写配置仍按 owner 门禁。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

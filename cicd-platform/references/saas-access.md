# cicd-platform：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：CICD GitLab OAuth → system JWT → browser task session。

打开 /#/task/list；复用原会话，无会话点击 GitLab 登录由用户完成 SSO/MFA；回到上线单页并核对操作者。候选源码 GitLab OAuth 使用 state cookie，后端换 code 后生成系统 JWT；不使用 Casdoor。

前端 TOKEN__ 位于带前缀 COMMON__LOCAL__KEY__ 缓存结构；不能当成 localStorage 单一 token key。源码含 query token callback，页面 URL 不能原样记录。

身份与权限：候选 GET /api/user/info，code=0，result.userId（uint stable ID）；result.username/roles 提供身份与角色上下文。前端 /user/info + API prefix /api + Bearer；部署 SHA 未匹配，仍待 runtime-only probe。上线单/项目/部署权限分别核验。

失效与撤销：候选 OAuth 生成系统 JWT 默认 12h；GET /logout handler 只返回 success，没有撤销 JWT。前端清除 TOKEN__/USER__INFO__ 仅清本地会话；服务端撤销/refresh 策略仍待核验。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，CICD profile 仅激活源码确认的 Bearer 身份探针，浏览器 nested cache 适配仍 pending。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。

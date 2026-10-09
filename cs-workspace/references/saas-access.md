# cs-workspace：认证与源码入口

认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、研究版本与部署缺口：[source-discovery.json](source-discovery.json)。业务契约：[SKILL.md](../SKILL.md)。

首选源码为 [CUS/CSTools](https://gitlab.addx.ai/CUS/CSTools) 的 `dev-react` 分支，研究 commit `8231a010eb5b05261f7fb7a5004249d97ede714b`。[部署路由 runbook](https://gitlab.addx.ai/DEV/k8s/-/blob/84312a8e75e315e685181c6ceef603c3a0569555/docs/runbooks/csworkspace-db-endpoint-cutover-runbook.md) 第 28–29 行指定项目 128，并将项目 129 `CUS/CSWorkSpace` 标为已废弃的 pre-monorepo 布局。旧 Flask 源码仅作历史证据；源码路由冲突已解决。runbook 描述手工 Docker Compose，但不证明今天的部署方式或版本；不得默认用 `dev` 替换 `dev-react`。

FastAPI 入口为 `apps/csworkspace/backend/app/main.py`，注册的认证实现位于 `api/api.py`、`api/endpoints/auth.py`、`api/deps.py`、`core/security.py`、`schemas/user.py`（均相对该 `app/`）。router 和 OpenAPI 使用 `settings.API_V1_STR`，认证 prefix 为 `/auth`；与 owner `/api/v1/auth/token`、`/login`、`/me` 契约一致，但有效 API prefix、部署 SHA、OpenAPI 和配置对应关系仍待核验。

先复用 `CS_WORKSPACE_TOKEN`；缺失时用户在 CS Workspace 自身登录页登录，或在当前任务授权范围内安全注入凭据至 `POST /api/v1/auth/token`（OAuth2 form）或 `/api/v1/auth/login`（JSON）。两者通过 LDAP 与本地用户验证后签发 JWT，`sub` 为本地 username；无 Casdoor/飞书 issuer 替换。浏览器 localStorage 消费方式不授权缓存采集或披露 token。

`GET /api/v1/auth/me` 验证 JWT 签名、expiry 和 sub，再按 username 查数据库，返回 numeric `id`、`username`、email/display metadata 与 groups。稳定身份候选是服务/环境范围内的 DB id，JWT 绑定为 username，不能跨独立安装比较 numeric id。身份应通过 cookie-free fixture 私下绑定；groups 不证明操作/资源权限。当前 active-user dependency 未强制 `is_active`；非管理员检查可能受 `PERMISSION_CHECK_DISABLED` 影响，有效配置与各 handler 权限需独立核验。

JWT 过期由 `ACCESS_TOKEN_EXPIRE_MINUTES` 控制，有效值未知。注册的 `POST /auth/logout` 仅返回前端 logout 成功，JWT blacklist 为未来工作；未发现实际 refresh/revocation handler。历史 Flask Redis-jti 逻辑不作为当前撤销能力。

本轮仅文档/源码发现，未登录、发放/撤销 Token、读取私人凭据或调用业务 API。研究 commit 不证明当前部署，本地 Node/文件检查不构成在线验收；既有 runtime profile/fixture 边界保留，delegated profile 不因此可执行。后续须有 owner 批准的部署配置/OpenAPI 对应、身份 fixture 和原生资源权限验收。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，依 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据仅经宿主安全注入，原始 token 不打印，不写聊天、SSOT、仓库、fixture 或命令行；业务写入继续服从原 owner 审批/发布/回读门禁。

# 来源基线与已知漂移

本文件记录 Skill 创建时使用的事实快照。所有 SHA、版本、端点和资源名都可能变化；执行任务时必须重新核验。

## 飞书设计文档

- 文档：`Web服务开发部署流程`
- URL：`https://a4x-paas.feishu.cn/docx/ChBedovlYooCdNxIyzacBLuunog`
- 文档 metadata update time：`2026-05-22T00:05:04.404+08:00`
- 创建本 Skill 时已读取完整 block 内容。

文档以 EdgeFeature 资源包发布为例，提出：

- 把新功能做成 `micro_app_platform` 的 qiankun 子应用；
- 主应用负责飞书登录和用户 JWT；
- 子应用通过主应用注入的 API 客户端请求后端；
- 后端执行 JWT 校验和 SpiceDB 权限校验；
- 上传前由后端签发限定 Role/路径/TTL 的 AWS STS 凭证；
- 上传成功后由后端调用下游发布接口；
- 无权限时进入飞书审批申请。

文档中的 `base/view:edge-feature/publish#update`、Controller 示例和 `props.utils.api` 是当时方案，不是永久协议。实施前要对照实时 metadata、SDK 注解和已部署主应用 props。

## micro_app_platform 代码快照

创建 Skill 时核验：

| 基线 | SHA | 状态/用途 |
| --- | --- | --- |
| `origin/master` | `ae6b9be5646b649901f6886486792be6487b848b` | 2026-07-31 远端主分支 |
| `origin/staging` | `2bdaad2923977a536a9029a1a68fe2d3a3e1c658` | 2026-07-30 AuthZ Facade 与 staging 配置基线 |
| 本地 `feat/growthbook-web-auth-staging` | `89818b634b79cd3d0b4a560dfcfa1ed3cd9001ae` | 独立 Web 授权码桥；当时未在 master/staging 树中 |

关键来源文件：

- `frontend/packages/main/src/micro/index.ts`：qiankun 注册和注入 `utils.api/getAuthToken/getUserInfo`。
- `frontend/packages/main/src/utils/api.ts`：Bearer 注入与权限 API。
- `frontend/packages/main/src/utils/tokenStorage.ts`：当前主应用 localStorage 行为。
- `frontend/packages/sub-app1/src/main.tsx`、`vite.config.ts`：生命周期和 UMD 构建参考。
- `packages/sdk/auth_java`、`auth_python`、`auth_nextjs`、`auth_mcp_node`：多语言 SDK。
- `server/.../security/JwtAuthenticationFilter.java`：平台用户 JWT、内部 secret 和服务 JWT 的分流。
- `server/.../security/InternalServiceJwtVerifier.java`：注册调用方、audience、TTL、jti 和双层 scope。
- `server/.../controller/authz/InternalAuthzController.java`：Facade 入口。
- `docs/api/internal-authz.openapi.json`：Facade 请求/响应契约。
- `docs/architecture/adrs/0002-centralize-authorization-behind-internal-facade.html`：2026-07-14 Accepted ADR。

## auth_sdk 能力快照

当时仓库声明版本：

- Java：`io.a4x:auth-java-sdk:1.1.0`
- Python：`a4x-auth:2.0.0`
- Next.js：`@micro-app-platform/auth-nextjs:1.0.1`
- MCP Node：`@micro-app-platform/auth-mcp:1.0.0`

共同能力：Bearer token 提取、平台用户信息验证、用户上下文、权限 wrapper/decorator、缓存、错误响应。微应用后端应通过对应 `packages/sdk` 同时接入认证与权限鉴权，具体安装、配置和 API 以该 SDK 在固定 ref/SHA 下的 README 为准。Java/Python/Next.js 当前 README 还描述直接 SpiceDB 客户端；MCP SDK 包含 MCP token 验证和 permission wrapper，但创建本 Skill 时缺少顶层 README。

## 已知漂移与风险

1. Java `JwtConfig` 默认 auth endpoint 是 `/auth/me`，Python 和 Next.js 参考实现拼接 `/api/auth/me`。部署前必须确认 base URL 是否已含 `/api`。
2. 主应用代码会把用户 token 放 localStorage，并有 token preview 日志；这是观察到的兼容实现，不是推荐安全基线。子应用应优先消费 `utils.api`，不复制该行为。
3. 主应用路由资源使用斜杠形式 `base/view:<app>/<route>`；Python decorator 的 `app_name` 参考实现曾拼成 `<app>_<view>`。必须做跨语言契约测试。
4. 主应用注入的 API adapter 会为相对 URL拼接子应用 `entryUrl`，并复用带 Bearer interceptor 的客户端。登记 entry origin 必须视为高信任边界。
5. 平台仓库同时存在自有 JWT filter 和 SDK 自动 filter。重复注册会造成双重认证、路径 allowlist 冲突或上下文覆盖。
6. Java/Python/Next.js README 当前说明 SDK direct SpiceDB，而 Accepted ADR 选择新外部服务使用内部 AuthZ Facade。必须由 SDK/平台 Owner 确认目标环境支持模式；Skill 不允许业务代码绕过 SDK 自行选择直连或 Facade。
7. 独立 Web 授权码桥在创建 Skill 时只存在于功能分支，不能在未确认 merge、deployment、redirect registration 和 E2E 前宣称可用。
8. 平台内部 `X-Internal-Secret` 是受限兼容通道，不应替代 `/internal/authz/**` 的服务 JWT。
9. Java README 示例版本与 manifest 版本、Spring Boot 声明与依赖存在漂移；Python README 的安装来源/分支示例也存在漂移。README 决定使用方法，manifest 和发布仓库决定实际版本，二者都要核验。

## 使用规则

- 重新读取飞书文档时记录 title、update time 和媒体内容是否完整。
- 重新读取仓库时同时核验 master、staging、当前工作分支和目标环境 deployed SHA。
- 如果源代码、OpenAPI、Schema 和运行时返回不一致，以目标环境运行时契约为阻塞信号，先由平台 Owner 澄清，不自行选择一个版本。

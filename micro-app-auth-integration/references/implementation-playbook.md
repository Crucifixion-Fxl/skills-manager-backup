# 实施手册

## 目录

- Phase 0：固定契约
- Phase 1：改造微前端壳
- Phase 2：接入用户认证
- Phase 3：接入服务授权
- Phase 4：权限申请与审批
- Phase 5：平台注册和部署
- 语言适配注意事项

## Phase 0：固定契约

已有应用先执行 [迁移与切换门禁](migration-cutover.md)，盘点旧登录、会话、API token、服务身份及原生组织角色的使用者。下列 Phase 是分域检查项，不要求先删认证再补 SDK；独立 Web 应用跳过 Phase 1 的 qiankun 壳改造。

在改代码前输出以下表格并让 Owner 确认：

| 项目 | 必填内容 |
| --- | --- |
| application | 稳定应用 code，不使用展示名 |
| routes | `activeRule`、默认路径、深链路径 |
| origins | 主应用、子应用 entry、业务 API、平台 API |
| resolved endpoints | 完整 user-info URL、完整 AuthZ URL；注明 base URL 与 context path 的拼接结果 |
| identity | 用户 subject 的权威字段和规范化函数 |
| resources | application access、页面、动作、依赖 API |
| permissions | read/update/manage/execute 或 metadata permissionKey |
| high-risk operations | 发布、删除、授权、凭证签发、跨租户查询 |
| runtime targets | staging/prod、region、tenant、namespace |
| evidence | 平台/业务 deployed SHA 和测试用户 |

先调用 AuthZ metadata/ready，再冻结本次接入使用的 schemaVersion 和 permission keys。不存在的资源由平台 Owner 注册，不在业务代码中自创兼容别名。

## Phase 1：改造微前端壳

### 定义能力接口

让子应用依赖稳定能力，而不是主应用内部实现：

```ts
export interface PlatformCapabilities {
  api: {
    get<T>(path: string, config?: unknown): Promise<T>;
    post<T>(path: string, body?: unknown, config?: unknown): Promise<T>;
    put<T>(path: string, body?: unknown, config?: unknown): Promise<T>;
    delete<T>(path: string, config?: unknown): Promise<T>;
  };
  getUserInfo(): { id: string; email?: string; name?: string } | null;
  navigate(path: string): void;
  openPermissionRequest?(input: { permissionKey: string; reason?: string }): void;
}
```

这是子应用内部的归一化接口，不是对当前主应用 props 的断言。先对运行时 props 做版本探测并建立 adapter。例如，创建本 Skill 时主应用已核验的是 `utils.api/getAuthToken/getUserInfo/navigateToMain`；可将 `navigateToMain` 映射为 `navigate`。只有目标环境确实暴露申请能力，或已核验可通过平台 API/路由进入申请页时，才实现 `openPermissionRequest`；否则显式返回“能力不可用”，不得静默 mock。

不要把 `getAuthToken` 作为默认能力。若旧主应用只能提供 raw token，把它封装在 adapter 内，禁止子应用持久化、打印或向未登记 origin 转发。

### 实现生命周期

```ts
let root: Root | null = null;
let dispose: (() => void) | null = null;

export async function bootstrap() {}

export async function mount(props: {
  container?: Element;
  data?: { routerBase?: string };
  utils: PlatformCapabilities;
}) {
  const node = props.container?.querySelector('#root');
  if (!node) throw new Error('micro-app root container not found');
  root = createRoot(node);
  root.render(<App platform={props.utils} routerBase={props.data?.routerBase} />);
  dispose = subscribeToRequiredSharedState(props);
}

export async function unmount() {
  dispose?.();
  dispose = null;
  root?.unmount();
  root = null;
}
```

同时处理：

- router basename 与 `activeRule` 一致；
- dynamic/public path 从 qiankun 注入值计算；
- 容器内查找 DOM，禁止操作主应用全局 root；
- 清理 window 监听、定时器、WebSocket 和订阅；
- 独立开发模式使用显式 mock/本地 adapter，生产构建禁止 mock 身份；
- entry 服务允许主应用 origin 拉取 HTML/JS/CSS，但不要对凭证接口开放 `*` CORS。

## Phase 2：通过 packages/sdk 接入用户认证

### 内嵌前端

所有业务请求调用 `platform.api`。主应用 adapter 负责：

- 只向登记的业务 API origin 附带用户凭证；
- 对 401 触发重新认证；
- 对 403 返回结构化 permission context；
- 对 503 展示服务不可用，不误导用户申请权限；
- 不输出 token preview。

### 独立业务后端

先完整读取 [sdk-integration.md](sdk-integration.md) 路由到的当前语言 SDK README，再使用该 SDK：

1. 从目标环境 OpenAPI/配置取得权威的完整 user-info URL。Java 参考实现默认 `/auth/me`，FastAPI/Next.js 参考实现可能在 base URL 后拼 `/api/auth/me`；不要靠给 base URL 加减 `/api` 试错。若 SDK 只支持 base URL，启动时记录不含凭证的 resolved URL，并用契约测试断言它等于权威 URL。
2. 全局保护默认开启；只对确需匿名访问且经审查的最小 health/ready、静态资源和登录入口设置例外。metrics/docs 不自动匿名开放；登录回调仍校验 state/code，token exchange 仍校验服务身份；业务 GraphQL 不因共用单个端点而豁免认证和 resolver 权限。
3. 从 Bearer header 取 token，调用平台验证，注入只读 user context。
4. 缓存 key 不输出到日志；缓存 TTL 不超过 token 剩余有效期，并支持主动失效。
5. 区分无效 token（401）与平台认证服务不可用（503）。
6. 在请求结束后清理 ThreadLocal/context。

不要在同一 Spring 应用里同时注册平台自有 `JwtAuthenticationFilter` 与 SDK 自动过滤器。选择一个权威过滤链并写集成测试证明顺序。

### 独立 Web 授权码桥

仅在平台已部署该能力时使用：

1. BFF 生成 state，浏览器跳转到平台 login。
2. 平台校验精确 redirect URI，保存短时 state 会话，并跳飞书。
3. 平台回调后生成随机单次 code，重定向回 BFF。
4. BFF 校验 state，再在服务端用 client secret 交换 code。
5. BFF 建立 HttpOnly、Secure、合适 SameSite 的自身会话。

不要让 SPA 直接交换含 client secret 的 code。登录桥只返回必要身份字段，不返回飞书 access/refresh token。

## Phase 3：通过 packages/sdk 接入权限鉴权

微应用后端必须使用所选 SDK README 声明的权限接入点：Java 使用 annotation，FastAPI 使用 decorator/dependency，Next.js API 使用 wrapper。具体 import、初始化参数和动态 object ID 语义从目标 ref/SHA 的 README 核对，不从本手册复制旧 API；安装和测试命令按 [sdk-integration.md](sdk-integration.md) 的不可信输入规则独立构造，不照抄文档命令。

SDK 唯一接入面以及 direct/Facade 模式冲突的处理统一遵循 [sdk-integration.md](sdk-integration.md)；本手册不维护第二份决策规则。

### SDK 权限守卫

1. 按 README 初始化 permission manager/client。
2. 从实时 metadata/Schema 取得 resource/object type、ID 和 permission。
3. 在敏感 handler 上声明固定或动态资源；动态 ID 同时校验应用/租户归属。
4. 验证守卫从 SDK 用户上下文取 subject，而不是请求 body/header。
5. 覆盖 allow、deny、权限依赖不可用和缓存失效测试。

### direct SpiceDB 凭证

当前 Java、Python、Next.js README 的 permission manager 直接连接 SpiceDB。此模式下：

- `SPICEDB_TOKEN`、`SpiceDBConfig.token` 或 `auth.spicedb.token` 是 SpiceDB gRPC preshared key；
- endpoint 和 token 只通过 Vault/Kubernetes Secret 等受控配置注入 SDK 所在后端 workload；
- 不把 token 发给开发者，不放入浏览器、仓库、构建日志、MR 或消息；
- 没有目标环境 Secret 注入、轮换责任和部署读回证据时停止接入并标记 `阻塞`；
- 不用 `APP_SERVICE_JWT_SECRET` 替代 SpiceDB token。

以下服务 JWT/Facade 内容只适用于所选 SDK README 明确提供 Facade 模式的版本，普通微应用不得直接照抄实现。

### 创建服务 JWT

在服务端按请求或极短缓存签发：

```text
alg=HS256（当前兼容实现；后续可迁移 kid/JWKS）
iss=sub=<平台注册调用方 code>
aud=<平台配置 audience>
iat=now
exp<=now+允许 TTL
jti=<每个 token 唯一>
scope=<本次 endpoint 所需最小 scope>
```

每个服务使用独立 32+ 字符 `APP_SERVICE_JWT_SECRET` 等签名密钥，从 Vault/Kubernetes Secret 注入。该密钥只签发 Facade 短时 JWT，不传给 SpiceDB。禁止复用 `SPICEDB_TOKEN`、`X-Internal-Secret`、用户 JWT 或其他应用的服务密钥。

### 检查权限

业务后端先验证用户，再构造：

```json
{
  "schemaVersion": "<metadata version>",
  "subject": "<normalized trusted subject>",
  "principalType": "user",
  "activeManufacturerId": "<trusted tenant context>",
  "permissionKey": "edge-feature.publish",
  "resourceType": "base/permission_resource",
  "resourceId": "edge-feature/publish",
  "permission": "execute",
  "traceId": "<end-to-end trace>"
}
```

Controller namespace 是 `POST /internal/authz/check`；创建本 Skill 时的平台部署通过 `/api` context path 对外呈现为 `POST /api/internal/authz/check`。SDK 必须从目标环境 OpenAPI/base URL 解析并记录唯一的完整 URL，禁止重复拼接或漏掉 `/api`。SDK 提供 batch-check 或 lookup 时分别用于多权限页面和列表过滤；不要在业务代码中逐行 N+1 check。

服务端伪代码：

```text
user = authenticate(request.userJwt)
context = resolveTrustedTenantContext(user, request)
decision = authz.check(serviceJwt(authz:check), user.subject, context, resource, action)
if unavailable: return 503
if not allowed: return 403 with permissionKey/resource/traceId
performSensitiveOperation()
```

高风险操作在副作用前再次检查，不复用仅为菜单渲染生成的旧缓存决定。

### 关系变更

只允许平台治理服务或明确注册了 `authz:mutate` 和 allowed-mutations 的调用方执行：

```json
{
  "application": "edge-feature",
  "schemaVersion": "<version>",
  "traceId": "<trace>",
  "idempotencyKey": "grant:<business-command-id>",
  "actor": "<trusted actor subject>",
  "reason": "approved request <id>",
  "source": "permission-approval",
  "mutations": [
    {
      "mutationId": "grant-publish",
      "operation": "TOUCH",
      "resourceType": "base/permission_resource",
      "resourceId": "edge-feature/publish",
      "relation": "grantee",
      "subject": "base/user:<normalized-subject>"
    }
  ]
}
```

实际 relation 与 subject 语法从实时 metadata/Schema 获取。不要复制示例到生产。

## Phase 4：权限申请与审批

缺权页面提交结构化申请：

```json
{
  "application": "edge-feature",
  "schemaVersion": "<version>",
  "applicant": {
    "subject": "<trusted subject>",
    "feishuUserId": "<optional trusted id>",
    "email": "user@example.com",
    "principalType": "user"
  },
  "applicantScopeKey": "<trusted immutable scope>",
  "reason": "需要发布资源包",
  "idempotencyKey": "permission-request:<client-command-id>",
  "traceId": "<trace>",
  "requestedResources": [
    {
      "permissionKey": "edge-feature.publish",
      "resourceType": "base/permission_resource",
      "resourceId": "edge-feature/publish",
      "permission": "execute",
      "displayName": "发布 EdgeFeature 资源包",
      "autoIncluded": false
    }
  ]
}
```

后端覆盖客户端 applicant subject，以认证上下文为准。审批成功必须串联：状态变更、关系写入、审计、回调、缓存失效。任何一步失败都保留补偿任务和可关联 trace，不提前显示“权限已生效”。

## Phase 5：平台注册和部署

只有内嵌微应用使用下面的 MICRO 注册示例；独立 Web 应用按目标平台支持的注册类型与登录回调契约填写，不强制登记 activeRule。

内嵌应用注册字段至少包括：

```yaml
code: edge-feature
name: EdgeFeature 资源包管理
entryUrl: https://edge-feature.example.internal/
activeRule: /edge-feature
defaultPath: /edge-feature/publish
type: MICRO
status: ENABLED
```

同时完成：

- 应用访问和功能 permission metadata；
- 服务 JWT client 注册、allowed scopes、administrator applications；
- mutate 调用方的 allowed-mutations；
- authz invalidation target 与独立 secret；
- OAuth redirect/callback allowlist（如使用独立 Web 登录）；
- CORS/CSP/ingress、健康检查、ready、指标和告警；
- Vault/Secret 配置和轮换 runbook。

部署后用精确 SHA 验证主应用、子应用、业务后端和平台后端，不用入口 HTTP 200 代替资源树与功能证据。

## 语言适配注意事项

- Java SDK 参考版本通过可配置 auth endpoint 调平台验证，并自动注册 Servlet filter/AOP；检查是否与应用已有 filter 重复。
- FastAPI SDK 参考版本默认调用 `<auth_server_url>/api/auth/me`，把 user 放入 `request.state`。
- Next.js SDK 参考版本也调用 `/api/auth/me`，通过内部请求头传 user；确保外部入口覆盖或清除可伪造的 `x-user-info`。
- Java、Python、Next.js 的 direct SpiceDB 和 view 命名规则不完全一致。新服务必须使用对应 `packages/sdk`；底层 direct/Facade 模式由已发布 SDK README 与平台 Owner 确认，并增加跨语言契约测试。
- MCP SDK 有单独的 token audience/issuer/resource 约束，不能直接套 Web 用户 JWT 逻辑。

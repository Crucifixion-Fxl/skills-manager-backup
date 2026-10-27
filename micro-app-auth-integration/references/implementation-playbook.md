# 三步实施手册

## 1. 声明与登记

已有应用先执行 [迁移与切换门禁](migration-cutover.md)。固定平台/业务 SHA、环境/区域/namespace、已部署 revision、完整 user-info/AuthZ URL、SDK/profile/能力和 subject scheme。无 AuthZ 的组合记录不适用理由；有敏感 API 必须建立 `动作 → API → 副作用 → 资源 → permission` 清单。

按 [平台 admission](../../micro-app/references/sdk-admission.md) 读取支持清单和只读 planner。声明只保存 purpose/bindingRef；本地输出不能代替 application/caller 绑定、资源登记或 Secret 注入。metadata/ready 用于 schema 和端点探测；具体 permission/resource ID 由权威权限清单与注册契约确认，动态归属由业务验证。不存在的能力/资源先记录缺口，不生成兼容别名或假定注册 API。

独立 Web 必须登记 exact redirect/callback；内嵌应用按目标平台真实注册契约核对 code、entry、activeRule、defaultPath 和状态。独立 Web 不强制 qiankun/MICRO 注册字段。普通 caller 的 scope 最小化；治理模块另外准入 actor 管理权限与 mutation 白名单。

内嵌静态试点可以先登记为禁用，确认实际部署入口可加载后再启用并刷新读回。仅复用宿主会话不需要新增 caller secret；这不等于独立后端 caller 已登记或凭据已注入。应用管理员、申请人和审批人分别记录，不能把维护管理员当无权限申请人或由申请人给自己审批。

## 2. 安装并接入 SDK

按 [SDK 规则](sdk-integration.md) 独立构造安装命令，核验实际分发包和依赖闭包。下面是业务实施检查项，不是另一套 SDK API 或支持矩阵。

### 认证与权限

- 从目标环境 OpenAPI/配置取得权威完整 user-info/AuthZ URL。SDK 若拼 base URL/context path，测试其最终 resolved URL 完全相同，不靠增删 `/api` 试错。
- 只通过 SDK 获取可信认证上下文；清除/覆盖外部伪造 header。默认保护业务，最小化匿名 health/静态/login 入口；GraphQL 不能因共用 endpoint 豁免 resolver 权限。请求结束清理 ThreadLocal/context。
- canonicalEmail 与授权 subject 分开，显式 versioned adapter 选择 identity scheme，见 [架构契约](architecture.md)。平台已有 OIDC/code bridge 显式复用，不重新造完整登录。
- 新 strict Facade 不新增正向身份/allow 缓存，不透明 retry/redirect；上层剩余 deadline 逐跳传入，不能每跳刷新完整预算。旧缓存和 transport 的保留/迁移显式记录。
- Next platform-online 除 Facade 引用外，声明独立 `next-context-signing` 并向 middleware/API-route runtime 注入同一 `AUTH_NEXTJS_CONTEXT_SECRET`；运行配置显式 `enableCache=false` 才能验收严格在线，旧 middleware 默认 300 秒缓存保持兼容。planner 通过不证明这些 runtime 条件已满足。
- 敏感 handler 在副作用之前使用该 SDK 的 require/guard；动态资源同时查应用/租户归属。batch/lookup 错误不能变为空权限或静默丢行。caller401/403 属集成503，不能使已登录用户登出。
- 业务只调用被支持的申请入口。治理独立导入和准入，不暴露底层关系写入给普通应用。OUTCOME_UNKNOWN 冻结冲突域，已知终结/fencing 后受控恢复。

### 独立可信 Web

使用已部署且 profile 支持的流程：BFF 建 state → 平台 exact redirect → 短时单次 code → 服务端凭据交换 → HttpOnly/Secure/合适 SameSite 会话。具体路由/DTO、code 原子消费、在线复验与登出按固定实现核验。浏览器不持 client secret 或飞书 access/refresh token；会话/context、OIDC、Web bridge、caller signing 和失效回调凭据各自隔离。

Go Web bridge 使用已登记 ClientID 及其 ClientSecret 的 `web-bridge-client` 引用，不把业务 session key 或 caller signing key 当 client 凭据；业务 session key 由消费方自管并另行隔离。

### 内嵌微前端


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
  getUserInfo?(): { id: string; email?: string; name?: string } | null;
  navigate(path: string): void;
  openPermissionRequest?(input: { permissionKey: string; reason?: string }): void;
}
```

这是子应用内部的归一化接口，不是对当前主应用 props 的断言。先对运行时 props 做版本探测并建立 adapter，可将已核验的 `navigateToMain` 映射为 `navigate`。优先通过 `utils.api` 访问目标环境已确认的完整 user-info URL；先核对 Axios 等返回 envelope 与相对路径拼接规则，不能把示例路径直接接在子应用 entry 后。仅消费通过会话状态校验的最小字段，不记录或渲染原始 profile、响应或错误。

`getUserInfo` 是可选能力，调用前审查目标实现。已核验平台提交 `0b36aebed4689f6791c836e634f1a02f8b48edde` 的 `frontend/packages/main/src/micro/index.ts` 会在该函数中输出原始用户；该实现不得作为接入默认路径，改用宿主 API。此项是固定版本事实，不代表未来版本均存在该行为。只有目标环境确实暴露申请能力，或已核验可通过平台 API/路由进入申请页时，才实现 `openPermissionRequest`；否则显式返回“能力不可用”，不得静默 mock。

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

### 分开验证宿主路由与应用访问

按目标部署 Schema 和路由解析器确定视图 ID 与关联方向，不自行转换分隔符。上述已核验版本的根路由 `/code` 检查 `base/view:code` 的 `read`，关联为 `base/view:code#app@base/app:code`；`view.read` 继承 `app.read`，而 `app.access` 还可由 accessor 获得，两者不能互证。该版本遇到视图拒绝会尝试确认或建立 view→app 关联并复查；不得另授个人 view.read 或修改守卫来掩盖登记问题。

宿主的 Re-check Permission 清除前端权限缓存并重新检查，不是权限申请或审批。首次拒绝后复检恢复，只能记录观察到的先后结果；没有请求时序或关系读回，不断言唯一原因是缺关联、缓存或一致性延迟。没有实际支持的关系登记入口时记录缺口，不从下拉框缺选项推导可手工调用任意写接口。

历史网页 `check-permission` 可能在依赖异常时返回 HTTP200/false，故 false 显示为 NOT_ALLOWED_OR_UNAVAILABLE；异常、超时或畸形响应保持未验证，不能把它当可信 DENY/503 证据。应用和宿主均可见、刷新、离开后卸载、重入及深链整页刷新分别验证；隐藏容器中的 ALLOW 不算用户可用。


## 3. 部署并验收

按业务仓既有 CI/GitOps 路径部署，核对主应用、子应用、后端、平台及配置的 exact SHA/digest。Secret 仅核对受控引用与注入状态，不读取值。每 SDK/profile 分别保留 fixture/真实 HTTP、旧 API、包构建、干净安装和运行测试证据。

获授权测试身份与 caller 在目标部署真实执行 allow/deny/503，按需要覆盖申请、关系写入、审计、撤权和跨应用/租户拒绝。先固定旧缓存清单和可测撤权时限；best effort 失效通知失败由现有短 TTL 兜底，不假称可靠投递或延长 allow。最终回执使用 [验证清单](validation-checklist.md)，没有真实入口或权限就保留具体阻塞，不将本地通过写成 staging 已验收。

浏览器桥接夹具的管理员 ALLOW 不覆盖非管理员 DENY→申请→独立审批→ALLOW→撤权→DENY，也不证明实际 SDK 包可安装或已被业务使用。按身份、部署版本、包 digest 和实际副作用计数留证，缺少测试申请人、专用 caller/SecretRef 或执行器时分别列为未验收；不据此修改 catalog 的 candidate 状态。

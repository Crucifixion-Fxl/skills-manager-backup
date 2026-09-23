# 验证清单

## 目录

- 静态证据
- 认证测试
- 授权测试
- 审批与一致性测试
- 微前端测试
- 部署和运行时证据
- 完成判定

## 静态证据

- [ ] 业务仓库 HEAD、平台仓库 HEAD、SDK 版本和目标环境已记录。
- [ ] 已固定所选 `packages/sdk` 子目录、README ref/SHA 和 manifest 版本，并完整按该 README 接入。
- [ ] 后端没有自行解析 JWT、直接调用用户信息接口或绕过 SDK 编写权限客户端。
- [ ] SDK 认证中间件与 SDK 权限 annotation/decorator/dependency/wrapper 都已注册；不是只接入 `UserInfo`。
- [ ] 当前 OpenAPI、metadata、Schema 与代码使用的 resource/permission 一致。
- [ ] 认证与 AuthZ 的完整 resolved URL 已记录，并分别通过一次契约探测；没有重复或遗漏 `/api` context path。
- [ ] 当前主应用 runtime props 已记录；子应用 adapter 对缺失/未知能力安全失败，不把理想接口误当成现有接口。
- [ ] 子应用没有自己的飞书 OAuth、服务 secret、token localStorage 或 token 日志。
- [ ] 用户 subject 从服务端认证上下文派生。
- [ ] 每个敏感 API 有服务端 permission guard。
- [ ] 普通业务代码没有取得 SDK 底层 SpiceDB client 或直接关系写入；direct/Facade 模式与目标环境治理结论一致。
- [ ] direct 模式的 `SPICEDB_TOKEN`/`SpiceDBConfig.token` 已确认为 SpiceDB gRPC preshared key，并由受控 Secret 链路直接注入后端 workload；开发者没有查看或复制明文。
- [ ] Facade 模式的 `APP_SERVICE_JWT_SECRET` 只用于签发短时服务 JWT；没有传给 SpiceDB，也没有与 `SPICEDB_TOKEN` 或其他 caller/环境复用。
- [ ] 所选模式的 Secret 来源、目标 workload、轮换责任和部署读回均有证据；任一缺失时状态为 `阻塞`。
- [ ] application code 与 caller service code 已从权威注册表读回并分别记录；维护流程缺失时标记平台流程阻塞，不把 Owner 当作未定义流程的兜底。
- [ ] Facade 模式的服务 JWT client 使用独立 secret 和最小 allowed scopes。
- [ ] Facade mutate 调用方有 operation/resource/relation/subject 白名单。
- [ ] redirect URI 精确匹配，callback URL 完整解析后校验 scheme/host/port/path boundary。
- [ ] health/docs allowlist 足够小；业务 API 默认保护。
- [ ] 已有应用按 [迁移与切换门禁](migration-cutover.md) 完成替代链路验证后才移除旧认证；未通过禁用 SDK、跳过测试或放行业务 GraphQL 绕过门禁。
- [ ] 独立 Web 应用保留经审查的授权码回调和自身安全会话，不套用内嵌应用的“删除全部登录接口”；原生 API token/服务身份有独立迁移结论。
- [ ] 日志、错误响应和 trace 不包含 token、secret、code 或临时凭证。

## 认证测试

| 用例 | 期望 |
| --- | --- |
| 无 Authorization | 401，业务 handler 未执行 |
| token 格式错误 | 401，无用户上下文 |
| token 过期/撤销 | 401，缓存不延长有效期 |
| `/auth/me` 超时/5xx | 503 或明确依赖不可用，不返回普通 401/403 |
| SDK 缓存命中 | subject 与未缓存结果一致，TTL 有界 |
| SDK 权限守卫未声明 | 静态检查或默认保护阻止敏感 handler 裸奔 |
| 伪造 userId/x-user-info | 被忽略或覆盖 |
| 请求结束 | ThreadLocal/request context 已清理 |
| 登录 state 重放 | 拒绝 |
| 授权码二次兑换 | `invalid_grant` |
| 非 allowlist redirect | 400 |

## 授权测试

| 用例 | 期望 |
| --- | --- |
| 登录用户无权限 | 403 或 allowed=false/DENY |
| 登录用户有权限 | 允许，traceId 可关联 |
| 错 resourceId | 拒绝，不自动兼容别名 |
| 其他应用同名页面 | 不发生权限串用 |
| 其他租户/厂家动态 ID | 拒绝 IDOR |
| Facade 超时/SpiceDB 故障 | 503/AUTHZ_UNAVAILABLE，fail closed |
| lookup 故障 | 明确故障，不返回空权限集合 |
| batch-check 部分非法 | 对应单项有 reason，其他项行为符合契约 |

以下用例只适用于已确认由所选 SDK 支持的 Facade 模式：

| 用例 | 期望 |
| --- | --- |
| 错 service JWT 签名 | 401 |
| `iss != sub` 或未注册 client | 401 |
| audience 错 | 401 |
| 缺 iat/exp/jti 或 TTL 超限 | 401 |
| token 有 scope、client 白名单无 scope | 401 |
| client 白名单有 scope、token 无 scope | 401 |
| mutate 越过 allowed-mutations | 403/拒绝且不写关系 |

## 审批与一致性测试

- [ ] 同一个 idempotency key 与相同请求返回同一结果。
- [ ] 同一个 idempotency key 与不同请求返回 409。
- [ ] 重复飞书回调不重复授权。
- [ ] 审批拒绝和取消不写授权关系。
- [ ] 权限关系写入失败时不标记最终生效。
- [ ] 审计写入失败时有幂等补偿或整体失败。
- [ ] 外部回调失败时进入可恢复重试，不回滚已确认事实而制造双状态。
- [ ] 缓存失效失败时保留 subject 级恢复任务。
- [ ] revoke 后再次 check 返回 deny，旧缓存不继续 allow。
- [ ] UI 权限和自动包含的 API 依赖都出现在申请详情。

## 微前端测试

- [ ] 首次从主应用进入能 mount。
- [ ] 浏览器刷新 activeRule 深链能恢复。
- [ ] 切换其他微应用后 unmount 干净，无重复监听/请求。
- [ ] 再次进入能重新 mount。
- [ ] router basename 与 activeRule 一致。
- [ ] JS/CSS/public path 在生产 entry origin 正确。
- [ ] 主应用 capability adapter 只向 allowlisted origin 附带用户凭证。
- [ ] 401 触发登录，403 展示申请，503 展示不可用；三者不混淆。
- [ ] 独立开发模式无生产 mock 身份。
- [ ] 主应用不可用或 props 缺失时安全失败。

## 部署和运行时证据

按顺序收集：

1. Source：commit SHA、diff、生成的 OpenAPI/Schema、配置模板。
2. Merge：MR/PR、审批、最终 merge SHA。
3. Pipeline：业务前端、业务后端、平台配置和契约测试结果。
4. Deployment：每个环境/区域的镜像 digest 或 deployed SHA、配置版本、资源树。
5. Runtime：health 与 ready、AuthZ metadata、允许/拒绝请求、审批写入、缓存失效。
6. User acceptance：真实测试用户完成一次 401、一次 403/申请、一次允许操作。

健康检查 200、旧应用 Healthy 或源分支 pipeline 通过都不能单独证明新版本已部署。

## 完成判定

只有以下条件同时满足才标记完成：

- 目标 SHA 已合并；
- 目标 SHA/镜像已部署到声明环境；
- 认证 401、授权 403、依赖 503 语义已验证；
- 至少一个 allow 和一个 deny 用例在真实环境读回；
- 审批到关系写入和缓存失效有闭环证据；
- 无未解释的高风险 exception、fail-open 或共享 secret。

其余情况使用 `实现完成/待部署`、`已部署/待 E2E`、`阻塞` 或 `待用户确认`，不要统称“完成”。

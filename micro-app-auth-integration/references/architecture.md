# 身份、授权与治理契约

## 身份链路

内嵌应用消费主应用实际提供的受控 api/getUserInfo 能力，经 versioned props adapter 归一化，不持久化或打印 raw token。独立可信 Web 使用目标环境已部署的 code bridge/OIDC 和安全会话；MCP/后台任务使用其 profile 支持的专用身份，不借管理员浏览器 token。每个请求跳转均记录完整 resolved URL 和凭据用途。

AuthN 输出与 AuthZ subject 分开：平台在线认证以可信 `enterpriseEmail` 优先、再 `email` 的 canonicalEmail 为资料输出；trim/lowercase、非法/合成 fallback 和账户状态以所选版本契约及共同测试核验，不能从客户端 header 推导。仅既有 legacy Go AuthN 的 owner-approved 策略由平台 HTTP 结果决定账户可用性，`status` 仅为资料；不得扩展到新 Go Facade Guard 或其他 SDK。新 Go Guard 独立要求 ENABLED，DISABLED 拒绝，缺失/未知状态为依赖错误且不发 Facade。canonicalEmail 不是稳定授权 subject。

在应用可信 adapter 中显式选择身份 scheme：

| 来源 | 授权映射边界 |
| --- | --- |
| 平台规范邮箱 | 显式版本化 email-markers adapter；不能把任意字符串当已认证身份 |
| OIDC | 精确 issuer + sub，按契约散列编码；不继承同邮箱或 Feishu 授权 |
| 平台 Web bridge | 桥返回的 Feishu-ID subject，单独 scheme，不猜邮箱映射 |
| MCP user/chat 或服务身份 | 按该 profile 的专用契约；不能冒充人类管理权限 |

本地 `subject.scheme` 类型信息不代表 wire DTO 新字段；扩展请求必须先升级平台/OpenAPI。迁移先清点旧 subject/关系，生成映射、检查冲突与所有者，独立窗口验证和回滚；禁止先试 OIDC 再试 email、自动合并同邮箱关系或暗中回退旧权限。服务 JWT caller/审计 actor 也不自动证明该人有管理权限。

MCP 的 `mcp-online-user` catalog profile 与 mint 请求体的 `auth_profile=mcp-online-v1` 是不同层的标识。只在目标部署确已支持时显式选择：单步 mint body 声明该 auth_profile；两步 prepare 将它签入临时凭据，confirm 继承且不能变更，只接受 user、拒绝 chat。每次工具调用在线 introspect，绑定 issuer/application/contract/scheme/status/expiry，从同一 fresh 目录资料取得状态与邮箱，不把旧邮箱缓存当权限主体。新 online token 不能用于 legacy config 取 SpiceDB 凭据，也不能降级为 PAT、旧 MCP/chat、普通 session 或 Feishu fallback。省略 auth_profile 保留旧 mint 行为，旧 token 不能冒充 online；旧入口不原地改语义。源码或 SDK candidate 不证明这些路由已部署，具体 DTO/完整 URL 和支持状态仍须核验。

## 授权资源与发现

用 `页面/动作 → API → 下游副作用 → resource → permission` 建立权限地图。UI 和菜单只作 UX，敏感 handler 在副作用之前通过 SDK 检查。动态资源的应用/租户归属必须由业务可信数据验证，不能仅清洗字符串。

metadata 提供 schemaVersion、resourceTypes 和静态接口能力，不提供完整 permission/资源 ID 注册表、caller 有效 scope、业务归属或 SDK 支持。应用权限清单、平台权威登记和 Schema/OpenAPI 共同确定具体 resource/permission；再用目标 caller 真实探针验证。不能在 `base/view`、`base/app`、`base/permission_resource` 或斜杠/下划线之间自动猜别名。`ready` 成功也不代表业务资源授权或所有能力就绪。

SDK 支持/profile/版本只读 [平台 admission 正本](../../micro-app/references/sdk-admission.md)。Facade 的服务 JWT 校验包含注册 caller、issuer/sub、audience、时间、jti 和 token/client 双层 scope；actual TTL/audience/允许 scope 按固定平台契约和目标配置。jti 存在不等于一次性消费。

## strict Facade 语义

| 条件 | 新 SDK 对业务的结果 |
| --- | --- |
| 缺失/无效用户凭据 | 401；不发权限申请 |
| 确定 ALLOW | check 为确定允许，require 继续 |
| 确定 DENY | check 为拒绝，require 403 |
| caller 的 401/403、scope/签名/注册错误 | 集成依赖错误 503；不能触发用户登出 |
| HTTP 200 内 AUTHZ_UNAVAILABLE/AUDIT_UNAVAILABLE | 依赖错误 503，不能变成 false/空列表 |
| trace/schema/响应结构错位、未知 reason | PROTOCOL_ERROR，fail closed |

按共同 fixture 校验整响应与每项资源/permission/顺序/trace 关联。新 batch 保留每项完整 Decision，包括 unavailable；require-batch 任一 unavailable 优先 503，其次 deny 403，不因一个失败丢掉其他已返回行。具体语言返回类型可 idiomatic，但语义一致。

lookupPage 仅返回一页和 cursor；空页有非空 cursor 仍可继续。页大小、扫描次数按契约边界；跨页不是快照，`authzRevision=unknown` 不能保证全集一致，业务使用资源前仍需授权。旧 wrapper 的聚合/分页行为保留原接口，迁移显式选择。

新路径使用有界单跳时间和响应体上限，接受调用方剩余 deadline，实际预算为 min(config timeout, remaining)。剩余预算耗尽不发网络；串行调用不能每跳重置上层总预算。无自动 redirect、代理、retry 或新增跨请求身份/allow 正向缓存，TLS/集群 HTTP 例外只按已验证的具体 profile 准入。兼容 OIDC/旧 transport 的原有边界单列，不宣称已获得新路径的全链总 deadline。

## 凭据与治理

用户 JWT、caller signing secret、短时 Service JWT、OIDC client、Web bridge 注册 client 凭据、会话/context、失效回调和云临时凭据分别管理；direct 兼容使用 SpiceDB preshared key。每环境、caller、purpose 隔离注入引用，不向开发者、argv、日志、仓库、浏览器泄露值。Facade 减少业务持有 SpiceDB key，workload 仍需安全持有其 caller 凭据；未实现的轮换双钥/reload 能力不能假定存在。下游云凭据在后端授权之后签发，限制 Role、资源路径、动作与 TTL，不替代用户会话。

普通应用只用被声明支持的申请能力。申请者由可信认证上下文覆盖，保留 application、资源列表、原因、trace 和稳定键；UI 权限及自动包含的 API 依赖必须一并读回。治理独立准入并校验 actor 管理权限、资源归属、operation/resource/relation/subject 白名单、审计与稳定键。申请成功、审批通过、关系写入、审计和最终业务允许分别读回。

通用 mutation 的 idempotencyKey 不等于全局持久 key/payload 账本，也不保证恰好一次。申请/审批接口按其各自实际幂等规则验收，不将某接口的 409 保证推广到全部变更。禁止因网络错误透明重放写操作。

结果未知进入 OUTCOME_UNKNOWN：治理执行器记录稳定键与 payload 摘要，冻结应用/subject/资源/relation 及受影响父层的后续冲突命令。只有证明旧请求已终结、隔离旧执行器并完成受控恢复，或有可验证 fencing，才解锁。单次 readback 不能排除迟到 grant；TOUCH 的状态幂等不能证明迟到 grant 不会覆盖 revoke。

## 缓存迁移与撤权

新路径不增加跨请求身份/allow 正向缓存。逐旧消费者登记缓存层、key、TTL 和失效接收器；新 SDK 上线不会自动删除旧应用五分钟缓存。现有失效通知按 application + subject、eventId 幂等和本地主体 generation 处理，revision 只追踪不排序；best effort 投递可丢失，由短 TTL 收敛。通知失败不得延长旧 allow 生命周期，也不能伪称平台已有可靠持久投递。

Next platform-online 以 `next-context-signing` 独立引用提供 `AUTH_NEXTJS_CONTEXT_SECRET`，由 middleware/API-route runtime 共同验证签名 context；它不与 Facade key 共享。严格在线必须显式 `enableCache=false`，legacy middleware 默认 300 秒身份缓存继续保留，不能把新 Facade 的无缓存扩展成 middleware 默认无缓存。Web bridge client 凭据与消费方自管的业务 session key 亦分别验收。

关键审批/治理事件的持久任务和受控重试由治理执行器显式建设；它与平台现有 best effort 失效通知不同。验收重复/乱序、通知丢失、重启、在途旧查询和 revoke 后最终 deny。撤权/身份停用时限应由具体环境实测固定，不把设计建议写成现有 SLA。代码回滚不自动回滚权限关系或 Secret 版本。

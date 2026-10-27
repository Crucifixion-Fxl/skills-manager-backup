# 三步验收清单

## 审计脚本资源边界

可信 shell 入口只调用同目录普通、非 symlink 的 stdlib Python helper；支持 macOS/Linux、Python 3.9+、git 与 rg。应用仓和平台仓共享一次预算，包含 Git facts、增量文件名枚举、内容读取/匹配及输出，不按 section 重置。默认 `--max-files 10000`、`--max-file-bytes 2097152`、`--max-total-bytes 67108864`、`--timeout-seconds 30`；可在审查输入规模后显式调整，硬上限依次为 100000、16 MiB、1 GiB、60 秒。

file/total bytes 按去重后的路径 corpus/stat 计量，敏感文件仅列名和 stat，不读取内容，但其大小仍计入同一预算。普通内容遵守 gitignore/rgignore，每文件仅读一次，并受实际累计读取字节限制（最多额外读 1 byte 检出超限后失败）；保留 rg smartcase、UTF-16 BOM 解码及 NUL/binary 的文件名候选匹配，均不输出内容。Git status 的增量记录数也受同一 max-files 上限约束。文件名和结果各另有 8 MiB 内存上限。

Git 探针禁用 optional locks、hooks、fsmonitor、untracked cache；status 前仅枚举最多 128 个 filter 配置键名（不读取值），清空 clean/process 并禁用 required，避免执行仓库 filter。`dirty_paths` 仅统计 superproject，忽略 submodule 状态，避免进入子仓执行其独立配置；它不是完整子模块审计。上述 Git 阶段同样计入全局 deadline。

全局耗时由扫描进程外的 supervisor 控制，超时终止整个 Git/rg/扫描进程组，最多另给 0.5 秒回收；无法确认回收时明确 `CLEANUP_UNKNOWN` 并非零退出。文件数/字节数/耗时超限均返回 4、标记 `Inventory is incomplete`，不输出 JSON 成功 envelope；输出候选数截断仍返回 3。`--max-results` 只限每节输出，不能替代扫描预算，非零或 UNKNOWN 都不得当作 clean。

## 声明与登记

- [ ] 平台/业务仓 origin、ref/SHA、目标环境、OpenAPI/fixture/Schema 与完整 resolved URL 已固定。
- [ ] 声明只含 purpose/bindingRef，无真实凭据；AuthN-only 有理由，所有敏感操作仍需 AuthZ。
- [ ] catalog entry 的 SDK/profile/必要能力匹配；candidate 只进入允许的 staging，未知/unsupported 必要能力阻塞。
- [ ] 本地 planner 的 REGISTRATION_PENDING 与 pending gates 原样保留，不当作平台注册/注入完成。
- [ ] application、caller 和绑定从目标环境权威来源读回；最小 scope、资源清单、mutation 白名单按需要确认。
- [ ] Facade/OIDC/Web bridge/会话/context/失效回调/legacy direct 的凭据目的隔离；受控注入引用、workload、轮换责任与读回明确，不查看值。
- [ ] Next platform-online 的 `next-context-signing` 与 Facade 引用独立，`AUTH_NEXTJS_CONTEXT_SECRET` 在 middleware/API-route runtime 配对生效；实际 `enableCache=false`，不把保留的 legacy 默认 300 秒缓存当严格在线。
- [ ] Go Web bridge 的 `web-bridge-client` 对应已注册 ClientID/ClientSecret；消费方自管业务 session key 独立，未误用临时 HMAC purpose 或 caller key。
- [ ] metadata 仅作 schema/静态能力发现，资源登记、caller 有效 scope、动态归属和 SDK 支持各有证据。
- [ ] 没有可执行登记、注入、测试身份或跨应用试点入口时，明确缺口和责任方，未借用其他身份。

## SDK 与协议

逐 SDK/框架/profile 留证，不能以一语言通过覆盖其余语言：

| 范围 | 必须覆盖 |
| --- | --- |
| 分发与安装 | exact source/version/digest、真实包构建、干净安装、实际导出/加载、最小依赖闭包 |
| legacy 回归 | 旧 API 错误/缓存/分页不原地改变，显式 adapter 才迁移 |
| AuthN | 缺失/错误/过期/撤销凭据、依赖故障、canonicalEmail 优先级与非法 fallback、实际账户状态策略 |
| 身份映射 | issuer/sub 编码共同向量、scheme 冲突、旧 email/OIDC 不自动继承、伪造 header/subject 被拒绝 |
| Facade | ALLOW/DENY、HTTP200 unavailable、caller401/403→503、scope/audience/签名/TTL、schema/trace/unknown reason |
| batch | 完整逐项 Decision、关联/顺序错位整体协议错误、require-batch unavailable 优先于 deny |
| lookup | 空页+cursor、边界/扫描上限、依赖故障不为空集合、unknown revision 不承诺快照 |
| transport | 剩余 deadline 更短、预算耗尽不发请求、慢 header/body、响应体上限、TLS、redirect/proxy/retry 禁用 |
| 业务 | 每个敏感 handler 在副作用前守卫、跨应用/租户 IDOR 拒绝、上下文清理、最小公开路径 |

共同 fixture 不能只复制字段：测试必须实际消费平台 `docs/api/fixtures/unified-facade-v1.json`，再补真实 HTTP 交换和分发安装测试。既有 legacy Go AuthN 的 status 策略按其 exact 版本单列，不扩展到新 Go Facade Guard；新 Guard 仅 ENABLED 可继续，DISABLED/缺失/未知不得触发 Facade。旧 OIDC/transport 没有新路径的总 deadline 证明时不得扩展声明。

## 审批、治理与缓存（按所需能力）

- [ ] 普通应用仅申请，不直接授予；申请者由可信认证上下文覆盖。
- [ ] 申请/审批各自重复键、不同 payload、回调重复/乱序按实际接口契约验证。
- [ ] 审批拒绝/取消不写关系；关系或审计失败不宣称最终生效。
- [ ] 写后响应丢失、部分写入、超时迟到 grant/revoke 交错：OUTCOME_UNKNOWN 冻结冲突域，终结/fencing/恢复后才解锁；一次 readback 不解锁。
- [ ] 审批最终状态、关系、审计、投递和业务 check 分开记录；未实现全局持久幂等账本时明确不支持恰好一次。
- [ ] 旧缓存层/TTL/失效 receiver 清单完整；application+subject、eventId、generation 验证，revision 不比较。
- [ ] best effort 通知丢失、重复/乱序、receiver 重启、在途旧查询均测试；TTL 兜底且失败不延长 allow。
- [ ] 目标环境实测 revoke→deny 和身份停用时限；设计建议不充当 SLA。

## 前端与登录（按拓扑）

- [ ] 内嵌应用首次 mount/刷新/深链/切换/unmount/重入均通过；router、public path、DOM 容器和 activeRule 一致。
- [ ] 登记状态、实际入口文件和持久化启用状态分别读回；入口 HTTP200 须确认不是主应用 fallback。用户可见页面通过，隐藏容器中的 ALLOW 不算装载完成。
- [ ] 使用实际 props 的 versioned adapter；能力缺失安全失败，生产不使用 dev/mock 身份。
- [ ] `getUserInfo` 等宿主函数经审查后才调用，不输出原始用户；优先宿主 API 和已确认的完整 URL，返回 envelope、用户状态与最小身份字段校验明确。
- [ ] 宿主 route read 与应用 access 分开取证，视图→应用关联按部署 Schema/解析器确认，不额外授个人权限或修改守卫来通过试点。
- [ ] 复检只算清缓存重查，不算申请/审批；初次失败原因未获唯一证据时不推断。历史网页 false 含故障合并态时不计真实 DENY。
- [ ] 只向已登记可信 origin 转发凭据；CORS/CSP/cookie/SameSite/ingress 有证据。
- [ ] 独立可信 Web 保留必要回调与安全会话；exact redirect、state、防重放、code 原子消费与服务端 secret 生效。
- [ ] 用户401触发认证，确定deny403展示无权/申请，依赖503显示不可用；caller401/403不引发用户登出。

## 分层回执和完成边界

逐 SDK/环境/profile 记录：source SHA → candidate 包/version/digest → 干净安装 → merge/CI → 部署 SHA/image digest/config revision → 获授权身份的 runtime 回执 → 业务验收。supported 的结构化证据必须与环境/profile、SDK 版本、源 SHA、包 digest、contract 和必要能力匹配；空列表、[null] 或其他环境证据不能通过。

真实环境至少读回一次 allow、一次 deny 和依赖503，并验证本次所需的申请/关系写入/审计/撤权闭环。health200、ready/metadata200、fixture/隔离平台测试、CI绿灯或旧应用Healthy均不能替代目标部署和业务验收。按缺口使用 `实现完成/待部署`、`已部署/待 E2E`、`阻塞`，不得统称完成。

浏览器桥接夹具与实际 SDK 消费者分别记录。当前管理员 ALLOW 不能覆盖非管理员申请人、独立审批人、专用后端 caller/SecretRef、真实包执行器和业务副作用计数；这些输入缺失时不填 runtimeEvidence、不晋级 candidate。仅创建内嵌应用无需新增 secret，不代表后端凭据链路已完成。

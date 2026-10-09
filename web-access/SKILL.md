---
name: web-access
description: 统一处理网站和平台登录、授权、会话恢复及人工网页验证；覆盖本机和远程开发、Casdoor/飞书/Micro App、平台 Token、MFA 与 Cloudflare challenge。需要首次调研业务功能或开发适配器时使用 platform-onboarding。
---

# 统一平台访问

## Description

供平台专用 Skill、官方 CLI/API、OpenCLI 和普通网站任务共用。已知平台先从 [平台索引](../platform-onboarding/references/tools.json) 找 owner 的接入 reference；未知网站也能建立浏览器会话和人工接管，身份/Token/API 契约由 [platform-onboarding](../platform-onboarding/SKILL.md) 按需调研。

## Workflow

1. 确认目标站点/环境、预期账号或服务身份、目标资源与任务授权；已有上下文直接沿用。
2. 复用并验证现有凭据或官方 CLI 登录。需要网页登录时使用原任务浏览器；本机优先使用浏览器协议或任务内浏览器控制，避免抢占系统鼠标；浏览器必须跑在远端时按用户授权和可用接管工具选择方式，不默认安装 VNC/noVNC。新会话询问 once/save，已有选择沿用。
3. 按平台契约选择平台 Token 或 Session。飞书/Casdoor/Micro App 登录成功不等于目标平台认证成功；申请 Token 使用平台真实支持的 scope、期限与撤销流程，值直接进入受控本机存储。
4. 需要用户登录、扫码、MFA、验证码、Cloudflare 验证或其他本人操作时，先核验真实目标页面，再将本任务浏览器标签置前（bringToFront）并明确提醒所需动作；不要求用户发送密码或验证码。用户操作期间不点击、填充或导航该页面；通过不读取秘密输入框的有界状态检查检测完成，核验目标身份后自动继续。其他自动化保持后台，不抢占系统鼠标。可由用户完成的步骤标记 WAITING_USER_LOGIN，不直接判为平台 BLOCKED。浏览器接口无法置前时明确告知，并提供真实页面入口，不声称已置前。
5. 完成后核验目标身份、权限与资源范围，再恢复业务。未知站点缺身份契约时只报告目标内容可访问，不宣称 Token/API 或读写权限已核验。
6. 结束时撤销短时租约、停本任务进程与隧道；once 清理 profile 和本次新建的一次性凭据，save 保留私有 profile/受控凭据。借用资源保留；Mac 和开发机分别记录清理结果。

## References

- 原生凭据、运行库与远程调用：[认证使用](references/auth-usage.md)。命令入口为 web-access、web-view、platform-onboard。旧名直接迁移，不保留兼容入口。
- 本机/远端浏览器准备与 SSH/VNC：[浏览器接管](references/browser-handoff.md)。使用 Host alias，无需固定开发机 IP；首次使用由 Agent 按该 reference 引导并执行配置，核验真实连接后再转交平台授权，普通用户无需 inspect。
- 调用者与恢复验证：[接管契约](references/handoff-contract.md)。浏览器 plan 仅为 PLANNED，不等于画面或认证就绪。
- noVNC JPEG/CSP、屏保与实测门禁：[画面验收](references/viewer-acceptance.md)。

## Rules

- 平台 Token 的申请、私有保存、原生消费与撤销：[Token 生命周期](references/token-lifecycle.md)。当前埋点 issuer 已有源码/本地契约，其余按 owner 原生流程。
- 不打印 Cookie、Token、密码、device_code、DevTools websocket 或凭据文件内容。平台 Token 申请尚未有可执行 issuer 契约时，沿用 owner 流程，不猜接口。
- 固定身份/租户/项目；账户变化停止，不换账号重试。READY 来自目标平台探针；权限不足、依赖失败、过期和撤销分别处理。
- 写入结果未知先对账，不盲重放。登录或人工验证不扩大业务授权；审批、支付、SQL、部署与发布沿用专用 Skill 门禁。
- 官方原生能力优先。日常不重扫网站；可复现能力漂移交维护入口。Agent Reach 可提供渠道/工具诊断，实际认证仍遵循本 Skill 和上游工具契约。

受控 stdin 传递长会话凭据时，优先使用非 PTY 管道。必须使用 PTY 时，先关闭 ECHO 与 ICANON，并确认私有输入通道就绪，再写入；canonical 行缓冲会截断较长 JWT，导致阻塞或解析失败。出现截断时终止该消费者、丢弃未完整输入，用正确通道重试，不能打印输入排障。

人工登录检测不得以一次检查仍在登录页就结束：等待期间保留明确的登录检测任务，使用不读取秘密输入的有界轮询；单个窗口到期后先重新核验页面和目标身份，再决定下一步。用户消息到达时立即复查目标页，无需重复索要 done。仅等待用户登录不是认证失败，也不能据此把整体目标判为 BLOCKED；若当前运行无法持续监听，明确告知检测已停止，不能声称正在自动监听。

### 保留开源软件原生 Token 认证

统一 IdP（例如 Authentik）用于人员登录、身份与组映射，不能覆盖或替换开源 SaaS 自身原生 Token/API/CLI 认证。继续使用该软件正式支持的 Token 类型、签发入口、scope、期限、撤销及 CLI OAuth；浏览器 Session 只在其真实支持且确有授权的路径中作为明确标注的回退，不冒充原生 API Token。不能为统一接入拦截 Token 请求改成网页登录、另造通用 Token 或扩大权限。每种模式分别验收。

网页登录与原生 Token/API/CLI 是并存能力：网页用于正常登录、辅助申请已授权的原生 Token、补足实际 API/CLI 不完整的场景和最小结果对照。不能因为存在 CLI 就删除网页登录，也不能以网页 Session 覆盖原生 Token 认证。实际业务优先可用原生能力，缺口按真实部署选择网页，并分别记录验收结果。

OpenCLI 版本的 browser 子命令与 adapter 执行可能采用不同 transport：仅存在 OPENCLI_CDP_ENDPOINT 或 CDPBridge 源码不能证明 browser 子命令可用。检查实际版本的 browser 命令参数与 transport，并分别运行真实通道探针；doctor 的 extension 失败与 SaaS 登录失败分开。临时无头 CDP 探针不操作业务，不输出 DevTools websocket，清理独立进程组和 profile。

### 工具通道与验收效率

实际消费优先可用的官方 CLI OAuth、既有 Token/CLI、原生 Token/HTTP API；浏览器继续用于正常登录、已授权凭据申请、真实接口缺口与网页对照。loopback PKCE 与 Device Flow 分别记录，不猜未部署的授权端点。运行库首次安装使用实际 package.json bin 或 node_modules/.bin，环境变量未设置不证明原有凭据不存在；浏览器通道诊断失败也不证明 SaaS 登录失败。

平台 HTTP 已可用时，内部 CLI 的 npm registry 401、缺缓存或安装超时只记录为工具缺口，不反复安装而阻断原生读取；不修改共享 npm/shell 配置。按本次已约定深度核验双方身份、明确资源、最小网页对照与必要清理，不强制添加撤销后的拒绝请求测试，不用重复模拟测试替代真实消费。

浏览器授权 callback 的 code/state 和临时控制地址应在工具结果显示前遮蔽；回调使用完毕关闭。工具 schema 与实际实现可能不一致，evaluate_script 的 args 可能解析为元素 UID 并在错误中回显，不能向此类工具参数传入凭据。秘密只经已验证的私有进程通道注入，失败结果也须脱敏；不读取完整旧缓存或 JWT claims 排障。

表单工具报告成功不证明框架内部状态已接受输入；检查真实校验与目标加载状态。需要补发键盘事件时保持最终原值，不回显凭据。网页清理只移除本任务明确的会话 key 并检查存在性，不读取值；页面关闭、前端清缓存和服务端撤销分别记录。 Chrome DevTools 的 close_page 可能仅关闭标签而保留具名 BrowserContext；结束前确认 context/profile 销毁能力，不能以最后一个标签消失作为凭据清除证明。接口未提供销毁能力且未确认独占浏览器进程时，记录清理未完成，不关闭共享浏览器或宣称已删除 profile。 如能核对工具服务以 --isolated 启动的父链、准确临时 profile、无其他用户页面及任务归属，可终止该独占 Chrome；确认关联进程退出后删除准确临时 profile，并分别记录进程与文件清理结果。禁止按名称批量杀 Chrome 或删除通用 profile。验收结果、固定用户/主机/日期及一次性环境路径保存在任务报告，Skill 只保留可复用方法。

远端原生执行失败先区分 SSH transport 与 SaaS 认证：SSH banner/连接超时且远端未启动消费者时，只记远端未验收，不能算 Token 拒绝、API 失败或安装失败。以一次有界无业务 SSH 探针确认连接；保持本机已取得结果，恢复连接后仅补远端缺失范围，避免重复本机业务读取和 npm 安装。

浏览器 RPC/脚本请求与私有凭据接收端均须有界：先确认任务消费者已启动且 READY，再触发一次已授权的浏览器私有转交。任务 loopback socket 只监听环回、限定一次接收、设置 accept/read/总生命周期期限及输入大小上限；到期或失败关闭 socket/进程并丢弃内存秘密，不留下长期等待的接收端。控制 RPC 超时只是通道结果，不证明网页登录失败或凭据无效；先区分请求是否送达、消费者是否启动/收到、身份探针是否运行，未知不称转发 PASS，不盲目重发登录、OAuth 交换或相同转交。未启动消费者时保持网页登录状态，先修复接收通道，再补未覆盖范围。

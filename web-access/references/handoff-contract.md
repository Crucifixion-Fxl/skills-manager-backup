# 通用人工网页接管契约

`web-access` 是日常认证与人工网页接管统一入口。它处理登录、MFA、二维码、验证码、授权确认、安全校验和其他本人网页操作，不限 SaaS。可见本机浏览器直接交给用户；浏览器在人看不见的开发机/托管环境时，用 VNC/noVNC 转发同一画面。Chrome tunnel/inspect 只在研发主动选择时使用。

| 调用方 | 责任 | 接管后验证 |
|---|---|---|
| web-access | 日常目标认证、固定身份/租户、权限及租约 | 目标平台 identity/scope 探针，恢复 READY 后才继续业务 |
| platform-onboarding | 首次/变更扫描、命令与功能覆盖 | 原待验证页面/操作及来源证据，不把登录当全站验收 |
| 普通网站任务 | 目标 URL、预期账号和业务目的 | challenge 消失、目标内容可见；涉及账号时确认账号 |
| 官方 CLI/OAuth | 原生授权及凭据消费 | 官方 CLI/API 的登录状态与目标身份；接管不转交 Cookie |

调用方保留一个接管上下文：目标站点、本人动作、reason（login/verification/consent/manual）、预期账号/资源、选定浏览器、原任务恢复点、验证方式、once/save 选择。回执只记录 PLANNED → 画面实测就绪 → NEEDS_USER → 本人完成信号 → 目标验证结果 → 清理；脚本 plan 返回 PLANNED，不声称其他状态已经发生。

1. 本机可见时使用原任务浏览器；远端优先本任务拥有的同一 profile/tab。已有人工验证页不能被新开浏览器、切 IP、自动刷新或自动重试替换。
2. 新会话询问一次性/保存；已有选择沿用。默认 VNC，无需再次问技术模式。保存要有站点、账号和期限；保存 profile 不保存常驻 listener、隧道或租约。
3. 先由 Agent/Companion 准备画面和连接，并通过 [实际渲染门禁](viewer-acceptance.md)。普通用户只获得实际链接、网站、本人动作、完成回复和保留方式。将技术安装步骤交给用户电脑上的 AI 时，单独提供完整 setup prompt，注明未安装 plugin 时使用候选 Skill/私有 helper 的真实路径。
4. 等本人操作时暂停当前页面自动化。其他不依赖登录的工作可以继续。用户说完成后先做只读验证；仍未登录或 challenge 未消失则给出实际状态与恢复动作。没有站点身份契约时只报告“目标页可访问”，不猜 Token、角色或返回 web-access READY。
5. 操作完成不扩大业务授权：支付、提交、授权 consent、修改、删除、审批、发布均沿用原任务/平台门禁。接管不自动同意权限，不代输入密码，浏览器 Cookie/JWT 或 Cloudflare clearance 留在原浏览器；按平台真实 issuer 契约申请的 Token 直接进入受控本机存储，不在对话或接管回执中转交。
6. 一次性结束/失败时关闭本任务进程和隧道并删 profile；保存时关闭连接并保留私有 profile。Mac helper 与远端资源分别有 owner 和清理回执，远端关闭不代表 Mac 清理完成。不可达的另一台机器交由其 AI/用户处理，不声称远程清理已执行。

## 旧 Skill 的边界

- `feishu-auth`、官方 lark-cli、平台 OAuth/Device Provider 保留：它们是原生身份与 Token 消费契约，不是画面接管。
- `feishu-remote-login` 保留明确选择“飞书私聊二维码”时的专用能力，通用网页登录优先本 Skill；外部发送文字/二维码还需目标与身份授权，不能因接管自动发送。其二维码脚本不能直接替代 VNC 的一般验证码/MFA。
- `clawplex-remote-auth` 只用于实际存在 ClawPlex Sandbox/Gateway 的占位凭据、challenge 协议和宿主 VNC。普通开发机没有这些端点时不激活，也不猜 Gateway URL。它不是本 Skill 的全环境替代品。
- Deprecated 仅在替代覆盖、调用者迁移及运行验收都成立后标记；暂不删除这些仍有专用能力的 Skill。

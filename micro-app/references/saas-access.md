# Micro App 接入与认证

平台 owner：`micro-app`。认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)。日常认证由 [web-access](../../../agent-harness/web-access/SKILL.md)协调，首次或变化由 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)更新本 owner。

业务命令和现有门禁仍由 [micro-app-auth-integration](../../micro-app-auth-integration/SKILL.md)承担。本平台 owner 保存实例、登录/Token、身份权限和官方能力路由；业务消费者不复制认证事实。已有 CLI/客户端属于原实现，调用前读取本 owner 的认证契约。

只有 source 证据时保持 delegated/runtime pending；未验证部署版本、身份和资源权限不能宣称已接入。申请/撤销凭据或业务写入沿用本次明确授权及原业务门禁，未知结果先对账，不自动重放。凭据仅私有注入，不写仓库、日志、argv或聊天。

应用接入统一走 [三步与 admission 正本](sdk-admission.md)。先选择 exact SDK/profile/能力和目的隔离的凭据引用，再读回登记/注入，最后逐包及部署运行验收；本地规划不执行这些外部步骤。metadata 不是资源/SDK 注册发现，历史源快照不构成目标部署支持。

2026-10-16 有限 staging 观察：[平台 MR !153](https://gitlab.addx.ai/WEB/micro_app_platform/-/merge_requests/153) 合入并部署 `0b36aebed4689f6791c836e634f1a02f8b48edde`；staging-us 的独立浏览器试点已启用，当前应用管理员的可见挂载、会话、ALLOW、刷新及重入通过。它只消费宿主桥接能力，不是 SDK 包消费者；非管理员申请/独立审批/撤权、专用后端 caller/SecretRef 与执行器未验收。此观察不修改历史 discovery 的全局 runtime pending 或 catalog 支持状态；后续执行仍须核对目标部署。

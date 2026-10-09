# Micro App 接入与认证

平台 owner：`micro-app`。认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)。日常认证由 [web-access](../../../agent-harness/web-access/SKILL.md)协调，首次或变化由 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)更新本 owner。

业务命令和现有门禁仍由 [micro-app-auth-integration](../../micro-app-auth-integration/SKILL.md)承担。本平台 owner 保存实例、登录/Token、身份权限和官方能力路由；业务消费者不复制认证事实。已有 CLI/客户端属于原实现，调用前读取本 owner 的认证契约。

只有 source 证据时保持 delegated/runtime pending；未验证部署版本、身份和资源权限不能宣称已接入。申请/撤销凭据或业务写入沿用本次明确授权及原业务门禁，未知结果先对账，不自动重放。凭据仅私有注入，不写仓库、日志、argv或聊天。

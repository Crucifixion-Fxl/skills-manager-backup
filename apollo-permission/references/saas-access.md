# Apollo：SaaS 接入入口

认证与业务能力正本：[SKILL.md](../SKILL.md)。平台 owner：`apollo-permission`；索引 profile：[auth-profile.json](auth-profile.json)。

当前依据：配置平台角色。策略：委派原 Skill；发布门禁不变。已完成源码分档，线上认证、完整功能和角色覆盖仍待实测；不得把该 reference 视为已登录或全站命令可用。

日常使用：先按原认证契约复用官方工具/已有凭据并验证 identity 与目标权限；需要本机浏览器或远程开发时使用 [web-access](../../../agent-harness/web-access/SKILL.md)。首次发现功能缺口或可复现漂移时，使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，把命令、sitemap/覆盖、输入/输出、角色/范围和验证回执维护在本平台 reference 中。现有业务审批/发布门禁继续适用。

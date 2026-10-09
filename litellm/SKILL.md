---
name: litellm
description: LiteLLM C 端平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 LiteLLM C 端 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [litellm-cend-integration](../../development/litellm-cend-integration/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

6. 身份端点先核对部署版本、公开 schema 和实际返回源码，不能从较新文档推测 `/v2/user/info` 已存在。旧版 `/user/info` 省略 `user_id` 的管理员分支可能读取全实例团队和 Key；即使指定本人也可能返回完整 Key 与用户行，响应处理的“移除 hash”注释不能替代字段检查。客户端只投影邮箱、别名、角色并不能缩小服务端查询与下载范围；Token hash、密码字段和任意 metadata/config 都须先审查。传入某个用户 ID 不等于核验当前调用者。没有最小、服务端绑定的安全自身份读取契约时，标记 native 身份与权限未覆盖，不创建 Key、不枚举 Key、不把登录页成功或 JWT 本地解码算 API 身份 PASS；保留原生 Virtual Key/API/CLI 与网页登录并存的方法，优先让 owner 指明已有凭据及安全身份契约。认证缓存、告警和部署自定义 callback 与业务读取副作用分别记录。

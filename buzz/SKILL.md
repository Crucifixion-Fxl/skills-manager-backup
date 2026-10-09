---
name: buzz
description: Buzz平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 Buzz 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [buzz-agent-setup](../../agent-harness/buzz-agent-setup/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

## 原生 CLI 发现

复用现有 Buzz CLI 并核对实际命令/版本，不把浏览器通道失败当作 CLI 缺失。Nostr 私钥是签名身份，NIP-OA auth tag 是独立证明；不能以 LDAP 密码、普通网站 Cookie 或其他 SaaS JWT 替代。仅检查当前进程注入是否存在，不打印值，不遍历私人配置猜密钥。未注入不证明用户没有既有凭据；明确所需 relay、授权的签名身份和可读资源后，沿原生私有注入路径消费，并核验 relay 认可的主体/权限。本地派生 pubkey 不是服务器授权证明，配置文件里名称也不是人类身份核验。

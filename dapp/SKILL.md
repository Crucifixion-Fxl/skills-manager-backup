---
name: dapp
description: dapp平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 dapp 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [sla-metric](../sla-metric/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

## 分实例认证与源码门禁

旧页面的飞书 code exchange 可建立进程内 Session；匿名 cookie 仅为 Session map 索引，不等于独立后端 Bearer JWT。旧后端认证候选与新 MicroAuth/UserContext 候选须按实例、部署版本分别匹配；不能用 Micro-app OAuth 套旧 API，也不能调用 username-only session/generate mint 填补身份桥梁。保留既有宿主注入 DAPP_TOKEN、官方 Token/API 与新实例正常 native OAuth 消费者，但旧实例 native 读取仍须可信服务端本人探针；未证明的 currentcaller URL 不猜造，浏览器身份或本地 JWT 解码不能替代该门禁。

仅审过列表 handler 不代表完整页面可安全读取。导航前追完 mount、自动辅助请求与 lazy side effects，特别授权 workflow check/update/submission；缺口未闭合保持候选未覆盖。Session/Token 私有处理，不输出 map、原始身份响应或认证错误；只记录正常应用 Session logout 和任务临时资源的实际清理，不把页面退出视为后端 Token 撤销。源码固定 commit 是候选证据，部署 match 保持 unknown 至核验。

---
name: buildbuddy
description: BuildBuddy平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 BuildBuddy 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [buildbuddy-deploy](../../delivery/buildbuddy-deploy/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

### 配置与身份读取边界

GetBazelConfig 不能因名称是 Get 就自动作为无凭据变更的读取。固定版本候选源码中，Quickstart 默认 includeCertificate=true：有组织 Key 且证书生成开启时可产生新客户端证书和私钥；页面也可能自动 GetApiKey 读取既有值。只读任务不要打开有凭据的设置/Quickstart 后导出原始响应，不申请证书或读取 Key 值。专用配置探针应按实际部署 JSON/protobuf 契约明确 include_certificate=false，并不填猜测的 groupId/request_context，仅投影公开配置；配置可读或空 group 无凭据不证明个人身份或私有构建访问。

GetUser handler 的读取与完整网页登录初始化分开：网页在无用户时可能回退 CreateUser，OIDC 正常刷新会话也会写认证状态。按本次正常登录授权处理认证初始化，不将它说成整个调用链无写入；不主动调用创建用户、组织或管理 Key 接口。OSS NullAuth 或缺 UserDB 的部署可能没有个人登录能力，部署版本/认证配置必须核验，不能从公开 Quickstart 推断 SSO 已接入。候选源码、真实请求和身份/资源证据分别保存。

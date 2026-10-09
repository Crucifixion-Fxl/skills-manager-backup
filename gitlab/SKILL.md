---
name: gitlab
description: GitLab CI/MR/Issue平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 GitLab CI/MR/Issue 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [gitlab-mr](../gitlab-mr/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

### CLI OAuth 前置核验

优先使用已验证的原生 CLI 凭据；需要新 OAuth 登录时，先检查当前二进制的 `auth login --help` 是否实际支持 Web/Device，不因在线文档已有 `--device` 就把旧版本当成支持。自建 GitLab 的 device flow 还要核对服务端版本及已注册公共 OAuth 应用的 client ID、grant 和权限；client ID 不是访问 Token，不能拿 PAT 代替它。没有已批准的应用时，记录前置条件，不自动创建 OAuth 应用或管理凭据。

远端原生 CLI 直连、远端租约调用本机 CLI 与网页会话是不同模式，分别记录实测结果；租约成功不证明远端已安装 CLI 或独立 OAuth 登录成功。任务临时 CLI/配置/凭据与已有配置隔离；只读调用核验当前用户及明确目标资源，随后按本次授权清理。官方前置条件见 [GitLab CLI authentication](https://docs.gitlab.com/cli/authentication/)，仍以安装版本和目标实例实际行为为准。

### 复用成熟 CLI 认证

已有可用 glab CLI、SSH 或开发环境认证时，优先用现有原生路径核验身份并读取目标资源；不为了统一验收流程反复网页登录或强制重新 OAuth。Device OAuth 用于确实需要新增登录的场景，缺新流程前置条件不阻断已经实测可用的认证方式。远端模式仍需实际远端消费证据，不能只凭本机成功推断。

远端缺少官方 CLI 而已有获准复用的原生凭据时，可在任务独立临时目录安装对应系统/架构的官方固定版本，核对官方 release checksum，再将凭据只通过加密 stdin 和进程环境注入。隔离 CLI 配置目录，不复制用户凭据配置、不执行 auth login 写入；实际远端核验身份和明确资源，结束删除仅本任务 binary/config 并清理私有内存。此模式是远端原生 CLI 的既有凭据消费，不能宣称新的 Device OAuth 登录已通过。

远端缺 CLI 时，在任务授权范围内可使用受限本机 CLI 租约，核验同一目标结果并清理。此模式与直接远端 CLI、原生 HTTP 消费分别报告；长期原有凭据保留，仅清理本任务临时租约及私有副本。

---
name: worktime
description: 研发工时和项目平台唯一登录与能力入口，维护实例、身份权限、Token生命周期和官方工具路由；用于登录恢复、认证检查及路由平台读写任务，业务Skill共享本owner的认证契约。
---

## Description

本 Skill 是 研发工时和项目 的认证与接入 SSOT。读取 [平台接入](references/saas-access.md)及 [认证事实](references/auth-discovery.json)，经 [web-access](../../agent-harness/web-access/SKILL.md)复用登录、Session/Token与人工验证；平台变化由 [platform-onboarding](../../agent-harness/platform-onboarding/SKILL.md)维护。

## Rules

1. 本 owner 唯一维护实例认证资料；消费者引用本 owner，不复制登录流程或另建 auth-profile。认证源码事实、fixture与实际平台验收分别记录，delegated不代表可执行统一provider。
2. 先核对环境、目标实例、部署版本、subject及资源权限。IdP登录不等于平台API授权，能查询不等于能写入。未知事实保持pending，不猜URL、Token或接口。
3. 公开平台优先本认证reference中的官方Skill/CLI/API与部署版本对应资料；内部平台按已记录源码commit和业务契约。OpenCLI仅补已证明的缺口，不复制公共完整API。
4. [worktime-filing](../worktime-filing/SKILL.md)继续维护业务命令、参数和门禁；读写任务先取得本owner的访问能力再执行。所有写入沿用用户授权、前置检查、结果回读和清理要求，结果未知先对账。
5. 凭据私有注入，不输出到argv、日志、仓库或聊天；申请、轮换、撤销必须有对应授权，不因缺凭据扩大身份或权限。

### 登录成功与工时权限分别核验

先用 `/api/user/info` 核验本次目标身份，并将两端 user_id/open_id 的正常化摘要对比，避免只比较姓名或字段存在。再读取 `/api/worktime/check-permission`；`has_permission=false` 即业务权限未启用，即使 HTTP 200、飞书登录成功或 Token 存在，也不能报告业务读取通过。网页权限提示与 API 结果分别对照。权限不足时不通过额外 Token、换账号或提交任务绕过；报告阻塞范围及所需部门/统计配置。

原生 CLI 登录保留随机 loopback state 校验，禁止打印含 Token 的回调 URL。一次性验收需要不落盘的受控消费者，不能直接运行默认会写 `.env` 的登录脚本；永久保存与一次性内存使用应明确区分。新 CLI Token 创建仍核对平台真实期限、scope、用户授权和撤销方式，不自行声称它是只读 Token。

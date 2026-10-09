---
name: n8n
description: 维护AddX n8n的真实实例、部署源码、身份和权限验证及官方API入口；用于查认证方式、申请或撤销个人凭据、定位当前版本API或排查接入失败。所有运行态与业务调用遵守环境和用户授权。
---

## Description

n8n实际接入由[saas-access.md](references/saas-access.md)持有，机器认证事实见[auth-profile.json](references/auth-profile.json)，源码和全API发现见[source-discovery.json](references/source-discovery.json)。日常认证复用`web-access`；首次或部署/认证变化用`platform-onboarding`更新本owner。优先官方Skill/CLI/API，本Skill只补AddX部署事实与私有差异。

## Rules

1. 先核对目标环境、部署SHA/版本、subject和最小资源权限；source-verified不等于runtime-verified。未知部署不得沿用其它平台认证或退回占位域名。
2. 官方 API Key 从已授权的宿主环境变量`N8N_API_KEY`消费；本次正常登录 Session 的有限只读交接按下方专节处理；不读取Secret、vault、session/token缓存值，不在argv、日志、仓库或聊天输出凭据；用户交互创建/撤销不由扫描自动触发。
3. 默认源码研究和无副作用小范围验证。业务写必须有本次明确授权、完整目标/payload和资源scope，审批/发布/生产门禁独立适用；写后回读，结果未知先对账不重放。
4. 刷新仅覆盖受影响认证/部署/API，不复制完整官方公开API。无权限停止并说明owner和恢复条件，不换身份。

## Steps

1. 读取实例和认证reference，判断source-only还是runtime evidence；缺失环境/版本/权限时按source-discovery具体入口研究。
2. 路由部署版本对应官方reference或fork OpenAPI；先核对资源和subject，再执行已授权操作。
3. 记录候选版本、环境、数据范围、执行时间/退出码与未验收能力；凭据内容不入回执。

### 原生 API 与 Session 验证

优先消费已授权注入的官方 API Key。创建前检查当前版本、edition、权限与期限；若实例无法限制为本次授权范围，不创建包含管理权限的 Key，也不把原有只读授权当成管理凭据授权。可在用户已授权的登录与读取范围内，使用本次正常登录签发的 Session 做有限的原生 HTTP 验证，但单独记录公开 API Key 路径未覆盖。

Session 请求若遭 401，先核对实际浏览器请求的绑定契约；某些版本同时要求当前会话的 `browser-id` 与 `User-Agent`。只交接本次会话的必要字段，不读取旧缓存或复制全部浏览器请求头；通过私有 stdin/加密通道在进程内传递。先用目标平台当前用户接口核验预期 subject，再做小范围工作流列表读取；不得读取工作流凭据、激活或执行工作流。网页数量、名称和启停状态与两端结果分别对照。

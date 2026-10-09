---
name: dittofeed
description: 维护AddX Dittofeed AddX fork的真实实例、部署源码、身份和权限验证及官方API入口；用于查认证方式、申请或撤销个人凭据、定位当前版本API或排查接入失败。所有运行态与业务调用遵守环境和用户授权。
---

## Description

Dittofeed AddX fork实际接入由[saas-access.md](references/saas-access.md)持有，机器认证事实见[auth-profile.json](references/auth-profile.json)，源码和全API发现见[source-discovery.json](references/source-discovery.json)。日常认证复用`web-access`；首次或部署/认证变化用`platform-onboarding`更新本owner。优先官方Skill/CLI/API，本Skill只补AddX部署事实与私有差异。

## Rules

当前 AddX 实例已下线，见实例 reference 的生命周期说明。停止该实例运行态访问；只保留历史说明，恢复上线后重新核验。

1. 先核对目标环境、部署SHA/版本、subject和最小资源权限；source-verified不等于runtime-verified。未知部署不得沿用其它平台认证或退回占位域名。
2. 凭据仅从宿主环境变量`DITTOFEED_API_KEY`消费；不读取Secret、vault、session/token缓存值，不在argv、日志、仓库或聊天输出凭据；用户交互创建/撤销不由扫描自动触发。
3. 默认源码研究和无副作用小范围验证。业务写必须有本次明确授权、完整目标/payload和资源scope，审批/发布/生产门禁独立适用；写后回读，结果未知先对账不重放。
4. 刷新仅覆盖受影响认证/部署/API，不复制完整官方公开API。无权限停止并说明owner和恢复条件，不换身份。

## Steps

1. 读取实例和认证reference，判断source-only还是runtime evidence；缺失环境/版本/权限时按source-discovery具体入口研究。
2. 路由部署版本对应官方reference或fork OpenAPI；先核对资源和subject，再执行已授权操作。
3. 记录候选版本、环境、数据范围、执行时间/退出码与未验收能力；凭据内容不入回执。

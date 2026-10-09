---
name: reportportal
description: ReportPortal平台唯一接入owner，维护AddX实例、部署源码、身份权限与官方MCP/API入口；用于查询测试执行证据、排查认证、管理已授权的测试结果和申请或撤销个人凭据。巡检与设备云流程消费本owner，不自行维护登录契约。
---

## Description

平台登录与能力入口的唯一正本是[saas-access.md](references/saas-access.md)，机器事实见[auth-profile.json](references/auth-profile.json)、[auth-discovery.json](references/auth-discovery.json)和[source-discovery.json](references/source-discovery.json)。日常登录/访问调用`web-access`，首次或部署/认证变化调用`platform-onboarding`更新本owner；不复制通用登录、浏览器或隧道教程。优先复用官方MCP/API，业务消费者保留自身测试编排。

## Rules

1. 操作前明确环境、实例、部署版本、subject和project角色。source-verified不代表runtime-verified；生产与staging凭据和数据不混用，不套用其它平台SSO。
2. 凭据只由宿主注入；不读取Secret、vault、.env或session/token缓存，不写入argv、日志、仓库、聊天或回执。申请、轮换和撤销必须有明确授权。
3. 默认按已知launch/item关联ID做有测试上下文的小范围读取；普通开发问题不在ReportPortal寻找或制造测试记录。日志和附件按任务最小范围读取，避免无关个人数据。
4. 查询与写入按官方版本对应的API/MCP路由。创建/导入/改删launch、finish、分析任务、defect修改及项目/用户管理均需本次明确授权、完整目标/payload和权限；生产/审批门禁仍适用。写后回读，结果未知先对账，不重放。
5. MCP工具存在不证明只读或已获权；按实际`tools/list`核对工具、版本及副作用。权限失败停止并记录owner与恢复条件，不换身份，不自动生成测试或新key来验证。

## Steps

1. 读取references，判断source-only或runtime证据，核对实际部署版本与官方兼容范围；缺失事实按source-discovery定向研究。
2. 经`web-access`复用当前登录；凭据与权限确认后优先官方MCP的读取能力，未接入或版本不兼容时路由版本匹配的官方API，不猜endpoint。
3. 保留环境、候选版本、subject/project角色的非敏感证据、查询范围、时间/退出码与写后回读；明确pending能力。认证、部署或权限变化使相应证据失效。

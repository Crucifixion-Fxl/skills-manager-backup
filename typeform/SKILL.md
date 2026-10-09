---
name: typeform
description: Typeform平台唯一接入owner，维护真实入口、身份权限、官方能力或私有源码路由与凭据生命周期；业务调用遵守消费者门禁，未知运行态明确pending。
---

## Description

本平台接入唯一正本为[saas-access.md](references/saas-access.md)，认证与源码事实见[auth-profile.json](references/auth-profile.json)、[auth-discovery.json](references/auth-discovery.json)、[source-discovery.json](references/source-discovery.json)。日常访问调用`web-access`；首次/认证或部署变化调用`platform-onboarding`更新本owner，不复制通用登录、隧道或浏览器教程。

## Rules

1. 先核对实例、版本、subject与目标资源scope；source-verified不代表runtime-verified。未知事实先按reference研究，不猜API、不换身份或套其它平台认证。
2. 凭据仅由宿主注入；不读Secret、vault、.env或token/session缓存，不在argv、URL、日志、仓库、聊天或回执输出凭据。申请/轮换/撤销需明确授权。
3. 默认小范围获权读取；业务写需本次明确授权、完整目标/payload与权限，保留消费者发布/审批/生产门禁。写后回读，结果未知先对账不重放。工具存在不证明只读或获权。
4. 保留[业务消费者门禁](../user-research/survey-research-workflow/references/typeform-execution-contract.md)并按接入reference路由；认证、版本或权限变化使相关证据失效。

## Steps

1. 读取references，区分source-only与runtime事实；补齐缺失scope/版本后才调用。
2. 优先官方Skill/MCP/API或私有service源码入口，复用现有业务执行器，不复制全API。
3. 记录环境、版本、subject/资源的非敏感证据、授权范围、时间/退出码、回读和pending能力。

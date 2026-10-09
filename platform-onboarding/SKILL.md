---
name: platform-onboarding
description: 首次或变更后调研平台的认证与全站功能，比较官方 Skill/CLI/API、OpenCLI 和 Agent Reach 上游工具，为缺口开发适配器，并把已核验契约沉淀到该 SaaS 的 Skill/reference；日常登录与网页验证使用 web-access。
---

# 平台调研与接入

## Description

每个 SaaS 建立一次可维护的接入成果：认证消费契约、能力路由、验证证据和专用 Skill 的使用入口。自建平台还需内部 API/业务契约和缺口适配器。已有可用接入只更新受变化影响的部分。适用于无 CLI/API 或 API 不全的自建/三方平台；有官方 Skill 的能力优先复用。

从 [工具索引](references/tools.json) 找平台 owner，再读取 owner 的 `references/saas-access.md`。用 `python3 scripts/scan.py --root=<AddX仓库>` 生成全插件源码线索和待分档域名；扫描不登录、不读取凭据。域名可能是文档、示例或占位符，必须确认其用途。

## Rules

- 开源平台默认只固化实例地址、部署版本获取方法、认证/Token、身份与权限探针，以及对应版本的官方 Skill/CLI/API reference 入口；不复制完整公开 API 清单。执行前查当前版本文档，写入先核对授权、资源和结果回读，不能等写错后再查。厂商工具已有能力时直接路由；只有部署定制或真实缺口才开发适配器。
- 自建平台记录源码仓库、部署版本与源码 commit 的对应证据、OpenAPI/路由入口和业务门禁。优先使用源码维护的契约；没有规范时生成带 commit 的内部 reference，标注角色、请求、错误、副作用、回读与清理。源码可得不等于当前部署已验证。详见 [来源与版本](references/source-policy.md)。
- 验收默认使用本地模拟服务或隔离测试覆盖读写，线上尽量只读；测试中通过不等于线上通过。实际线上写入仍受本次授权和平台门禁约束。
- 先做能力比较：厂商官方 Skill/CLI/API、AddX 已有契约、OpenCLI 现有 adapter；只补实际缺口。来源与适用能力要可追溯，社区包不能标为厂商官方。
- 使用 OpenCLI 随发行包提供的 `opencli-adapter-author`、`opencli-sitemap-author`；可复现 adapter 漂移使用 `opencli-autofix`。从安装包的 `skills/` 读取原文，不复制另一套易过时教程。官方稳定 API、可见 UI/DOM 优先；私有 PAGE_FETCH/INTERCEPT 需注明收益、漂移风险与写入契约，不能盲目把网络拦截当唯一策略。
- 扫全站菜单、路由、弹窗、角色分支、分页/过滤/导出和写入后状态。源码帮助发现隐藏功能；未获权限的角色功能标记阻塞，不伪造验收。按功能波次测试，扫描本身不执行线上写入。
- 写测试使能力缺口或漂移产生真实行为 RED，实施后读回 GREEN；环境失败不算 RED。写操作要求授权、前置状态与结果回读；生产发布沿用原 Skill 门禁，不因 OpenCLI 封装绕过。
- 日常认证交给 `web-access`，SaaS 的实际登录方式、subject/tenant、权限探针与 Token 消费规则保存在平台 reference/profile。飞书/Casdoor provider 名称不证明 Device Grant 或跨应用 Token 可用。
- 区分 `source-verified`、`fixture-verified`、`runtime-verified`、`pending`、`unsupported`。全站有条目不等于全站能操作。新接入、认证/接口/DOM/官方工具变更或可复现失败触发更新；日常用户无需扫描。

交付字段、角色覆盖和淘汰条件见 [接入契约](references/contract.md)。

## Examples

- “接入埋点平台全部功能”：复用 tracking-lifecycle 的源码固定 REST/业务发布契约，用站点清单补浏览器入口与真实缺口，不把缺失后端接口包装成可用命令。
- “给 CICD 做 CLI”：从 GitLab OAuth 和平台现有 UI/API reference 起步，验证 subject 和列表读取，再覆盖创建、审批、灰度、Apollo 等角色操作；真实部署仍需原门禁。
- “Sentry 有官方 Skill”：核验厂商来源及当前能力覆盖，优先复用官方插件，AddX reference 只保留实例/组织/权限与缺口。

未知网站可以先通过 web-access 建立原任务浏览器会话；持久接入时调研身份、Token/Session 和业务能力。Agent Reach 的渠道与上游工具是可选来源，见 [Agent Reach 集成](references/agent-reach.md)。

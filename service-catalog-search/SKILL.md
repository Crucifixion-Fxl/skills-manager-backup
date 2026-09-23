---
name: service-catalog-search
description: 查公司的服务 & 能力目录（Backstage/RHDH Software Catalog）—— AI 在做技术方案、写代码时自查「我要做 X → 调哪个服务、怎么接、它现在能用吗」，人在命令行里也能查。三类查询：能力反查（谁提供 push / feature flag / 权益校验 …）、服务查询（某个服务的完整条目：层 / owner / API / 依赖 / 运行态）、接入规格拉取（怎么接这个服务）。当用户提到「调哪个服务」「我要做 X 该调谁」「X 服务怎么接」「X 服务谁负责 / 属于哪层 / 现在能用吗」「能力反查」「服务目录」「找依赖」「公司有没有现成的 X 能力」时触发；architect 在 Step 2 识别到方案需要某个平台能力（push / 灰度 / 权益 / 用户画像 / 设备上下行 …）时也应触发本 skill 去查清楚再写方案。
---

# service-catalog-search — 查服务 & 能力目录

## 描述

查公司内部开发者门户（Backstage/RHDH）的服务 & 能力目录——AI 或人在做技术方案、写代码时自查「我要做 X → 调哪个服务、怎么接、它现在能用吗」。三类查询：**能力反查**（谁提供 push / feature flag / 权益校验…）、**服务查询**（某服务的完整条目：层 / owner / API / 依赖 / 运行态）、**接入规格拉取**（怎么接这个服务）。门户离线时自动降级直读各仓的 `catalog-info.yaml`。

公司用 [Backstage](https://backstage.io) 家族门户（Red Hat Developer Hub，部署仓 `infra/backstage`）做**服务 & 能力目录**：每个服务/包仓库根目录一个 `catalog-info.yaml`（用 Backstage 原生 System Model：`Component` / `API` / `System` / `Domain` / `Resource`），GitLab discovery 自动注册进门户。本 skill 让你查这个目录——**不再靠现读现答地拉 GitLab、不再靠某个 skill 里内嵌的静态表**，查的是稳定、结构化、随代码更新的真相。

> 设计 SSOT：`infra/backstage` 的 `docs/architecture/{overview,catalog-schema,domain-model,ai-query-layer}.md`。
> 服务名 / 5 层架构 / 能力（=`API`）名 / Domain 名 的规范清单 SSOT：`engineering/skills` 的 `docs/architecture/{backend-service-architecture,domain-model}.md`。

## 核心概念（来自 ADR-002，全用 Backstage 原生 System Model）

| 你想问的 | 目录里对应的 | 怎么查 |
|---|---|---|
| 「公司有没有 X 能力？谁提供？」（push / feature flag / 权益校验 / 用户画像 / 设备上下行 …）| **`API` 实体** = 「能力」（`Component` `providesApis` / 消费方 `consumesApis`）；按 `metadata.tags` 含能力关键词找；顺 `apiProvidedBy` 关系到提供它的 `Component` | `find-capability` |
| 「X 服务是啥？属于哪层？谁负责？依赖什么？运行态如何？」| **`Component`**（`spec.type` / `metadata.tags` 里的 `layer-*` / `spec.lifecycle` / `spec.owner` / `spec.system` / `dependsOn` / `providesApis`）+ 运行态插件摘要（K8s / ArgoCD / GitLab CI / Sentry …）| `describe-service` |
| 「怎么接 X 服务/能力？」（SDK 坐标 / REST 契约 / 鉴权 / 配置 / 示例 / 韧性建议）| `Component`/`API` 的 `metadata.links` 里 `title: 接入规格` 的链接（指向 TechDocs 或仓内 `docs/integrate.md`）+ 对应 `API` 实体的 `spec.definition`（CI 从代码生成的 OpenAPI/proto）| `get-integration-spec` |
| 「公司有哪些服务？某个域/层/团队下有什么？」| 列 `Component`（按 `metadata.tags` 的 `layer-*` / `spec.type` / `spec.owner` / `spec.system` / `Domain` 筛）| `list-services` / `list-capabilities` |

5 层架构（`layer-*` tag）：`layer-vertical-product`（垂直产品）/ `layer-business-platform`（C 端业务平台）/ `layer-base-platform`（基础平台）/ `layer-shared-infra`（共享基础）/ `layer-company-infra`（公司基础设施）。
状态：`spec.lifecycle` ∈ `production`/`experimental`/`deprecated` + `metadata.tags` 里的 `status-*`（如 `status-extracting` 剥离中、`status-planning` 规划中）。

## 规则

1. **查目录优先**：「公司有哪些服务 / 调哪个 / 怎么接」的答案必须来自目录（`catalog-info.yaml` 注册出来的 Backstage 实体），不凭记忆、不现读现答地拉 GitLab 拼清单。
2. **三类查询入口**：能力反查 → `find-capability`；服务条目 → `describe-service`；接入规格 → `get-integration-spec`；浏览 → `list-services` / `list-capabilities`。所有查询走 `scripts/catalog-query.sh`。
3. **约束必须传递**：`find-capability` 返回的 `no-direct-*` 标签（如 `no-direct-fcm`）是架构约束，写方案 / 生成接入代码时必须遵守（业务方不能绕过这些中间件直连底层）。
4. **降级原则**：门户离线时脚本自动降级直读 GitLab 各仓的 `catalog-info.yaml`（需 `GITLAB_TOKEN` + `GITLAB_HOST`）；降级结果标注「降级模式」，无运行态、无关系解析——不等于出错，只是数据不完整。
5. **自然语言映射**：输入不必精确匹配 API 名，脚本内置关键词映射（见 `references/capability-map.md`）；映射失败时原样当 API 名 / tag 关键词使用。

## 用法 — 三类查询 + 两个列举

所有查询走 `scripts/catalog-query.sh`（它做 curl Backstage Catalog REST API + jq 整理）；门户离线时自动降级直读各仓的 `catalog-info.yaml`（见「降级」）。

### 1. 能力反查 — `catalog-query.sh find-capability <api-name | 关键词 | 自然语言>`
- 输入：能力（`API`）名（如 `push-notification`、`entitlement-check`、`device-uplink-downlink`），或关键词/自然语言（「我要发推送」「判断用户是否在订阅期」「设备属性上报」）—— skill 先按 `references/capability-map.md` 把自然语言映射到 `API` 名/`metadata.tags` 关键词。
- 干的事：`GET /api/catalog/entities/by-name/api/default/<api-name>`（按名）或 `GET /api/catalog/entities/by-query?filter=kind=API,metadata.tags=<keyword>`（按 tag 粗筛，按 `pageInfo.nextCursor` 翻页）→ 读 `API` 实体的 `relations.apiProvidedBy` → 提供它的 `Component` → 读该 Component 的 `relations`（`partOf` → System、`ownedBy` → Group）和 `metadata.tags`（`no-direct-*` 约束）/`metadata.links`（接入规格）。
- 输出（每个提供方一行）：
  ```
  capability(API)=push-notification  →  Component: novu  (System: notification-platform, Domain: notifications, layer-shared-infra, lifecycle: planning)
     约束: no-direct-fcm, no-direct-apns, no-direct-sendgrid    # 业务方不能直连这些（必须走 novu）
     接入规格: https://<rhdh>/docs/default/component/novu/integrate#push-notification
     owner: team-notification（飞书: @xxx）
  ```
  多个提供方（如 `crash-monitoring` 由 `sentry`（云/App）和 `memfault`（设备）分担）：全部列出，按 `API.spec.lifecycle` 排序（production 优先），按 `metadata.tags` 的 `scope-cloud`/`scope-app`/`scope-device` 区分适用范围。

### 2. 服务查询 — `catalog-query.sh describe-service <service-name | 自然语言>`
- 输入：服务名（全局 domain-model 规范名）或自然语言（「订阅服务」「录像回看」「物模型平台」）。
- 输出：该 `Component` 的完整条目——基本信息（`spec.type`、`metadata.tags` 里的 `layer-*`、`spec.lifecycle`、所属 System/Domain、`spec.owner`、仓库链接）、`providesApis`（它暴露的 `API` 实体清单 = 「能力」，各带 `spec.definition` 指向 CI 生成的 spec）、`consumesApis`/`dependsOn`/下游消费者（依赖图摘要）、运行态摘要（ArgoCD sync/health、K8s 副本数、最近 CI 状态、Sentry 24h issue 数——按已配置的插件而定）、`metadata.links`（接入规格等）、TechDocs 入口。

### 3. 接入规格拉取 — `catalog-query.sh get-integration-spec <service-name | api-name>[#<anchor>]`
- 输入：服务名或能力（`API`）名，可选锚点。
- 输出：拼好的接入规格——`metadata.links` 里 `title: 接入规格` 那条指向的文档（TechDocs 渲染的 markdown 或仓内 `docs/integrate.md`，可带 `#anchor`）+ 对应 `API` 实体的 `spec.definition`（`$text` 指向 CI 从代码/契约生成的 OpenAPI/proto/asyncapi，或〔仓没生成器时〕手维护的 OpenAPI）。AI 据此生成接入代码（可调 `microservice-integrate` 的代码生成——`get-integration-spec` 的输出格式 = `microservice-integrate` 代码生成的输入）。
- 门户「Definition」标签页对不同 `spec.type` 的渲染：`openapi` → Swagger UI（结构化）；`asyncapi` → AsyncAPI 渲染器；`graphql` → GraphiQL；**`grpc` → 原始 `.proto` 文本**（Backstage 没内置 gRPC 结构化查看器，对内部 gRPC 服务 `.proto` 本身就是契约）；自定义 type（`dart-package` / `c-header` / `web-routes` / `swift-package` / `typescript-package`）→ 纯文本（指向源文件如 barrel `.dart`、`.h` 头、路由清单）。所以拉到 `.proto`/源文件文本不是出错——那对那类 API 就是「定义」。

### 4. 列举/浏览（辅助）
- `catalog-query.sh list-services [--layer X] [--type Y] [--owner Z] [--system S] [--domain D]` — 按 `metadata.tags` 的 `layer-*` / `spec.type` / `spec.owner` / `spec.system` / Domain 筛 `Component`。
- `catalog-query.sh list-capabilities [--domain D]` — 列所有 `API` 实体及它们的 `apiProvidedBy`。
- 全用原生 Backstage `filter`（标量过滤，原生支持）。

## 配置

`scripts/catalog-query.sh` 读这两个环境变量（或 `~/.config/service-catalog-search/env` / 仓内 `.env`）：
- `RHDH_BASE_URL` — 门户地址。**当前可先用 `https://192.168.20.24:8444`**（PoC 阶段的临时门户；仅在确认使用自签证书时显式设置 `RHDH_INSECURE=1`）。本机开发：`http://localhost:7008`（端口看 `infra/backstage` 仓 `make dev-check`）或 `https://<本机LAN-IP>:8443`（Caddy TLS 入口）。生产：部署后的 RHDH URL。
- `RHDH_TOKEN` — Backstage 后端 service token。**新 Backstage backend 对 `/api/catalog/*` 要求鉴权**（没 token 会 401）。本机：用 guest provider 拿（`curl $RHDH_BASE_URL/api/auth/guest/refresh` 取 `backstageIdentity.token`），脚本会自动这么做。生产：配一个 `static` token（`backend.auth.externalAccess` 里加一个 static token，给本 skill 用）或一个 service account——见 `infra/backstage` 的 `docs/architecture/ai-query-layer.md §3`。

GitLab 降级用：`GITLAB_TOKEN`（read_api scope）+ `GITLAB_HOST`（内部 GitLab 域名，必须显式设置）。

## 降级 — 门户离线时直读 GitLab

门户暂时不可用（升级中、故障）时，脚本**不报错**，而是降级：
- 直接调 `gitlab.addx.ai` 的 API，对目标 group 下的仓读 `catalog-info.yaml`（即门户 discovery 平时扫的那批文件），在本地解析、做能力反查/服务查询。
- 拿不到的部分（运行态摘要、自动发现补的字段、跨实体关系解析）标「门户离线，此项不可用」。
- 返回结果**明确标注「降级模式（直读 GitLab catalog-info.yaml，未经门户处理）」**——让 AI 知道这是兜底数据（可能拉到半成品、没经过 schema 校验、没有跨实体关系）。
- 降级是「读结构化的 `catalog-info.yaml`」，**不是**回到「现读现答地扫整个 GitLab 拼服务清单」。

## 与其它 skill 的关系（ADR-006）

| skill | 关系 |
|---|---|
| `architect` | Step 2 识别到方案需要某个平台能力时，调本 skill 的 `find-capability` 把「公司有没有现成服务、调谁、约束是什么」查清楚，写进技术方案——而不是凭记忆。 |
| `microservice-integrate` | 「我要做 X → 调哪个服务」的答案**来自本 skill**（`find-capability`），不再 `microservice-integrate` 内嵌静态反查表（避免两份漂移）。`microservice-integrate` 的「接入代码生成」能力保留，输入来自本 skill 的 `get-integration-spec`。 |
| `software-development-guide` | 「公司有哪些服务 / X 仓库干嘛的 / X 模块谁负责 / X 能力调谁」改由本 skill 回答（一致、可信、秒级），不再 `software-development-guide` 现读现答地拉 GitLab 拼服务清单。`software-development-guide` 保留「研发流程导航」职责。 |
| `datahub-schema-search` | 形态范本（自然语言 → 结构化查询 → 结构化结果）；二者并列——一个查 DB schema，一个查服务/能力，互不替代。 |

> 不变量：「公司有哪些服务、调哪个、怎么接」这个问题，**只有一个权威答案来源 = 目录**（`catalog-info.yaml` 注册出来的 Backstage 实体）。所有 skill 要么查它（本 skill / `software-development-guide` / `architect`），要么用它的输出（`microservice-integrate`）——不再有第二份会漂移的清单。

## 反过来：你在某个服务/库/应用仓里干活时（写配置让目录收录）

不是查目录、而是**让你这个仓被目录正确收录** —— 用 **`service-catalog-onboarding` skill**（producer 侧）：仓根要放什么（`catalog-info.yaml` 的 C 类字段 + `a4x.io/cicd-app-name` join key + 关联注解、`mkdocs.yml` + `mermaid_hook.py` + `docs/index.md`、CI 里 `.api`/`.proto` → OpenAPI 生成 / stdlib http 手写 OpenAPI → `API.spec.definition.$text`），什么不归你仓的活（System/Domain/Group、ArgoCD App、IAM/IRSA、运行态数据 —— 自动）。门户的 GitLab discovery 扫各仓默认分支根目录的 `catalog-info.yaml` 自动注册，不用「去门户里注册」。新建仓走 `infra/backstage` 的 scaffolder 模板会直接带上这些。

## references / scripts

- `references/catalog-api.md` — Backstage Catalog REST API 速查（endpoints / `filter` 用法 / `relations` 跳转 / 鉴权）
- `references/capability-map.md` — 「自然语言/关键词 → `API` 名 / `metadata.tags`」映射（同步自全局 domain-model 的能力清单）
- 「你的仓要做什么才算正确接入了服务&能力目录」—— 见 **`service-catalog-onboarding` skill**（producer 侧：catalog-info.yaml / mkdocs.yml + mermaid_hook.py / CI 的 API 契约约定 / 关联注解 / join key）
- `scripts/catalog-query.sh` — 查询脚本（curl + jq；门户离线降级直读 GitLab）

## 示例

### ❌ Bad — 凭记忆 / 现读现答
```
AI 做「续费提醒」方案 → 凭记忆「自己接 FCM 发推送」 → 重复造轮子，且违反「业务方不直连 FCM」的架构约束
或：software-development-guide 现去 GitLab 拉一遍拼服务清单 → 慢、可能拉到半成品、和别处不一致
```

### ✅ Good — 查目录
```
AI 做「续费提醒」方案 → 识别到需要「发推送」和「判断用户是否在订阅期」
→ catalog-query.sh find-capability "我要发推送"   → push-notification → Component novu（约束: no-direct-fcm/apns/sendgrid；接入规格: ...）
→ catalog-query.sh find-capability "判断用户是否在订阅期" → entitlement-check → Component subscription（约束: no-direct-stripe/apple-pay/airwallex）
→ 方案里写「依赖 Novu 发推送、依赖 SUB 判权益」+ catalog-query.sh get-integration-spec subscription#entitlement-check → 据此生成接入代码
```

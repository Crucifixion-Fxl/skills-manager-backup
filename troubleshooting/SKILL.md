---
name: troubleshooting
description: AddX Troubleshooting 故障诊断平台 API 操作助手。通过 REST API 进行日志检索、ES 索引查询、数据库查询、工单管理、ID 关联查询、设备事件分析、凭证管理。当用户需要排查设备故障、搜索应用日志、查询 ES 数据、执行数据库诊断查询、管理工单、分析用户错误、查询设备事件，或提及故障诊断 (troubleshooting)、日志搜索 (log search)、设备问题排查、工单处理 (ticket)、ES 查询、数据库查询、cross-reference、device events 时使用此 Skill。
---

# troubleshooting

AddX Troubleshooting 故障诊断平台 API 操作助手。

**所有接口的请求/响应参数定义见 Swagger 文档**（OpenAPI JSON 均为 `/api/openapi.json`）：

| 环境 | Swagger |
|------|---------|
| Prod-US | `https://troubleshooting-us.addx.live/api/docs` |
| Prod-EU | `https://troubleshooting-eu.addx.live/api/docs` |
| Staging-US | `https://troubleshooting-staging-us.addx.live/api/docs` |
| Staging-EU | `https://troubleshooting-staging-eu.addx.live/api/docs` |

本文档仅说明业务规则、调用流程和 Swagger 中看不到的注意事项。

认证自动化流程参见 [references/auth_flow.md](references/auth_flow.md)。

## Description

Troubleshooting 平台是 AddX 内部的故障诊断中枢，服务于技术支持、研发和运维团队。

> 认证边界：本 Skill 使用 Micro App Platform/效能应用 OAuth 换取业务平台 JWT。
> 这不是普通飞书消息或资源访问；不得改用普通 `lark-cli` user profile，也不得把该业务
> token 用于飞书 OpenAPI。

| 环境 | 平台地址 | SSO 登录入口 |
|------|---------|-------------|
| Prod-US | `https://troubleshooting-us.addx.live` | `https://micro-app-platform-us.addx.live/troubleshooting-prod-us` |
| Prod-EU | `https://troubleshooting-eu.addx.live` | `https://micro-app-platform-us.addx.live/troubleshooting-prod-eu` |
| Staging-US | `https://troubleshooting-staging-us.addx.live` | `https://micro-app-platform-us.addx.live/troubleshooting-staging-us` |
| Staging-EU | `https://troubleshooting-staging-eu.addx.live` | `https://micro-app-platform-us.addx.live/troubleshooting-staging-eu` |

四个环境共用同一个 Token，从任意环境获取一次即可通用。

## 辅助：查应用上下文（用 `service-catalog-search`）

排查某个**应用/服务**的问题前（尤其是不熟悉的服务），先 invoke `service-catalog-search` 的 `describe-service <服务名 | 自然语言>` 拿到它的上下文，再来这里查日志/ES/DB——能少走弯路：

- **owner / 层 / 所属 System / 仓库**：知道这是谁的服务、属于哪一层、代码在哪，便于定位和找人。
- **依赖图（`dependsOn` / `consumesApis` / 下游消费者）**：故障可能在上游或下游——先看它依赖谁、谁依赖它，决定 cross-reference 往哪个方向查。
- **运行态注解**：`backstage.io/kubernetes-id`（K8s workload，对应 pod/容器日志）、`argocd/app-name`（最近部署/回滚——很多"突然出问题"是刚发版）、`sentry`（Sentry project，对应错误聚合）——用这些值定位运行时资源，不要猜服务名。日志接口必须先按环境路由：Staging-US/EU 的 K8s 日志统一走 `staging-logs/query`；Prod 的 `iot-service-cloud` 走 `query/es`，其他服务走 `query-index`。`a4x.io/troubleshooting-id` 仅用于适用的 Prod 旧 ES 索引/服务标识，不能替代 staging 的 Pod `app` 标签。
- **接入规格 / API 契约**：报错涉及某接口时，`get-integration-spec` 拿到契约，对照实际请求/响应判断是不是用错了。

门户离线时 `service-catalog-search` 会自动降级直读 GitLab 的 `catalog-info.yaml`（运行态注解可能拿不全，会标注）。查不到对应实体说明该服务还没接入目录：Staging 继续从 `DEV/argocd-apps` 的 Application 追到 workload Pod `app` 标签，不得按服务名猜过滤值；Prod 再按服务选择下文的旧 ES 路由。

## Rules

### 认证（自动化流程）

1. **首次获取**：会话开始时如果 `TROUBLESHOOTING_TOKEN` 为空，运行 `TROUBLESHOOTING_TOKEN=$(node <skill_dir>/references/get_token.mjs us)` 获取 Token
   - 脚本内部会先检查磁盘缓存（`~/.troubleshooting-token-{region}`），缓存有效则秒返回
   - 缓存过期则自动打开飞书授权页面，用户点击授权后自动获取新 Token
   - 详见 [references/get_token.mjs](references/get_token.mjs)
2. **直接使用**：获取 Token 后，后续所有 API 请求直接使用 `$TROUBLESHOOTING_TOKEN`，**不要每次请求都重新运行 get_token.mjs**
3. **401 时重新获取**：当 API 返回 401 时，运行 `TROUBLESHOOTING_TOKEN=$(node <skill_dir>/references/get_token.mjs us)` 重新获取 → 重试一次。重试仍 401 则报告失败，不循环

**认证请求格式**：

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TROUBLESHOOTING_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  "$BASE_URL/api/v1/..."
```

### 脱敏机制

- 除 staging logs-v2 原始日志查询外，业务接口返回的敏感数据会**自动脱敏**，ID 值会被替换为 `trouble_shooting_id:xxx` 格式
- **脱敏 ID 使用规则（默认全员自动识别，例外见下）**：
  - **默认行为**：除 staging logs-v2 查询外，业务接口都自动识别脱敏 ID，传原始 ID 或 `trouble_shooting_id:xxx` 均可。
  - **例外 1（必须脱敏）**：`/es-data/load-data` 的 `id_value` 必须用 `/es-data/status` 返回的 `trouble_shooting_id:xxx`，传原始 ID 会报错 `Cannot find original IDs for temp IDs`。
  - **例外 2（必须声明）**：`/log-search/figure` 使用脱敏 ID 时必须显式设置 `encrypted=true`。
- **脱敏 ID 生命周期**：由后端 Redis 维护，TTL **7 天**，任何接口访问到都会**自动续期**。同一会话短时间内拿到的脱敏 ID 可以放心复用，不必每步重新解析。
- **Staging 日志例外**：`POST /api/v1/staging-logs/query` 返回 OpenSearch 原始 `_source`，既不执行上述 ID 脱敏，也不解析 `trouble_shooting_id:xxx`。默认查最小时间窗和页大小，只展示排障必需字段与片段；输出前遮蔽 `Authorization` / `Cookie` / token / password，以及与本次排障无关的邮箱和用户/设备标识，不要外传原始日志。
- 查询结果可能包含用户数据，**禁止**将原始数据外传

### 环境选择规则

平台共有 4 个环境：**Prod-US**、**Prod-EU**、**Staging-US**、**Staging-EU**。

**环境 → API 基地址映射（确定环境后，所有 API 调用必须使用对应的基地址）**：

| 环境 | API 基地址 |
|------|-----------|
| Prod-US | `https://troubleshooting-us.addx.live` |
| Prod-EU | `https://troubleshooting-eu.addx.live` |
| Staging-US | `https://troubleshooting-staging-us.addx.live` |
| Staging-EU | `https://troubleshooting-staging-eu.addx.live` |

> **关键**：用户指定了哪个环境，就必须使用该环境的基地址。例如用户说"查 staging-us"，则所有请求都发往 `https://troubleshooting-staging-us.addx.live`，绝不能发往 prod 地址。

**确定环境后，第一步设置 `BASE_URL` 变量，后续所有请求统一使用 `$BASE_URL`**：

```bash
# 示例：用户指定 staging-us
BASE_URL="https://troubleshooting-staging-us.addx.live"
# 后续所有请求使用 $BASE_URL，如：
curl -s -X POST "$BASE_URL/api/v1/staging-logs/query" ...
```

#### 日志查询 / 业务数据库查询：必须明确环境

当用户执行**日志查询**（`staging-logs/query`、`query/es`、`query-index`）或**业务数据库查询**（`query/db`）时，如果**未指定环境**，必须先提示用户选择：

> "请确认要查询哪个环境？Prod-US / Prod-EU / Staging-US / Staging-EU"

**不要**默认使用某个环境，也**不要**并行查询多个环境——日志和数据库数据量大，盲目查询浪费资源。

#### Cross-Reference 查询：自动识别环境

当用户提供了 SN、user_id 等标识做 **Cross-Reference 关联查询**但**未指定环境**时，**不要询问用户**，而是主动并行查询**线上两个环境**来确认（Cross-Reference 查询轻量，适合并行）：

1. 并行调用 Prod-US 和 Prod-EU 的 `POST /api/v1/cross-reference/query`
2. 哪个环境返回了有效关联数据（如 device_sn、user_id 非空），即为目标环境
3. 如果两个环境都有数据，告知用户并确认使用哪个
4. 如果两个环境都没有数据，告知用户该 ID 未找到
5. 如果用户明确说是 staging 环境的数据，则查询 Staging-US 和 Staging-EU

#### 组合场景：给 ID + 要查日志/DB + 未指定环境

最常见的请求形式（"这个 SN 帮我看下日志"、"这个 email 查下订阅"）同时触及前两节的规则——表面冲突，按下面顺序处理：

1. **先做 Cross-Reference 自动识别环境**（按上一节并行 prod 两环境的规则），不必先问用户。
2. **识别到的环境直接用于后续日志/DB 查询**，不要再回头问用户"请确认环境"。
3. **两个 prod 都查不到** → 主动问用户"是不是 staging 环境？"，肯定的话并行查 Staging-US/EU；否定的话报告"该 ID 在 prod 未找到"。
4. **两个 prod 都有数据** → 把两边的关键信息列出来让用户选用哪一边。

→ 单一原则：用户给的 ID 本身就携带环境信息，让 cross-reference 替你判定，比打断用户去问更高效。

> ⚠️ **并行查 staging 仅适用于 AI 推测的场景**。如果用户**直接说**了"staging"但没明确 US/EU（例如"查 staging 上这个 SN"），按上一节"日志/DB 查询必须明确环境"的规则**问用户 US 还是 EU**，不要自作主张并行 staging-us/eu。区分点：AI 主动推测 ✅ 并行；用户主动说但没说完 ❌ 必须问。

### API 调用规范

**调用任何 API 前，必须先从 Swagger 获取该接口的请求参数和响应 schema，禁止凭猜测构造请求或解析响应。** 具体做法：

1. 首次调用时，获取并缓存 OpenAPI spec：`SWAGGER=$(curl -s "$BASE_URL/api/openapi.json")`
2. 调用具体接口前，用 node 提取该接口的 request schema：`echo "$SWAGGER" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const spec=JSON.parse(d);console.log(JSON.stringify(spec.paths['/api/v1/xxx'].post.parameters,null,2))})"`
3. 根据 schema 中的 `required` 字段和参数类型构造请求，确保所有必填参数都已提供
4. **解析响应前，必须先从 Swagger 提取该接口的 response schema**，确认字段名后再编写解析逻辑，禁止猜测字段名（如猜 `records` 实际是 `results`）：`echo "$SWAGGER" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const spec=JSON.parse(d);const resp=spec.paths['/api/v1/xxx'].post.responses['200'];console.log(JSON.stringify(resp,null,2))})"`

> 本文档只记录业务流程和 Swagger 中看不到的注意事项，不重复接口参数定义。遇到不确定的参数，查 Swagger 是唯一正确做法。

### 核心功能域

#### 用户/设备全景查询（query/db）

**用户提到查询业务数据时（如：订阅、VIP、订单、用户信息、设备绑定、支付等），一律使用 `POST /api/v1/log-search/query/db`。** 该接口直接查 iot-service 的 MySQL，无需预加载；一次返回某 user_id 或 device_sn 的全景数据（绑定、设备、订阅、推送配置、固件、状态 …）。仅覆盖 `user_device_binding` + `user_info` 两张主表的视图，不是任意业务表查询。

- **`db_queries` 固定写法**：`{"user_device_binding": ["all"], "user_info": ["all"]}`——始终传这个固定值，不需要根据用户意图调整。**Why**：该参数是占位形参，实际返回内容由服务端决定；改这里只会触发参数校验错误，不会减少返回体
- 接口会一次性返回所有业务数据，按数据块组织；**完整字段定义见 [references/user_device_query_field_mapping.md](references/user_device_query_field_mapping.md)**
- **根据用户意图筛选展示**：从返回结果中提取用户关心的部分，不要把所有数据都展示给用户
- 其他必填参数（`start_date`、`end_date`、`es_data` 等）从 Swagger 获取格式，其中 `es_data` 可传 `{}`
- **其他服务的业务数据**（如 statemachine 状态机表、订单系统表等）不在此接口范围内——用 `/api/db_query/execute` 跑预定义诊断查询（见下方「DB 查询」章节），或走 NineData 等数据库平台

**响应数据块速查**（字段 JSON 键名以字段映射文件为准，禁止猜测）：

| 数据块 | 说明 | 常用场景关键词 |
|--------|------|--------------|
| `factoryInfoDOS` | 设备出厂信息（SN、MAC、型号、出厂固件/MCU、出厂时间、品牌） | 设备基本信息、出厂配置 |
| `bindInfoDOS` | 设备绑定记录（绑定时间、用户邮箱、userId） | 绑定关系、谁绑了这台设备 |
| `cameraInfoAndSettingsDOS` | 设备当前配置（固件版本、时区、录像、PIR、夜视等） | 设备设置、当前固件 |
| `vipStatusResults` | 设备维度 VIP 激活状态（`vipActivate`） | 设备 VIP 是否生效 |
| `notificationInfoResults` | 推送通知配置（总开关、检测对象/事件类型） | 推送、通知设置 |
| `activityZoneInfos` | 提醒区域配置（名称、顶点坐标、是否删除） | 活动区域 |
| `otaInfo` | OTA 升级状态与进度（目标固件、进度、状态、是否静默） | 固件升级 |
| `deviceStatuses` | 设备最近一次在线/离线状态及原因、更新时间 | 设备状态、离线原因 |
| `user` | 用户账号信息（邮箱、注册时间、手机系统、注销状态等） | 用户信息、账号 |
| `devices` | 用户当前绑定设备列表（SN、UID、固件、型号、绑定时间） | 当前绑定设备 |
| `vip` | 账号订阅/VIP 记录（套餐类型、有效期、支付类型、退款状态等） | 订阅、VIP、套餐、支付 |
| `binding` | 账号绑定操作历史（绑定方式、时间、设备 IP） | 绑定历史 |
| `bindSnAndUserSnInfo` | 账号历史绑定设备去重列表 | 历史设备 |

#### 日志查询（Prod ES / Staging logs-v2）

**查询日志前，必须先确认以下三个前置条件：**

1. **时间范围**：用户必须指定查询的时间范围。未指定时按环境确认：Staging 先问“是否查询近 15 分钟的日志？”，Prod 先问“是否查询近 24 小时的日志？”；得到确认后再执行
2. **服务**：用户必须指定查哪个服务或 workload 的日志。未指定时向用户确认。**先按环境、再按服务选择接口**：

   | 环境 | 服务 | 查询流程 |
   |------|------|---------|
   | Staging-US / Staging-EU | 所有进入 logs-v2 的 K8s 服务 | 直接 `POST /api/v1/staging-logs/query`，用 `kubernetes.labels.app` 过滤项目 |
   | Prod-US / Prod-EU | `iot-service-cloud`（iot-service 云端入口） | load-data → `POST /api/v1/log-search/query/es` |
   | Prod-US / Prod-EU | 其他服务（statemachine、kiss、auth、ai-saas …） | `query-index`（先 `index-patterns` 拿索引名） |

   - **Staging 硬规则**：不得调用 `es-data/status`、`es-data/load-data`、`query/es`、`query-index` 或 `index-patterns`。Staging 日志已经由 FluentBit 写入 logs-v2，无需导入。
   - **Prod 硬规则 1**：`query-index` **永远查不到 iot-service prod 索引**——iot-service 在 Kibana 的 index-pattern 中被剔除了。要查 iot-service 一律走 `query/es`，不要尝试用 `query-index` 加 iot-service 索引名。
   - **Prod 硬规则 2**：`query/es`、`query/db`、`figure` 这三个接口**仅用于 iot-service 数据**；其他服务用这三个接口会报错或返回空。
   - **默认推断**：用户用业务现象描述（绑定 / OTA / 视频 / 推送 / 直播 / 设备配置 / 用户账号 …）未指明服务时，默认按 **iot-service** 服务族处理，并在响应里告知"按 iot-service 查，若指的是其他服务请告知"。在 Staging 仍须继续解析具体 workload 的 Pod `app` 标签，不能直接把 `iot-service` 当作过滤值。
3. **Prod 旧 ES 冷存储限制**：以下 hot/warm/cold 规则仅适用于 Prod 旧 ES，不得未经验证套用到 Staging logs-v2。Prod ES 索引在 hot/warm 状态仅保留 **3 天**，超过 3 天迁移至 cold，ES 索引总共保留 **1 个月**。查询结果为空时根据时间范围给出不同提示：
   - **3 天 ~ 1 个月前**：提示"数据可能已迁移至 cold 存储，需要先通过 `POST /api/v1/es-index-manage/indices/move-to-warm` 迁移回 warm 后才能查询。注意：迁移操作有权限控制，且可能因 warm 节点空间不足而失败"
   - **超过 1 个月**：提示"ES 索引仅保留 1 个月，该时间段数据已过期，无法查询"

**时区处理**：ES 中存储的时间均为 **UTC 时间**，所有查询必须传 UTC。如果用户输入的时间没有显式带时区（UTC offset、`Z` 后缀、或明确写"UTC"），**先问用户一次时区**再做 UTC 转换，不要默认假设。例如用户说"查今天 14:00 的日志" → 先问"按哪个时区？"，确认 UTC+8 后实际查 `06:00` UTC。**同一会话内已确认过时区后，后续查询沿用，不必再问**；只在用户切换地理/项目语境时重新确认。

**跨平台查询（Prod 旧 ES）**：当 ID 类型与目标平台不匹配时（如用 user_id 查 embedded），旧日志查询流程会通过 Cross-Reference 转换 ID。`staging-logs/query` 不自动转换 ID，也不解析 Cross-Reference 返回的脱敏 ID；使用用户提供的原始 ID 或真实日志关键词。只有脱敏 ID 时，明确说明无法据此搜索 staging 原始日志，不要把它直接放进 `query` 后把空结果当作无日志。

**关键词构造（所有日志查询通用）**：Staging `staging-logs/query` 的自由文本默认搜索 `log` 字段；Prod 的 `query/es`、`query-index` 主要搜索旧 ES 的 `message` 字段。**不确定日志里实际打印的字符串时，必须向用户询问，禁止凭描述猜**——猜空了无法区分"真没日志"还是"关键词错了"。问用户的两种方式（任选其一）：

1. 让用户直接给出关键词（真实的报错字符串、event 名、错误码等）；
2. 让用户提供对应的代码仓库地址（必要时含子模块路径），由 AI 用 GitLab Blob Search API 在该仓库扫实际的 `log.*("...")` 调用拿到关键词——一行命令模板：

   ```bash
   # 例：扫 iot-service-unified 仓库里跟 "bind" 相关的日志调用
   # 项目路径需 URL-encode："/"→"%2F"
   glab api "projects/CLOUD%2Fiot-service-unified/search?scope=blobs&search=bind+log" \
     | jq -r '.[] | "\(.path):\(.startline)  \(.data | gsub("\\n"; " ") | .[0:200])"' \
     | head -30
   ```

   拿到 `path:line snippet` 后挑出真实的 `log.info("...")` 字符串，作为查询关键词。不要 clone 仓库——search API 单次返回足够定位。

##### Staging K8s 日志查询（logs-v2）

Staging-US/EU 的应用日志共享区域级 logs-v2 索引空间。每次查询**必须**添加至少一个已确认的 `kubernetes.labels.app` 过滤，避免混入其他项目日志。需跨项目排查时，先解析出每个项目的具体 Pod `app` 值，再逐个项目分别查询；禁止通过去掉过滤条件扫描共享索引。

**AWS Staging 位置**：Staging-US 和 Staging-EU 位于 AWS 账号 `390709477306` 下的两个独立区域集群，不是同一个集群。只把下列目录用于这两个环境；不要把 CN、GCP、Data 或遗留 staging 资源笼统归入 390：

| 环境 | ArgoCD Application 目录 | 集群 |
|------|---------------------------|------|
| Staging-US | `aws-390709477306-us-staging/` | `us-eks-staging` |
| Staging-EU | `aws-390709477306-eu-staging/` | `eu-eks-staging` |

**解析 `kubernetes.labels.app`**：

1. 先用 service catalog 的 `backstage.io/kubernetes-id`、`argocd/app-name` 和仓库信息定位服务。
2. 在 [`DEV/argocd-apps`](https://gitlab.addx.ai/DEV/argocd-apps) 的对应区域目录中找到 Application，读取 `spec.destination.namespace`，并同时兼容单源 `spec.source` 与多源 `spec.sources[]`。从实际参与渲染的 source 中取 `repoURL`、`targetRevision`、`path` / `chart`；遇到 Helm `valueFiles` 的 `$ref` 时，同时追踪对应 `ref` source，不要只读数组第一项。
3. 把 Application 的 `metadata.labels.app` 只当作候选服务族，**不得直接当作日志过滤值**。ArgoCD 不会把 Application 标签自动传播给所有 Pod。
4. 跟到源仓库对应 revision/path，读取 Deployment/Rollout/StatefulSet/Job 的 `spec.template.metadata.labels.app`。Helm/Kustomize 或多源组合无法从源文件直接确定最终标签时，检查该 Application 的精确渲染清单或目标集群中的实际 workload；一个 Application 有多个 workload 时，收集所有候选值并按用户要查的组件选择。
5. 查询成功后检查返回记录的 `_source.kubernetes.labels.app`，确认实际值；如返回了 namespace 字段，还要与 Application 的 `spec.destination.namespace` 对账，发现重名 `app` 混入其他 namespace 时立即停止展示。无结果时不能断言标签正确或该时段没有日志；回查 workload 清单或请用户确认组件。

例如 `troubleshooting-staging-us` Application 的标签是 `troubleshooting`，但其 Pod 标签分别是 `troubleshooting-backend`、`troubleshooting-frontend`、`middlequery`。

**查询流程**：

1. 调用 `GET /api/v1/staging-logs/fields`，确认当前 mapping 中存在 `kubernetes.labels.app`；如同时返回 `kubernetes.namespace_name`，后续查询也加上该 namespace 过滤。字段枚举失败时报告真实错误，不要猜字段名。
2. 按上述流程得到具体 Pod `app` 标签。
3. 调用 `POST /api/v1/staging-logs/query`，在 `filters` 中传 `{"field":"kubernetes.labels.app","operator":"is","value":"<pod-app-label>"}`；若第 1 步确认存在 namespace 字段，再追加 `{"field":"kubernetes.namespace_name","operator":"is","value":"<destination-namespace>"}`。自由文本放在 `query`，时间传 UTC ISO。默认从 15 分钟窗口、`page_size <= 100` 开始，证据不足再逐步扩大。
4. 如果查询报连接错误，再调用 `GET /api/v1/staging-logs/ping` 区分 logs-v2 连通性问题与查询条件问题。

当前 `/fields` 只返回字段名/类型，不返回 `app` 的可选值。无法从 catalog、ArgoCD 和 workload 清单确定标签时，先向用户确认；不要无过滤扫描整个共享索引。

##### iot-service 日志加载 + 查询流程（仅 Prod 环境）

**仅 Prod 环境**的 iot-service 日志不直接写入 ES，必须先加载后才能查询。Staging 环境不进入此流程，改走 `staging-logs/query`。

1. **查状态**：`POST /api/v1/es-data/status` → 从响应提取 `es_data_status` 数组（状态值：`unloaded`/`loading`/`partial`/`complete`/`cleaned`/`failed`）
   - `cleaned` 表示数据曾加载但已被清理，需重新加载
2. **加载缺失数据**：筛选 `status` 为 `unloaded`/`partial`/`failed`/`cleaned` 的条目 → 组装为 `missing_data_items` → `POST /api/v1/es-data/load-data`
   - **关键**：`id_value` 必须使用 status 返回的脱敏 ID，不要替换为原始 ID
   - 加载耗时可能较长（数据量大时 **5-10 分钟**），优先缩短日期范围、只加载所需 platform + collection_type 以加速
3. **确认加载已启动**：load-data 返回后，立即再调用 `POST /api/v1/es-data/status` 确认目标条目状态已变为 `loading`。如果仍为 `unloaded`，说明加载未实际触发，需检查请求参数
4. **轮询任务**：`GET /api/v1/es-data/task-status/{task_id}`（每 30 秒一次），告知用户预计等待时间。轮询超过 **15 分钟**仍未完成 → 告知用户当前进度，询问是否继续等待或换更窄的时间范围
   - 可用 `DELETE /api/v1/es-data/task/{task_id}` 取消尚未开始的 pending 任务
5. **查询日志**：`POST /api/v1/log-search/query/es`
6. **查 DB 数据**：`POST /api/v1/log-search/query/db`（见上方「用户/设备全景查询」章节）
7. **设备图表**：`POST /api/v1/log-search/figure`

##### Prod 其他服务日志查询

Prod 的 statemachine、kiss、auth、ai-saas 等服务日志直接写入旧 ES，无需加载，直接查询：

1. **获取可用索引列表**：`GET /api/v1/log-search/index-patterns` — 获取当前环境所有可查询的 ES 索引（**不包含 iot-service 索引**）。**禁止自行拼接索引名，必须从此接口返回的列表中选择**
2. **确认索引**：根据用户描述从列表中匹配可能的索引，向用户确认："是否要查询这个索引？"。如果无法确定，将索引列表展示给用户让其选择
3. **查询日志**：`POST /api/v1/log-search/query-index` — 按索引名直接搜索，过滤语法从 Swagger 获取

#### Cross-reference（ID 关联查询）

通过 `POST /api/v1/cross-reference/query` 遍历 ID 依赖图，解析关联关系。**返回的所有 ID 均为脱敏格式（`trouble_shooting_id:xxx`）**，不会返回明文 ID。

**典型场景**：客户提供 ticket_id 或 email → 返回脱敏后的 user_id 和所有 device_sn → 可直接用于后续查询（大部分接口自动识别脱敏 ID）。

**ID 依赖关系图**：

```
ticket_id → email (Zendesk)
email → user_id (iot-service)
user_id ↔ device_sn (双向)
device_sn → device_mac
user_sn → device_sn
```

**ID 格式自动识别**：用户提供 ID 时无需指定类型，根据格式自动判断：

| 格式特征 | ID 类型 | 示例 |
|---------|---------|------|
| `AIC` 开头 15 位字母数字串 | `user_sn` | `AIC4BAWLXK82375` |
| `HUB` 开头 15 位字母数字串 | `user_sn` | `HUB1234ABCD5678` |
| `KC` 开头 14 位字母数字串 | `user_sn` | `KC123456789012` |
| `GL` 开头、总长度 14 位 | `user_sn` | `GL123456789012`（合成示例） |
| 32 位十六进制字符串 | `device_sn` | `824c796691e72b4e2acc7e3388395368` |
| 纯数字 | `user_id` 或 `ticket_id` | `5010000`——上下文明确（用户提到"工单"/"ticket" → `ticket_id`）才直接用；**否则必须问用户**"这个是 user_id 还是 ticket_id？"，不要默认猜 |
| 含 `@` 的字符串 | `email` | `user@example.com` |
| MAC 地址格式 | `device_mac` | `AA:BB:CC:DD:EE:FF` |
| `trouble_shooting_id:` 前缀 | 脱敏 ID | `trouble_shooting_id:ad49f9bb` |

> **GL 发布边界**：14 位 `GL` User SN 当前已在 Staging 后端支持。在 Prod 使用前必须先确认生产后端已发布 GL 校验；未发布时如实说明后端尚不支持，不要向 Prod 接口发起 GL 查询。

> 如果格式无法确定，尝试用 `user_sn` 和 `device_sn` 分别查询 cross-reference。

#### 工单系统

端点：`/api/v1/ticket/*`（add、list、get、update、cancel、finish、review、execute 等）

流程：创建 → 审批人 review → 审批通过 → 执行

#### 分析

| 端点 | 用途 |
|------|------|
| `POST /api/v1/analytics/user-error-summary` | 用户错误概览 |
| `POST /api/v1/analytics/device-events-by-session` | 按 Session 查设备事件 |
| `POST /api/v1/analytics/device-events-by-timestamp` | 按时间戳查设备事件 |

#### DB 查询

- `GET /api/db_query/config` — 获取可用的预定义诊断查询列表
- `POST /api/db_query/execute` — 执行诊断查询（只读，通过 IoT Service 代理，不支持任意 SQL）

**可用诊断查询**：`camera:payment_flow`（支付流水）、`camera:airwallex_payment`（Airwallex 支付）、`camera:order`（订单）、`camera:user_vip`（VIP 状态）、`piiVault:device_emergency`（设备紧急信息）

#### ID 批量校验

- `POST /api/v1/anonymous/{type}/check` — 批量校验 ID 有效性（支持 serial_number、user_id、user_sn、email），返回 valid/invalid/expired 状态和汇总统计

### 次要功能域

以下功能域详细参数见 Swagger 文档：

- **Chart & Dataset**：`/api/v1/chart/*`, `/api/v1/dataset/*`
- **凭证管理**：`/api/v1/credential/*`
- **用户管理**：`/api/v1/user/*`
- **DB 管理**：`/api/v1/manage/db/scripts/*`
- **ES 索引管理**：ISM 策略 CRUD、索引分层迁移（warm/cold）、索引模式管理（`/api/v1/es-index-manage/*`）
- **日志 & 审计**：`/api/v1/logs/*`
- **匿名校验**：`/api/v1/anonymous/{type}/check`（批量校验 ID 有效性）
- **系统**：`/api/health`, `/api/health/detailed`, `/api/login/*`, `/api/current`

### 操作红线

- **禁止**执行 DB 降级脚本（`/manage/db/scripts/downgrade`），除非有明确审批
- 查询结果可能包含 PII 数据，注意脱敏

### 诊断铁律（防止误诊的硬性规则）

排查任何"X 失败 / X 报错 / 设备无法 X"类问题时必须遵守，违反任何一条都可能导致结论完全错误。

1. **不要用"现状"解释"原因"**
   - `query/db` 等接口返回的是设备/用户**当前状态**（如 isBind=1、status=offline），不是某次操作的**调用历史**
   - 看到"现状 X" + 用户报"操作失败" → 禁止直接推断"因为 X 所以失败"
   - 必须找到"那一次操作"对应的事件级 / 日志级原始证据，再下结论

2. **用户给的"错误码"先验证出处，禁止凭经验套语义**
   - 用户说 "code N / -N / 某某 error" 时，先问/查这个码出自哪里：客服工单 / App 截图 / Sentry / 服务端日志？不同层级的码语义不同
   - 禁止"我记得这个码意思是 XXX"式推断
   - 出处不明 → 直接去事件表/日志拉**真实后端枚举**，而不是猜

3. **catalog 找不到 ≠ 不存在**
   - DataHub / Swagger 列表 / kubectl get 等 catalog 类工具都可能漏收或缓存陈旧
   - 任何"找不到 X"必须用底层接口兜底验证：information_schema / OpenAPI 直接 list / `kubectl get -A` / etc.
   - 兜底确认"真的不存在"之前，不准放弃这条线索

4. **见到 enum / state / code 字段必须查定义，禁止用名字猜**
   - 看到 `status=2` / `state=5` / `code=10` 这种数值字段，第一动作是查它的枚举定义（代码 / 字典表 / 文档 / 同事），不能假设"5 看着像失败"
   - 每次写"这个字段是 X 意思"之前必须有明确引用源（代码路径 / 文档链接 / 字典表）

5. **写结论前必须问："还有更直接的证据吗？"**
   - 看到一个看着合理的解释就停手 = 反模式
   - 同一个问题至少用两个不同维度的数据源交叉验证
   - 写"我认为根因是 X"之前，自问："如果根因是 X，应该能在哪个表/日志看到一条明确证据？查到了吗？" 查不到 = 继续找
   - 结论里必须带证据引用（event_id / 时间戳 / 日志行 / SQL 查询结果），不能只有推理链

### 场景操作指南

针对特定业务场景的详细排查流程，存放在各子目录下。**遇到匹配场景时，必须先检查该指南的环境范围；环境适用时再读取对应文件并执行。**

| 场景关键词 | 参考文件 | 说明 |
|-----------|---------|------|
| 认证、Token、401 | [references/auth_flow.md](references/auth_flow.md) | Token 自动获取流程 |
| 订阅、VIP、支付、套餐、云存储、免费试用 | [subscription/subscription_issue.md](subscription/subscription_issue.md) | 用户订阅问题排查（套餐规则、升降级规则） |
| 设备离线、offline、断连、离线根因 | [device-offline-analyzer/guide.md](device-offline-analyzer/guide.md) | 仅适用 Prod-US/EU；Staging 不读取该指南，按本文 logs-v2 流程排查 |
| 设备状态、绑定状态、在线离线、设备清单、Excel 批量 | [device-status-checker/guide.md](device-status-checker/guide.md) | 设备绑定与在线状态查询 |
<!-- 后续新增场景指南按此格式添加：
| <场景关键词> | [<子目录>/guide.md](<子目录>/guide.md) | <说明> |
-->

### 常见工作流

1. **设备故障排查**：Cross-reference 查 device_sn 关联 → 确定环境 → Staging 用 `staging-logs/query` + Pod `app` 标签，Prod 按服务使用 `query/es` 或 `query-index` → 关联其他服务
2. **用户订阅问题**：`query/db` 查询用户设备绑定和用户信息 → 结合 Cross-reference 关联分析
3. **iot-service 日志查看**：Prod 用 es-data/status → load-data → task-status 轮询 → query/es；Staging 解析 Pod `app` 标签后直接用 `staging-logs/query`
4. **用户错误分析**：`user-error-summary` 概览 → `device-events-by-session` 深入排查

## Examples

### Bad

```bash
# 直接执行 DB 降级 → 高危操作
curl -X POST ... "/api/v1/manage/db/scripts/downgrade" -d '{...}'

# 没有检查 Token 直接调用 API → 401
curl -X POST "$BASE_URL/api/v1/log-search/query/es" -d '{...}'

# load-data 使用原始 ID → 报错
curl ... -d '{"missing_data_items":[{"id_value":"235408",...}]}'
```

### Good

```bash
# Staging：无需导入，按 Pod app 标签查询共享 logs-v2 索引
curl -s -X POST -H "Authorization: Bearer $TROUBLESHOOTING_TOKEN" \
     -H "Content-Type: application/json" \
     "$BASE_URL/api/v1/staging-logs/query" \
     -d '{"query":"error OR timeout","filters":[{"field":"kubernetes.labels.app","operator":"is","value":"troubleshooting-backend"}],"start_time":"2026-09-03T01:00:00Z","end_time":"2026-09-03T01:15:00Z","page":1,"page_size":100,"sort_order":"desc"}'

# Prod iot-service：ES 数据加载完整流程
# Step 1: 查询数据状态
STATUS_RESP=$(curl -s -X POST -H "Authorization: Bearer $TROUBLESHOOTING_TOKEN" \
     -H "Content-Type: application/json" \
     "$BASE_URL/api/v1/es-data/status" \
     -d '{"id_type":"user_id","id_value":"235408","start_date":"2026-03-10","end_date":"2026-03-12","es_data":{"app":["event","log"],"backend":["log"]}}')
echo "$STATUS_RESP" | jq '.result.data.es_data_status[] | select(.status=="unloaded" or .status=="partial" or .status=="failed")'

# Step 2: 用 status 返回的脱敏 ID 构造 load-data（不要替换为原始 ID）
LOAD_RESP=$(curl -s -X POST -H "Authorization: Bearer $TROUBLESHOOTING_TOKEN" \
     -H "Content-Type: application/json" \
     "$BASE_URL/api/v1/es-data/load-data" \
     -d '{"missing_data_items":[{"id_type":"user_id","id_value":"trouble_shooting_id:abc123","platform":"app","collection_type":"event","status":"unloaded","missing_date_ranges":[["2026-03-10","2026-03-12"]]}],"force_reload":false}')
TASK_ID=$(echo "$LOAD_RESP" | jq -r '.result.data.task_id')

# Step 3: 轮询 task-status（每 30 秒一次）
curl -s -H "Authorization: Bearer $TROUBLESHOOTING_TOKEN" \
     "$BASE_URL/api/v1/es-data/task-status/$TASK_ID" \
     | jq '{status: .result.data.status, progress: .result.data.progress}'
```

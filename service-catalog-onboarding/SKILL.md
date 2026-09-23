---
name: service-catalog-onboarding
description: 让你的仓被公司内部开发者门户（Backstage/RHDH，infra/backstage）的服务 & 能力目录正确收录 —— 在仓内放对 catalog-info.yaml + mkdocs.yml + ADR + 关联注解，门户的 GitLab discovery 自动注册（不用「去门户里注册」）。当用户提 "catalog-info.yaml"、"接入服务目录"、"backstage 收录我的仓"、"我的服务怎么注册到目录"、"该建 Domain 还是 System"、"粒度怎么切"、"techdocs 没显示 / mermaid 不渲染 / API definition / Swagger UI 空"、"ADR 标签页 / docs/adrs / backstage.io/adr-location / ADR 怎么写"、"techdocs-entity / techdocs-entity-path / 文档精准指向"、"契约跟代码漂了 / drift gate / openapi 跟实现不一致"、"source-location / view-url / View Source 跳错"、"我要做什么才算接入了 IDP" 时主动触发；即使用户只是随口说"我新建一个仓要怎么让 backstage 看到"也要触发。反查目录（"我要找 X 能力调谁"）用 service-catalog-search，不是本 skill。
---

# service-catalog-onboarding —— 你的仓要做什么才算「正确接入了服务 & 能力目录」

> **规范 SSOT**：本仓 [`public/dev-standards/architecture/catalog-schema.html`](../../public/dev-standards/architecture/catalog-schema.html)（字段三分法 A/B/C；2026-08-11 从 `engineering/architecture` 仓迁入）+ `infra/backstage` 的 `docs/deployment/ci.md`（A 类字段的 CI 约定）。能力 / Domain 的语义说明 + Ubiquitous Language 在本仓 [`public/dev-standards/architecture/catalog-glossary.html`](../../public/dev-standards/architecture/catalog-glossary.html)（**不再**是命名白名单 —— catalog 本身是 SSOT）。5 层 / Domain 名规范见本仓 [`public/dev-standards/architecture/backend-service-architecture.html`](../../public/dev-standards/architecture/backend-service-architecture.html)。

---

## 核心原则 — §0 速览：字段三分法 + 7 件事 + 一键起步

**字段三分法（先理解这个）**：

| 类别 | 谁产生 | 你在仓里做什么 |
|---|---|---|
| **A. 代码派生**（API 契约） | `.api`/`.proto` → CI 生成 OpenAPI/proto | CI 生成 + commit；`API.spec.definition.$text` 指向它（§5）|
| **B. 自动发现**（运行态） | GitLab / K8s / ArgoCD / CI / Sentry / … | **什么都不写** —— 只在 `catalog-info.yaml` 里写「关联注解」让门户自动拉（§3 annotations）|
| **C. Curated**（语义判断） | 人 | 手写在 `catalog-info.yaml`（§2）—— 全用 Backstage 原生 kind/字段 |

**最重要 7 件事**：

1. **粒度先定**（§1）：业务高内聚 + 业务边界清晰 ≠ 团队规模 ≠ 端 ≠ 必须独立部署。一个 monorepo 推荐 = 一个有 owning repo 的 Domain。
2. **Domain 实体住哪**：判据是「**有没有 owning repo**」不是「是不是叶子」。有 → 在那个 repo 顶部声明（带 `subdomainOf`）；没有 → `engineering/architecture/catalog/domains.yaml`。完整规则见 §1。
3. **仓根唯一 catalog-info.yaml**（§2 hard rule）：monorepo 多 entity 用 `---` 分隔；**禁止子目录建 catalog-info.yaml**。
4. **`catalog-info.yaml` 必须**：`kind: Component`（+ 可选 API/Resource）、kebab-case name、`spec.lifecycle`、`spec.owner`、恰好一个 `layer-*` tag、`links[].url` 必须是 http(s) 绝对 URL（§2）。
5. **annotations 显式覆盖 source-location/view-url/edit-url**（hard rule，§3）—— 默认从 `managed-by-location` 派生 → 全部跳到 catalog-info.yaml，不精准。
6. **`docs/` 镜像 catalog 层级，项目文档 authoring SSOT 统一 HTML**（§4）：`docs/index.html` = Domain 入口、`docs/architecture/<system>/<component>/index.html` = Component overview、ADR 落 `adrs/`（option C，§4.2）。Markdown 只允许作为门户兼容构建产物，不作为人工维护的源文档。
7. **TechDocs Approach B + API 契约从代码生成 + drift gate**（§5/§6）：仓根唯一 `mkdocs.yml`，host Component 用 `techdocs-ref: dir:.`，其它用 `techdocs-entity` + `techdocs-entity-path` 深链；`providesApis`/`consumesApis` 引用合法性用 `service-catalog-search` 反查 catalog（catalog 是 SSOT）。

**一键起步（cp 模板到你仓根）**：见 [`scripts/`](scripts/) 目录的 `catalog-info.yaml.template` / `mkdocs.yml.template` / `mermaid_hook.py` / `api-drift/` / `gitlab-ci-snippets/api-drift.yml`。完整使用步骤在 `scripts/README.md`（如果没有就照各模板顶部注释操作）。

填好 `catalog-info.yaml`（按 §2 / §3 checklist）+ push → `add-catalog-info` 分支 → GitLab discovery 自动扫到 → 门户实体页生成。

---

## 规则 — §1 Domain / System / Component 粒度判定（canonical SSOT）

**原则：粒度按「业务高内聚 + 业务边界清晰」定 —— 不按团队规模、不按端、不按是否独立部署。**

- **团队规模不是判据**。1 个人也可以 own 一个 System / 整个 Domain（例：`engagement` 域 4 个 Component 可能由 1-2 人 own —— 不要因为人少就合成一个 Component）。
- **Domain 按业务高内聚切，不按端切**。一个 Domain 横跨后端 + Web + App + 嵌入式（各是一个 Component / 进对应的 System）。例：`engagement-flutter-sdk` + `engagement-ios-sdk` 跟 `engagement-service` 同域。
- **一个 repo 推荐对应一个自治的 Domain**（有 owning repo 的 Domain）。Domain 级 monorepo（后端 + 前端 + App + SDK 一仓）；不推荐 System 级的仓。父域 / BU 级 Domain（如 `monetization`，无 owning repo）跨多个有 owning repo 的 Domain。

### 三层定义

- **Component** = **业务高内聚** + 可独立 build / 部署 / 拥有的软件单元（一仓，或 monorepo 一个目录）。`spec.type` ∈ `service`/`website`/`library`/`mobile-app`/`firmware`。一个 Component 可暴露多个 API（不按 API 数量拆）。

    **拆 Component 的 PRIMARY 判据 = 业务边界清晰，不是「必须能独立部署」**（hard rule，最常见误判）。「独立部署 / 独立 publish」是业务边界清晰带来的**可能性 / 后果**（SECONDARY consequence），**不是前置条件** —— 即使代码暂时共部署 / 共 binary / 共 pubspec / 共 build artifact，业务边界清晰就值得拆 Component。catalog 是**业务边界 SSOT**，不是部署形态 SSOT。

    **三条全 yes 才拆 Component**：
    1. **业务逻辑高内聚** —— 这部分逻辑的变更频率 / owner / 演进节奏跟其它部分独立（不会被一起改）
    2. **接口契约清晰** —— 对外只暴露 ports / 公开 API，调用方不依赖内部实现（详见 [architect §4c 跨 Component 引用规则](../architect/SKILL.md#4c-跨-component-引用规则hard-rule)）
    3. **有独立可演进的可能** —— 哪天想换语言 / 换团队 / 拆独立 release / 替换实现，不用动其它 Component

    三条都满足但代码**暂时共部署 / 共 binary / 共 pubspec**，**仍然拆 Component**（共部署多 service Component 形态见 [references/composite-service.md](references/composite-service.md)；library / mobile-app / firmware 同理 —— 业务边界清晰就拆，物理拆分留 backlog）。

    **`spec.type` 决定独立部署的「实施形式」参考**（描述事实，不是拆 Component 的 gate）：

    | `spec.type` | 现在或将来独立部署 / publish 时的形态 | 典型 artifact |
    |---|---|---|
    | `service` | 独立 K8s Deployment | 容器镜像 → Deployment |
    | `website` | 独立 nginx / CDN / Vercel deployment | 静态站 / SSR app |
    | `library` | 独立 publish 到 package registry | pub / npm / pypi / maven / cargo / Conan / BCR package |
    | `mobile-app` | 独立 build artifact 上架 App Store / Play / 企业分发 | IPA / APK / AAB |
    | `firmware` | 独立 build artifact OTA / 烧录 | .bin / .uf2 / .hex |

    type 回答的是「这个 Component 的最终 publish 形态」，不是「现在能不能 publish」—— 即使现在没拆出独立 publish 形态，业务边界清晰就属于对应 type。

    **App / 固件 顶层 mobile-app / firmware Component 是否要建** —— case-by-case 三选矩阵（详细样例 + SDK 仓 vs App 仓对比见 [references/examples.md](references/examples.md)）：

    | 场景 | 顶层 Component | mobile-app/firmware config 挂哪 |
    |---|---|---|
    | **SDK / 库仓** | 无顶层 Component | n/a（SDK 不上架 / 不烧录单设备）|
    | **App / 固件 + 极薄 shell**（默认推荐）| 无顶层 Component（System 直装 N library）| 挂 **System annotations**（Crashlytics / App Store / Memfault / OTA channel 等）|
    | **App / 固件 + 厚 shell**（独立体系，几千行以上）| 1 个 `mobile-app` / `firmware` Component | 挂这个 Component annotations |

    判据：shell 跟业务 lib 的边界 + 独立演进可能。**强行为极薄 shell 建 Component 会让 "shell Component 一年不变更"**，违反 Component 三条判据的 ②③。

    **决策提示**：写 `kind: Component` 前先答两个问题 ——
    - **Q1（拆不拆）**：这块逻辑跟其它部分是不是有清晰业务边界（独立变更频率 / 对外只走 ports / 有独立演进可能）？三条全 yes → 拆 Component。
    - **Q2（什么 type）**：这 Component 的 publish artifact 形态是什么？→ 决定 `spec.type`。

    **方案设计 vs 代码现状可解耦**（hard rule）：catalog 描述**业务边界 SSOT**，代码物理拆分是后续重构方向 —— catalog 可写 N Component（业务边界已清晰），代码暂未按 N package 拆开（单体 lib / 单 binary / 单 Conan project，仍在重构 backlog 上）也合法。N 个 Component 的 `source-location` 全部指向**当前代码里对应的子目录**即可。**禁止**指向不存在的 future melos / Conan 路径，否则 Backstage 实体页所有 "View Source" 全部 404、catalog drift 检查立刻爆。

- **System** = 一组协作完成某功能的 Component（+ 它们的 API / Resource），统一 ownership、隐藏内部实现。**判据：一个不含任何 Component 的 System 不该存在**。

    **System ≠ 代码分包、= 治理边界**。System 粒度判据不是「是不是一个进程」「是不是一个 Deployment」，而是「是不是一片有独立 ownership / 独立演进节奏 / 独立对外契约的治理切片」：

    - 单一后端服务（单进程、单 Deployment）→ System 装 **1 个** Component 完全合法（e.g. `engagement-backend` System 装 1 个 `engagement-service`）。
    - 一个 binary 里多个**内部业务单元**要不要升为多个 **System**（注：升为多个 **Component** 共用 `kubernetes-id` 是 [composite-service.md](references/composite-service.md) 的事，不是这里），看 3 条全 yes 才切 System：① 单元有独立 ownership / SLO / ADR 演进节奏；② 单元之间是**接口调用**（即使是 in-process function call），不是纯共享数据库；③ 单元**有可能**被独立部署 / 换语言 / 换团队 own。三条只满足 1-2 条 → 留在同一个 System 里，按 [composite-service.md](references/composite-service.md) 拆成多个 Component（共享 `kubernetes-id` 表示共部署）。
    - ❌ 按代码目录切（`internal/handler` / `internal/logic` 各一个 System）—— 那是代码分层，不是治理边界。
    - ❌ 按 API 切（`touchpoint-api` System、`webhook-api` System）—— System 名应是业务能力名（`engagement-backend`），API 是 Component 暴露的契约，不是 System 的 identity。

- **Domain** = 业务领域（DDD 限界上下文 / 同一条产品线）。装 System，不直接装 Component。可嵌套（`spec.subdomainOf`）。垂直产品永远建；横向区域只在「治理上有意义」时建。

### 「是一个 Domain 还是几个？」—— 限界上下文测试

1. **同名不同义**（"用户"/"产品"/"event"在两块是不是同一模型？）→ 不同 Domain。
2. **不同主键**（per-user vs per-device）→ 大概率不同 Domain。
3. **演进节奏 / SLA / 合规断层** = 边界。
4. **独立算法/数据团队、独立代码仓** → 自然独立。
5. **别过度拆**。横向 Domain 起步先一个，需要时再拆子 Domain。

### Domain 实体声明位置 —— canonical 规则

**判据：「有没有 owning repo」，不是「是不是叶子」**：

- **有 owning repo 的 Domain（任何层级，不限叶子）** → 那个 repo 根 `catalog-info.yaml` 顶部声明 `kind: Domain`（带 `spec.subdomainOf: <parent>` 如果有父）。**同 repo 还要声明域内任何没有独立 owning repo 的 sub-Domain**（这些 sub-Domain 写 `subdomainOf: <本仓 Domain>`）。**默认推荐路径**。
- **没有 owning repo 的 Domain** → `engineering/architecture/catalog/domains.yaml`。典型：BU 父域（`monetization`/`content`/`devices`）/ 跨多 owning repo 的纯聚合分组。
- **Group / 跨仓 Resource** → `engineering/architecture/catalog/groups.yaml`（**待 GitLab org discovery 接入后**自动生成）。
- **System** → 在它的主 Component 仓 `catalog-info.yaml`，`spec.domain` 指所属 Domain。
- **Component / API / Resource** → 各自的仓（monorepo 用 `---` 分）。
- 跨域 / 跨仓引用直接写（被引的还没建 catalog-info.yaml 时门户显示「未知」，正好是 TODO 信号）。

**三种情况**：(A) Domain 有 owning monorepo → 在那个 repo 声明（例：`engagement`、`vip`、`golf`）；(B) BU 父域 / 纯聚合无 owning repo → architecture 仓 `domains.yaml`（例：`monetization`）；(C) Domain 在某 repo 下、自己没独立 repo 但有 sub-Domain → sub-Domain 也在父 Domain 的 repo 里声明带 `subdomainOf`。

### 真实样例

4 个完整样例（平台型 / 垂直产品 Golf 4-System 含 golf-app / SDK 仓 / 嵌入式固件）见 [**references/examples.md**](references/examples.md)。一句话总结：

- **平台型**（engagement / vip）：1 Domain → N System（backend / console / sdk）→ 1+ Component；**没有共部署多 Component 形态**。
- **垂直产品**（golf）：1 Domain → 4 System（backend / moments / admin / app）→ 多 Component；`golf-backend` 内部用 Composite Service 控制运维成本，`golf-app` 内部用 N 个 library Component 描述端业务边界（无顶层 mobile-app shell，按"极薄 shell"路径走）。
- **SDK 仓**：1 System 装 N library Component，无顶层 SDK Component。
- **嵌入式固件**：默认无顶层 firmware Component（极薄 main），firmware config 挂 System annotations。

> 本节是 Domain 声明位置 + Component 拆分判据的 **canonical SSOT**。后续 §2 / §3 / §4 涉及一律 cross-link 回这里。

---

## §2 仓根唯一 `catalog-info.yaml`（必须）—— C 类字段

### Hard rule —— 仓根唯一 catalog-info.yaml

**monorepo 必须仓根唯一 `catalog-info.yaml`；多 entity 用 `---` 分隔；禁止子目录建 catalog-info.yaml**。

**理由**：
- `GitlabDiscoveryEntityProvider` 默认只扫仓根，子目录 `catalog-info.yaml` 必须靠 `kind: Location` 路由（footgun）
- 拆子目录降低可搜索性 / 可维护性 / SSOT 原则
- `engineering/architecture` 仓踩过 `Location.spec.targets`（复数）展开 footgun

**正例**：`applications/golf` 仓根 `catalog-info.yaml`（1 Domain + 4 System + 14 Component + 10 API + 2 Resource = 31 entities，全在仓根用 `---` 分隔）。

**何时例外可用 `kind: Location`**：仅当 GitLab discovery 扫不到 monorepo 子目录代码（如 git submodule 形态），且 `infra/backstage` 的 `app-config` `catalog.locations` 显式列了 target；**不是默认推荐路径**。

⚠️ 用 `kind: Location` 时**别用 `spec.targets`（复数）**：`GitlabDiscoveryEntityProvider` 不展开多 target —— 拆成多个单 `spec.target` 的 Location，或在 `infra/backstage` 的 `app-config` 里 `catalog.locations` 显式列。

### 仓根 catalog-info.yaml 结构

一份服务仓通常 = `1 个 Component`（主）+ `0..n 个 API`（它 `providesApis` 的能力）+ `0..n 个 Resource`（它拥有的基础设施）+ 可选 `kind: Domain` / `kind: System`。**完整字段框架样板**：[`scripts/catalog-info.yaml.template`](scripts/catalog-info.yaml.template)。

⚠️ **已知局限**：门户的 GitLab 插件「Issues」标签页是项目级 —— monorepo 子 Component 共享 project-slug，会看到整仓的 issue（缓解方案 `a4x.io/monorepo-role: secondary` 见 `infra/backstage#20`）。

### Component（主）字段 checklist

- [ ] `kind: Component`；`spec.type` ∈ `service`(后端 go-zero) / `website`(前端 admin/console) / `library`(可发布的库) / `mobile-app` / `firmware`
- [ ] `metadata.name`：kebab-case，与仓名末段一致；`metadata.title`：人读名；`metadata.description`：一句话说清干什么（别写「待补」）
- [ ] `spec.lifecycle` ∈ `production` / `experimental` / `deprecated`（细状态用 tag `status-*`）
- [ ] `spec.owner`：指向 `Group`（待 GitLab org discovery 接入后用真实 group；现用占位 `group:default/team-<name>` + `# TODO`）
- [ ] `spec.system`：归属的 `System`（System 在它的主 Component 仓里声明）。**Domain 声明位置见 §1**
- [ ] `spec.providesApis: [...]`：每个 = 一个 `kind: API` 实体；没有就 `[]` + 注释（别瞎编）
- [ ] `spec.consumesApis: [...]`：你调的别人的能力（B 类可由 CI 扫 zrpc client 自动推断）
- [ ] `spec.dependsOn: [...]`：语义依赖（`component:default/x` / `resource:default/y`）
- [ ] `metadata.tags`：
  - **恰好一个 `layer-*`**：`layer-vertical-product` / `layer-business-platform` / `layer-base-platform` / `layer-shared-infra` / `layer-company-infra`
  - 能力关键词（`push` / `notification` / `feature-flag` / `iot` …）—— AI 反查靠这些
  - `no-direct-*` 约束：`no-direct-fcm` / `no-direct-stripe` / `no-direct-growthbook` / …
  - ⚠️ tag 只能 `[a-z0-9+#-]`，**不能有 `/`**（用 `layer-base-platform` 不是 `layer/base-platform`）
- [ ] `metadata.links`：必含 `{ title: 仓库, url: https://gitlab.addx.ai/<group>/<repo>, icon: github }`
  - ⚠️ **`links[].url` 必须是合法 http(s) URL** —— 不能是 `git@…` SSH，**也不能是相对路径**（`/docs/…`、`./x` 都让 catalog policy 拒「`links.0.url` is not valid」→ 整个实体处理失败）
  - **website 类 Component 的 prod/staging 域名打 🌐 link** 见 [references/website-links.md](references/website-links.md)
- [ ] `metadata.annotations`：见 §3

### API（每个能力一个；`kind: API`）字段 checklist

- [ ] `metadata.name`：能力名（kebab-case）。**catalog 是 SSOT，无需预先去清单注册** —— 加 `kind: API` 实体就生效；引用一致性见 §6.1
- [ ] `metadata.title` / `metadata.description` / `metadata.tags`（反查靠这个）
- [ ] `spec.type` ∈ `openapi`（HTTP）/ `grpc`（RPC，.proto）/ `asyncapi`（事件流）/ `graphql`；包级接口没更贴的就 `openapi`
- [ ] `spec.lifecycle` / `spec.owner` / `spec.system`：和主 Component 一致（或省略 system）
- [ ] `metadata.annotations.backstage.io/techdocs-ref: dir:.` —— 让 API 实体页有「Docs」标签（指同仓的 TechDocs，需 §5 mkdocs.yml）
- [ ] `metadata.links`：可省略（Docs 入口由 `techdocs-ref` 给）；要加只能用**绝对 http(s) URL**（不要 `/docs/…`）
- [ ] **`spec.definition` —— A 类**：`{ $text: ./<契约文件路径> }`，详见 [references/api-contract-drift.md](references/api-contract-drift.md)。⚠️ `.api` 本身不是 OpenAPI，别拿 `type: openapi` + `$text` 直接指 `.api`。⚠️ 内联写 OpenAPI 时值里有 `: ` 必须加引号；推荐放成单独文件用 `$text`，别内联

### Resource（你拥有的基础设施；`kind: Resource`）字段 checklist

- [ ] DB / S3 桶 / Kafka topic / APISIX 路由 / 第三方底层占位（`fcm`/`apns`/`stripe`/`mqtt`）；`spec.type: database`/`s3-bucket`/`kafka-topic`/`external-channel`/…
- [ ] 主 Component 的 `spec.dependsOn` 里要列上（`resource:default/<name>`）
- [ ] Crossplane 开的资源：加注解 `terasky.backstage.io/crossplane-claim: <namespace>/<claim-name>` 指向集群 Claim（详见 [references/source-location-and-annotations.md](references/source-location-and-annotations.md)）
- [ ] **`source-location` 必须指 k8s/Crossplane manifest，不是 catalog-info.yaml**（详见 §3 hard rule + [references/api-contract-drift.md §6.2](references/api-contract-drift.md#62--resource-source-location-必须指-manifesthard-rule)）

### 文件顶部规范

- [ ] 注释指向规范：`# Backstage Software Catalog —— A4x 原生 System Model（见 engineering/skills 仓 public/dev-standards/architecture/catalog-schema.html）`

> **不归你仓声明**：`kind: Group` / `User`（待 GitLab org discovery 自动生成）；ArgoCD `Application`（在 `DEV/argocd-apps`）；IAM/IRSA Role（在 `DEV/crossplane-infra`）；**`kind: Domain` 见 §1（有 owning repo 的就在你这）**；`kind: System` 在主 Component 仓声明。

---

## §3 source-location / view-url / edit-url —— per-entity 显式覆盖（hard rule）

**默认从 `managed-by-location` 派生 → 整仓所有实体的「View Source」都跳 `catalog-info.yaml`，不精准。每个实体显式覆盖。**

**写法纪律（指文件 vs 指目录）**：

```yaml
annotations:
  # 指目录：末尾带 /，用 -/tree/...，url: 前缀
  backstage.io/source-location: url:https://gitlab.addx.ai/<group>/<repo>/-/tree/<branch>/<subdir>/
  # view-url 不带 url: 前缀
  backstage.io/view-url: https://gitlab.addx.ai/<group>/<repo>/-/tree/<branch>/<subdir>/
  # edit-url 指代表性入口（index.html 优先），用 -/edit/...
  backstage.io/edit-url: https://gitlab.addx.ai/<group>/<repo>/-/edit/<branch>/<subdir>/<entry>.html
```

分支 = 仓当前 catalog dev 分支（如 `add-catalog-info`），merge 到 `main` 后批量改 `main`。

**详细规则 + 完整注解列表**（怎么扫每类实体的 source / Resource source-location 必须指 manifest / Crossplane / `a4x.io/cicd-app-name` / `backstage.io/kubernetes-id` / Sentry / Prometheus / PagerDuty / Grafana / Nexus / Crashlytics / Memfault 等）→ [**references/source-location-and-annotations.md**](references/source-location-and-annotations.md)。

**实战样板**：`services/value-added/engagement` 的 `catalog-info.yaml`（4 Component + 4 Resource 全部显式覆盖；MR !126）。

---

## §4 `docs/` 镜像 catalog 层级 + ADR

### 4.1 docs/ 镜像 catalog 层级（Domain → System → Component）

TechDocs Approach B（§5）要求 `docs/` 跟 catalog 实体层级一一对应，`techdocs-entity-path` 才稳定 —— **不对应 = 深链 404**。

```
docs/
├── index.html                                   # Domain 入口（深链 path: /）
├── architecture/
│   ├── index.html                               # 跨 System 总览
│   ├── domain-model.html                        # Ubiquitous Language SSOT
│   ├── <system>/                                # System 目录
│   │   ├── index.html                           # System overview（深链 path: /architecture/<system>/）
│   │   └── <component>/                         # Component 目录
│   │       ├── index.html                       # Component overview（深链 path: /architecture/<system>/<component>/）
│   │       ├── <topic>.html                     # 子组件 / 数据模型/状态机等
│   │       └── adrs/                            # per-Component ADR（option C，见 §4.2）
│   │           ├── index.html                   # ADR 索引
│   │           └── NNNN-<slug>.html
├── architecture/adrs/                           # repo-wide cross-Component ADR（option C）
│   ├── index.html
│   └── NNNN-<slug>.html
├── product/  testing/  deployment/  plans/  issues/
```

**规则**：每个 Component 在 `docs/architecture/<system>/<component>/` 有对应目录 + `index.html`；每个 System 在 `docs/architecture/<system>/` 有 `index.html`；Domain 总览写在 `docs/index.html`。`/architect` skill 的 `docs/` 约定跟这套对齐 —— 它读仓根 `catalog-info.yaml` 取 System + Component 名落进 `docs/architecture/<system>/<component>/`。

### 4.1.1 共部署多 Component（Composite Service）

多业务能力共部署 1 个 binary（运维成本 / 团队规模 / 跨 Component 通信延迟）—— **catalog 形态是 1 个 System + N 个 Component 共享 `backstage.io/kubernetes-id`**（不是合成 1 个 Component + N API）。详细范本 / 文档结构 / OpenAPI 文件组织 / 反模式见 [**references/composite-service.md**](references/composite-service.md)。

### 4.1.2 可选：vertical 视图作为 `techdocs-entity-path` 的目标

**默认仍是 §4.1 的 `docs/architecture/<system>/<component>/...`**（catalog 层级镜像 docs/，TechDocs 直跳 Component overview）。

**可选模式**：当一个**跨端云的业务垂直能力**（vertical / 业务垂直）由多个 System（如 backend / app / web）下的多个 Component 共同实现，且项目希望"读文档以业务能力为入口、而非以端为入口"时，可以让该业务下属的 Component 把 `backstage.io/techdocs-entity-path` 指到一棵 vertical 视图的 docs 树：

```
docs/architecture/verticals/<vertical>/
├── index.html
├── app/        ← 该 vertical 的 Flutter 端实现（合并多个 Component 的端侧文档）
├── backend/    ← 该 vertical 的后端实现（合并多个 Component 的云侧文档）
├── web/        ← 可选
├── contracts/  ← 端云契约
├── flows/      ← 跨端 sequence diagram
└── adrs/       ← 该 vertical 内 ADR
```

此时该 vertical 下属的 Component 各自把 `techdocs-entity-path` 指到对应子目录（`docs/architecture/verticals/<vertical>/app/` 或 `/backend/` 或 `/web/`），TechDocs 在门户里直接渲染 vertical 子树。

**这是项目级选择，不是 skill 强制**——选不选、vertical 怎么切、内部结构如何，写进**项目自己的 ADR**（HTML authoring SSOT，例如 `docs/architecture/adrs/0001-vertical-as-top-level-doc-grouping.html`）。横切件 / 纯单端 Component 仍按 §4.1 默认路径走，不强行塞进 vertical 视图。

**catalog 模型不变**：仍是 Domain > System > Component，**不引入** `kind: Vertical`（详见 §4.3）。

### 4.2 ADR 规范（option C：per-Component + repo-wide 双轨）

**ADR 内容规范参考 [`../architect/references/adr-format.md`](../architect/references/adr-format.md)**（5 H2 段和状态字段），但项目内 ADR 文件必须是 HTML authoring SSOT。本节 = 业务侧的接入规则。

| ADR 影响范围 | 路径 |
|---|---|
| **per-Component**（只影响单个 Component） | `docs/architecture/<system>/<component>/adrs/NNNN-<slug>.html` + 同目录 `index.html` |
| **repo-wide**（跨 Component） | `docs/architecture/adrs/NNNN-<slug>.html` + `docs/architecture/adrs/index.html` |

`NNNN` = 4 位零填充，单调递增。**不要 aggregator 文件**（多条 ADR 合一文件会破坏逐条决策的可链接性和门户渲染）。**为什么 option C**：单 Component 决策跟着 Component 走、随代码迁移；跨 Component 决策不属于任何 Component。

**catalog-info.yaml 注解**：每个有 ADR 的 Component 必填 `backstage.io/adr-location: docs/architecture/<system>/<component>/adrs`；repo-wide 由"主"Component（通常是 host）注解 `backstage.io/adr-location: docs/architecture/adrs`。如果当前门户 ADR 插件只支持 Markdown，由 CI 从 HTML SSOT 生成兼容产物；不要人工维护 `.md` ADR。

**模板要点**（详见 [adr-format.md](../architect/references/adr-format.md)）：HTML 页面必须有 5 个 H2 段（Context / Considered Options ≥2 / Trade-off Analysis 不只列优点 / Decision Outcome / Consequences）；元数据用 `<script type="application/x-yaml" id="adr-metadata">` 写 `status` / `date` / `deciders` / `supersedes` / `superseded-by`；**不删除任何 ADR** —— 改决策 = 新写一条把旧的标 `Superseded`，双向链；**空 ADR 不写** —— 信息不足时 `status: Pending`，Decision Outcome 节明确列"待回答问题"。

**ADR 索引 `index.html`** = 表格（编号 | 决策 | Status | Date），每加一条同步更新 —— 否则 ADR 标签页 + AI agent 找历史决策时会漏。

**CI 检查**：`backstage.io/adr-location` 路径目录存在；文件名匹配 `^\d{4}-[a-z0-9-]+\.html$`。建议：`adr-metadata` 必含 5 字段，status 合法，`Superseded` 必须有 `superseded-by` 指向真实 ADR。

#### 4.2.1 vertical 文档树下的 ADR location（可选模式补充）

若项目采用 §4.1.2 的 vertical 文档树，ADR 的归属位置同步调整：

| ADR 影响范围 | 路径 |
|---|---|
| **per-Component**（默认） | `docs/architecture/<system>/<component>/adrs/NNNN-<slug>.html` |
| **per-vertical**（vertical 内决策，跨该 vertical 下属多 Component） | `docs/architecture/verticals/<vertical>/adrs/NNNN-<slug>.html` |
| **repo-wide / 跨 vertical 元决策**（仓级） | `docs/architecture/adrs/NNNN-<slug>.html` |

对应 Component 的 `backstage.io/adr-location`：
- 默认 → `docs/architecture/<system>/<component>/adrs`
- 采用 vertical 模式 → `docs/architecture/verticals/<vertical>/adrs`
- repo-wide → 由 "主" Component（host）注解到 `docs/architecture/adrs`

**编号在各自目录内独立从 `0001` 起，不跨目录连号**（per-Component / per-vertical / repo-wide 三套编号空间互相独立）。仍遵循 §4.2 主规则：不删 ADR、推翻旧决策写新条目 + `Superseded by`、`Pending` 状态合法但不能空写。

---

## §4.3 vertical annotation pattern（可选，cross-end 业务 Component 桥接）

**适用场景**：项目采用 §4.1.2 vertical 文档视图（业务垂直能力跨多 System / 多 Component），希望在不引入自定义 Backstage entity kind 的前提下，让 catalog 能机器化"列出某 vertical 下属的全部 Component"。

**做法 = 三层 Component annotation**（不动 Domain / System / Component 三层模型）：

```yaml
metadata:
  annotations:
    # 1. 该 Component 归属的 vertical（kebab-case；horizontal 见下文）
    golf.addx.ai/vertical: <vertical-name>
    # 2. TechDocs 入口改指 vertical 子目录（替代默认 <system>/<component>/）
    backstage.io/techdocs-entity-path: docs/architecture/verticals/<vertical-name>/app/
    # 3. ADR 索引改指 vertical 内 adrs/（替代默认 <system>/<component>/adrs/）
    backstage.io/adr-location: docs/architecture/verticals/<vertical-name>/adrs/
```

**规则**：

1. **vertical-name** = kebab-case，业务能力词（`sync` / `play` / `ai-coach` / `share` / …），**不是端名**（不是 `app-sync` / `backend-sync`）。
2. **annotation key 用项目自己的反向域名前缀**（样例用 `golf.addx.ai/vertical`；其他项目自己换前缀，避免和 `backstage.io/` 官方 namespace 冲突）。
3. **横切件 / 纯单端 Component 用 `_horizontal`**（下划线前缀，表示"非 vertical"）：

    ```yaml
    annotations:
      golf.addx.ai/vertical: _horizontal
      # 注意：横切件的 techdocs-entity-path / adr-location 仍指原 <system>/<component>/，不改方向
    ```

    典型横切件：database / bridge / telemetry / observability / 第三方平台接入。

4. **annotation 是机器可读的**——未来若需要 Backstage UI 原生"按 vertical 浏览 Component 列表"，靠这个 annotation 在 plugin / search query 里过滤即可（不需要现在就引入 plugin）。
5. **不动 catalog 三层模型**：`spec.system` 仍指原 System（部署边界），`spec.domain` 仍是业务领域，**vertical 只是 annotation 元数据**。

**CI 校验建议**：

- annotation 值是 kebab-case，符合 `^([a-z][a-z0-9-]*|_horizontal)$`
- 非 `_horizontal` 时，对应目录 `docs/architecture/verticals/<value>/` 必须存在 —— 不存在 → CI fail（防漂移）
- 该 Component 的 `backstage.io/techdocs-entity-path` 若指 `docs/architecture/verticals/<X>/...`，则 `golf.addx.ai/vertical` 必须 = `X`（两个值一致）

**反模式**（不要这么做）：

- ❌ **自定义 `kind: Vertical` 实体** —— 要写并维护 Backstage plugin、跟随版本升级、自写 UI。除非项目体量足够大、且确有"在 IDP 里按 vertical 浏览"的强需求，否则 ROI 不划算（annotation + 反查 query 足够）。
- ❌ **把 System 改名以匹配 vertical**（如把 `golf-backend` 改名 `golf-sync` / `golf-play`）—— catalog 三层模型不动，System 是**部署边界**不是业务能力名。
- ❌ **vertical 文档和原 `<system>/<component>/` 文档都写一份** —— 必漂移。**选一个为 SSOT**，另一边只放 redirect（一句 "已迁至 …" + 链接）或干脆不存在。
- ❌ **vertical-name 写成端名**（`app-sync` / `backend-sync` / `flutter-sync`）—— 失去"跨端云一图"的意义；vertical 命名只反映业务能力。

> **vertical 的"准入规则 / 内部结构 / 何时新建 vertical"不在本 skill 范围**——那是项目架构 / `architect` skill 的事。本 skill 只管"catalog 怎么桥接到 vertical 文档树"。

---

## §5 TechDocs —— mkdocs + Approach B 深链（速览）

**Approach A vs B 决策**：

| 维度 | A（多 mkdocs） | B（单 mkdocs + 深链，**推荐**） |
|---|---|---|
| 仓内 mkdocs.yml 数量 | 每 Component 一个 | 仅仓根一个 |
| 深链稳定性 | 跨 Component 链接靠相对路径，重命名易断 | path 由 catalog 实体名生成，稳定 |
| 维护成本 | 高（每目录维护 mkdocs.yml + nav） | 低（仓根一份） |

**推荐 B**：跟 §4.1 docs/ 镜像 catalog 一起用 —— 一份 mkdocs.yml + 一棵 docs/ 树 + 注解深链 = Component / System / API 实体页都精准跳到自己那一节。

**详细 setup**（Approach B 注解样例 / mkdocs.yml + mermaid_hook.py + docs/index.html 三件套 / 不工作了怎么排查 plugin-techdocs 版本 / workaround 直链 link 模板）→ [**references/techdocs-setup.md**](references/techdocs-setup.md)。

---

## §6 API 契约 —— A 类字段（hard rule：trust the code）

**hard rule**：A 类（`API.spec.definition`）能从代码生成的必须生成 + CI drift gate；手写要显式声明「此文件即 SSOT」（4 种 last-resort 例外）。

**详细**（A 类生成器对照表 / CI drift gate 两层 gate 配置 / 手写 OpenAPI 4 种例外 + 文件头警告模板 / Resource source-location 必须指 manifest / 实战样板）→ [**references/api-contract-drift.md**](references/api-contract-drift.md)。

### §6.1 能力命名 / 引用校验（canonical）

**SSOT 是 Backstage catalog**，不是 markdown 清单（旧规则"能力名在 `domain-model.md` 清单里、CI grep 白名单"已废止 —— 两份 SSOT 易漂移）。

1. **`providesApis`/`consumesApis` 引用的 API 名必须能在 catalog 解析到一个 `kind: API` 实体** —— 用 `service-catalog-search` skill 反查（skill 没建好之前直查 catalog REST API：`GET /api/catalog/entities?filter=kind=API,metadata.name=<name>`）。解析不到 = 引用悬空 = CI fail。
2. **新加 API 实体无需预先去清单注册** —— owning repo 加 `kind: API`、push 即生效。
3. **同 MR 内新增的 API 算合法**（避 ingestion 时延误报）：CI 先 `git diff` 抽本 MR 新加的 `kind: API metadata.name` 当 allowlist；只对"既不在 catalog 也不在本 diff"的引用 fail。
4. **防长尾命名重复（同语义、不同名）是 PR review 的责任**（`code-review` skill 已加 check），不是 CI 的责任。
5. **本仓 [`public/dev-standards/architecture/catalog-glossary.html`](../../public/dev-standards/architecture/catalog-glossary.html)**：能力的**语义说明 + Ubiquitous Language**，**不是**命名白名单。新能力上线后 PR 补进 glossary（可选推荐）。

CI 校验范本见 [`references/api-refs-check.md`](references/api-refs-check.md)。核心：对每个 ref，本 MR 新加列表里有 → valid，否则查 catalog REST API 必须命中。放 `lint` stage，`rules: changes: [catalog-info.yaml]` + nightly schedule 兜底。

---

## §7 Component links 速览（website-only）

**规则**：只有 `type: website` 的 Component（admin / 落地页 / 控制台）的 prod/staging 域名加 🌐 `links`；`type: service`（backend 微服务）的 ingress host **不打** 🌐 link（API 入口走 `kind: API` 实体）。

**详细**（提取位置 / engagement-admin 实例 / `scripts/sync-ingress-links.py` 用法 / 反例）→ [**references/website-links.md**](references/website-links.md)。

---

## 示例对比 — §8 反例索引

5 个最常踩的坑（tag 含斜杠 / `links.url` 相对路径 / FastAPI 手写 OpenAPI 漂 / `providesApis` 引用不存在 / backend 微服务打 🌐 link）+ ✅ Good 综合范例 → [**references/anti-patterns.md**](references/anti-patterns.md)。

> 完整 Bad / Good 案例库见 [`references/anti-patterns.md`](references/anti-patterns.md)（含每条反例的 ❌ Bad 写法 + ✅ Good 修正）。下面给一个最常见踩坑的速览，详情进 reference。

### Bad — `metadata.tags` 写成 `layer/base-platform`

```yaml
metadata:
  tags:
    - layer/base-platform   # ❌ tag 不能含 '/'
```

CI policy 直接拒：tag 只能 `[a-z0-9+#-]`。

### Good — `layer-base-platform`

```yaml
metadata:
  tags:
    - layer-base-platform   # ✅ 用连字符
```

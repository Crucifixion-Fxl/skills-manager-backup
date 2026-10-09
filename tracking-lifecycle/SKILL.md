---
name: tracking-lifecycle
description: 当用户需要埋点审计、设计、代码注入、点位管理、本地/沙盒/staging TDD、工单校验或 CI 发布门禁时使用。
---

# tracking-lifecycle

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

本 Skill 管理从业务埋点意图到生产工单发布的证据链。M1 已提供通用的 YAML 契约、Node CLI、项目 hook 接口和 GitLab CI 模板；M2 已提供可选的 PRD 指标契约与 Superset/独立数仓数值一致性门禁。业务仓库负责真实 SDK 触发、构建产物、staging 部署及数仓和 Superset 查询。AI 业务意图审查仍需根据实际 PRD 与 observability design 出具带证据的报告，不能把 M1 技术 PASS 当作业务指标 PASS。

读取顺序：本文件 → [契约与命令](references/tdd-contracts.md) → [指标验收](references/metric-acceptance.md) → 按需阅读 [平台 API](references/api-reference.md)、[SDK](references/sdk-api.md)、[测试指南](references/testing-guide.md)。旧 `scripts/` 保留作兼容辅助，不充当新门禁。

原有能力继续从本 Skill 进入：先按 [平台 API](references/api-reference.md) 递归读取 application 事件树，区分可复用、可扩展、同名冲突和无关点位；再按 [埋点设计约束](references/tracking-schema.md) 和 [通用字段](references/common-event-schema.md) 核对触发条件、触点覆盖、字段及指标用途。代码注入时读取 [Android/iOS/Flutter/后端模板](references/injection-templates) 与 [SDK API](references/sdk-api.md)，L1 mock 和 L2 test sink 参考 [测试指南](references/testing-guide.md)，真实 SDK、contexts/baseSchemas、schema version、sandbox 端点和失败回读参考 [验证策略](references/verification-strategy.md)、[排障](references/troubleshooting.md)。平台查询和点位操作遵守 [平台 API](references/api-reference.md) 的 PAT 权限、敏感字段、已发布不可变和全量覆盖规则；没有权限或写入结果不明时停止并回读。

API 操作优先用已核验的 REST 能力，无需全部通过浏览器。完整路由与请求模型见 [Controller 清单](references/platform-api-catalog.md)，鉴权见 [平台鉴权](references/platform-auth.md)，公共字段注册与独立产物见 [Base/Context API](references/base-context-api.md)。查询用 Project Token，写入用有权限的 PAT；当前源码没有 JWT 登录。新增 reference 不代表 Base/Context 已有专用 CLI 命令。

## Rules

- 先审计平台全量点位与业务触点，再写设计 HTML 和独立 change YAML；设计未审定时不提交平台。
- 本地、sandbox、staging、指标数值与生产发布报告分别记录，不能用较低层 PASS 替代较高层证据。
- 平台共享主表出现未选任务变更、字段冲突或结果不明时停止；不自动覆盖他人点位，也不盲重试发布。
- 生产发布只在业务项目配置的受保护发行分支和受限 CI 身份执行，回读成功后才允许同一 artifact 部署。

## 先定范围

1. 从 PRD / metric spec / observability design 确定事件触发、字段、指标用途，编写供人 review 的 `tracking-design.html`。机器契约写在独立的 `events/changes/<issue>.yaml`，HTML 链接它。设计 review 若尚未通过，先交付文档，不写平台。
2. 明确 `host`、平台 `application`/`applicationId`，以及哪些 issue 属于同一 App 发布包。按本次实际部署配置 `TRACKING_PLATFORM_BASE_URL`（埋点管理 API）与 `TRACKING_SANDBOX_BASE_URL`（Micro good/bad），均为 HTTPS origin；不要从 Superset 地址或区域名推测。每个 host/application 分别维护全量 `baseline.yaml`。事件身份使用 application + type + trackerType + 完整 SPM；`SELF_DEFINE` 还需 eventName。
3. 一个应用的当前平台事件主表是共享的。当前实现按应用最多一个活跃工单处理；不同任务可并行写本地契约，但进入平台前要选定联合发布 issue。未选任务的点位变更不能混入本次工单。不同目标字段冲突先协商，不能自动覆盖。
4. 不从旧 `tracking-design.html` 的内嵌 YAML 自动迁移。新旧格式共存时，独立 `change.yaml` 是 TDD 与发布投影正本；旧 validator 只检查其旧格式覆盖范围。

## 产物

| 文件 | 粒度 | 来源 | 用途 |
|---|---|---|---|
| `events/baseline.yaml` | 已发布应用全量快照 B | `pull-baseline` + prod Iglu 回读 | 所有 issue 的共同 before |
| `events/bootstrap.yaml` | 首次发布前的空快照 B0 | `pull-bootstrap` + 唯一活跃工单回读 | 新应用无已发布版本时的显式 before |
| `events/changes/<issue>.yaml` | 单 issue 字段级 before/after | 设计与本地 TDD | 可 review 的变更意图 |
| `events/selected.yaml` | 本次发行选定 change 路径列表 | 发行负责人 | 阻止未选 issue 混入 |
| `.tracking/platform.lock.yaml` | 活跃工单当前全量快照 L | `post` 或 `readback-platform` | sandbox/staging 锁定版本 |
| `.tracking/release-candidate.yaml` | `apply(B, selected changes)` 全量投影 P | `build-candidate` | 与 lock、代码和发布前平台树比较 |
| `events/scenarios/*.yaml` | 各层预期数量与字段 | 用例作者 | 先写 RED，再触发真实行为 |
| `events/staging-target.yaml` | 本次实际 staging Collector、区域、数仓与物理表 | 环境负责人审核后提交 | L4/M2/发布的共同目标约束 |
| `.tracking/{local,sandbox,staging,acceptance,release}.json` | 分层 PASS 与出处 | Skill 验证器 | GitLab artifacts 与发布门禁 |
| `.tracking/usage.json` | 构建代码使用的事件及 issue 归属 | 项目 hook | 查未选 issue 的代码使用 |

**B、L、P 都是应用全量快照；change 是本次任务增量。** L 是平台当前事实，不是独立任务分支。`baseline.yaml` 和 `change.yaml` 可提交业务仓库；运行报告与候选默认作为 CI artifact。生产成功后，重新拉取已发布 B，不手工把 P 改名为 B。

首次发布例外：当应用从未有状态 3 的工单，且恰有一个活跃工单时，用 `pull-bootstrap` 生成 `kind: bootstrapBaseline`、空 `events`、绑定该工单 ID/version 的 B0。`pull-baseline` 仍必须拒绝这种状态。B0 不是已发布基线，也不验证生产 Iglu；`post`/`readback-platform` 必须证明平台当前**全部**点位恰好属于所选 change 投影，发现未归属点位即停止。首发不调用 `create-workorder`。成功发布后废弃 B0，立即运行 `pull-baseline` 回读真实 B。B0 仅允许空事件；一旦已有任何已发布工单，首发路径永久关闭。

## 安装与命令

在 Skill 目录运行 `npm ci --ignore-scripts` 与 `npm test`。CLI 为 `node cli/bin/events-tdd.js <command> --key=value`。可重复的 `--change=` 可换成 `--change-list=events/selected.yaml`；列表路径相对列表文件。仓库内可运行的无平台示例见 [examples/demo](examples/demo)。

### L1/L2：本机契约 TDD

- 先把本 issue 的触发、边界、计数和字段断言写入 scenario；使业务测试 RED。
- 用生产已发布全量 baseline 与所选 change 做 `check-contract`。运行项目 `usage`、`local`、`artifact` hooks；`local` 必须从实际业务代码的 SDK mock/test sink 路径采集，不以手写 JSON 充当生产证据。`verify-local` 比完整事件身份、次数、字段和值类型，生成绑定 commit 与投影摘要的报告。
- RED 后判断是代码、设计契约还是测试期望错误；修正后重跑。旧测试报告不可复用。

```bash
node cli/bin/events-tdd.js check-contract --baseline=events/baseline.yaml --change-list=events/selected.yaml
node cli/bin/events-tdd.js verify-local --baseline=events/baseline.yaml --change-list=events/selected.yaml --scenario=events/scenarios/local.yaml --capture=.tracking/local-capture.json --commit="$CI_COMMIT_SHA" --out=.tracking/local.json
```

### 平台工单与 L3 sandbox TDD

1. 用只读 prod Iglu URL 和平台最新 RELEASED 工单核对 B；存在活跃工单、当前树漂移或生产 schema 无法回读时停止导入。`TMT_TOKEN` 由应用 owner 的环境变量提供，绝不写入仓库。
2. 本地 PASS 后运行 `create-workorder`，或核对已存在的本次唯一活跃工单。`post` 逐个物理点位取完整详情、组装全量参数、写现有点位 API、每次回读，并生成 L。平台更新会同步 Micro/staging schema；L3/L4 不等于工单发布。
3. 使用真实 SDK 指向 sandbox collector，以本次唯一 namespace 和事件 ID 上报。`verify-sandbox` 要求证据 namespace 与命令请求完全相同，报告也绑定该 namespace；CI 发布只接受本次 project/pipeline 前缀。验证器查 Micro good/bad、Iglu URI、schema version、事件 data 字段及数量；达到查询上限视为不完整，不调用全局 `/micro/reset`。若点位关联 `baseSchemas`，须验证合入事件 data 的公共字段、类型和必填性。若业务 SDK 另外发送独立 Snowplow Context，须单独断言其 URI 和 payload，并回读独立 resolver 文件；Base Schema ID 不代表 Context 证据，当前通用验证器不解析 Context 内容，不能把 L3/L4 PASS 当作这项断言已通过。
4. sandbox RED 且需改契约时先修改 `change.yaml`，重跑 L1/L2，再用 `post --previous-lock=<上一轮L>`；只有上一轮全量 lock 与当前平台树一致且改动仍属于本 issue 的字段时才继续。每次写后重新生成 L、换 namespace，重做 L3。

```bash
node cli/bin/events-tdd.js pull-baseline --host=... --application=... --application-id=... --prod-schema-base=https://READ_ONLY_PROD_IGLU --out=events/baseline.yaml
node cli/bin/events-tdd.js pull-bootstrap --host=... --application=... --application-id=... --out=events/bootstrap.yaml
node cli/bin/events-tdd.js create-workorder --baseline=events/baseline.yaml --name=release-name --version=1-0-7 --prod-schema-base=https://READ_ONLY_PROD_IGLU
node cli/bin/events-tdd.js post --baseline=events/baseline.yaml --change-list=events/selected.yaml --release-id=123 --out=.tracking/platform.lock.yaml
node cli/bin/events-tdd.js verify-sandbox --candidate=.tracking/release-candidate.yaml --lock=.tracking/platform.lock.yaml --scenario=events/scenarios/sandbox.yaml --event-ids=.tracking/sandbox-event-ids.json --namespace=ci-project-pipeline-job --commit=... --out=.tracking/sandbox.json
```

`TRACKING_PLATFORM_BASE_URL` 必须填写本次实际埋点管理 API 的 HTTPS origin；`TRACKING_SANDBOX_BASE_URL` 必须填写当前 Micro 查询服务的 HTTPS origin。`--prod-schema-base` 必须填写真实、只读、HTTPS 的生产 Iglu resolver；三者是不同入口，占位符不能运行。`saveOrUpdateEventInfo` 的 `valueType` 必须使用 JSON Schema 类型名称；数字码回读视为不确定数据并阻断自动 round-trip。平台 API 返回结构尚需在目标环境验证。写入或发布超时后先回读，不盲目重试。

### L4 staging 技术 TDD

先由环境负责人确认实际 staging Collector、区域、数仓身份和物理源表，提交 `events/staging-target.yaml`；区域可以是 `us`、`cn` 或其他实际部署标识，Skill 不预设站点。项目 hook 用同一构建 artifact 部署该 staging 环境，真实业务流程通过 Snowplow SDK 发往配置中的 Collector。项目数仓适配器按唯一 `runId` 查询该环境的事件与 bad，输出查询 ID、发送时间、当前时间、分区水位、版本和行数据。`verify-staging --target=events/staging-target.yaml` 校验目标、物理表、水位和 bad，再断言事件身份、数量、字段；目标文件摘要写入报告并由 `publish` 复核。水位未到是 `WAREHOUSE_DELAY`，不能报告零事件 PASS。点位契约 RED 回到 L1；代码 RED 重部署并用新 runId 重测。工单仍无需发布。生产表或其他区域的表不能冒充 staging 表；若当前没有可审定的 staging 链路，应记录依赖，不能用生产查询代替 L4。

### CI 发布门禁

[GitLab 模板](ci/gitlab/tracking.yml) 依次执行 local → 平台全量 readback → sandbox → staging → 可选 metric → `publish`。业务项目把 `tracking` stage 放在生产部署之前，并让生产部署 job `needs: [tracking:publish]` 且消费同一 artifact。模板只在受保护、非 MR 且等于单个 `TRACKING_RELEASE_REF` 的分支执行后续门禁；每条需要发布的分支须分别配置/包含门禁。只在指定 release 分支的 `tracking-api` 环境注入 protected/masked `TMT_TOKEN`。模板从固定 40 位 `TRACKING_SKILL_REF` 获取 Skill，跨项目 `CI_JOB_TOKEN` 读取须由 GitLab 配置允许。项目提供 `TRACKING_APPLICATION_ID`、`TRACKING_RELEASE_ID`、`TRACKING_VERSION`、`TRACKING_RELEASE_REF`、`TRACKING_PROD_SCHEMA_BASE` 与 hook 命令，见 [接入说明](ci/gitlab/README.md)。

`publish` 重新读取工单和应用全量树，要求 L1/L3/L4 报告与候选 commit/摘要、目标文件匹配、usage 仅含选定 issue、应用**全部点位**通过平台 `getUnpassedEvents` 和 `validate`，再调用现有 `onlineRelease`。回读工单状态 3、当前全量树与生产 Iglu schema 后才给出 PASS。未发布工单可能导致正式 App 上报进入 bad，因此生产部署必须依赖这个 job。

当前平台没有发布时原子的候选摘要 CAS；发布 API 会再次读取共享主表并先推生产 S3。Skill 的写前读、GitLab 同项目串行锁和写后读是可执行防护，**不能证明不存在跨进程竞态或原子回滚**。出现 `RELEASE_UNKNOWN` 或发布后摘要不符，停止 App 部署并人工处置已可能发生的平台副作用。平台源码事实与待讨论改造见 [ADR](../../../docs/observability/design/tracking-lifecycle/adr-contract-gated-ci-publish.md)。

## 项目 hook 边界

`tracking.config.yaml` 中的 `hooks.<phase>.argv` 是命令数组，`outputs` 是项目目录内的非空文件。`run-hook` 不经 shell 执行。Skill 能编排并验证输出，但无法代替业务工程的真实 SDK 测试、App 构建/staging 部署、数仓权限、物理表 SQL 与测试账号。`usage` hook 必须从编译/扫描结果给出事件归属，不能只枚举 YAML。`staging` hook 不得伪造入仓数据。

## M2：PRD 指标验收

在 `events/metrics/<issue>.yaml` 定义 PRD/observability design 的固定修订、决策问题、分子/分母事件、去重粒度、人群、时间窗、Dashboard/Dataset/Chart 修订与预期值。项目 `metric` hook 对同一 staging run 分别执行独立数仓 SQL 和 Superset Chart 查询。`verify-metrics --target=events/staging-target.yaml` 要求 `envFilter` 恰为 `['staging']`、两个查询都只读目标文件允许的相同物理表、分母非零、Chart 与独立 SQL 数值在容差内、场景预期值满足，并输出绑定候选及目标摘要的 `acceptance.json`。将 `TRACKING_M2_ENABLED=true` 配在受保护 release ref 后，`publish` 必须回读这份报告与所选指标契约；未启用时 M1 仍可独立交付。格式与局限见 [指标验收](references/metric-acceptance.md)。

AI 再按 PRD 和 observability design 逐个决策问题审查事件与字段、指标公式、维度、人群、时间窗、Dashboard 能否回答问题，记录 `covered/gap/unknown`、来源版本和证据。数值 PASS 不自动代表语义 covered；缺口径或来源不可读必须输出 unknown，业务验收不能声称通过。若 Superset 共用 Dataset，以 env filter 选择目标环境的物理表；空值、多选、非法 env 或与目标文件不符的源表均失败。

## 验证状态与旧能力

本次 Skill 的 Node 单测和 synthetic demo 只验证逻辑与格式。平台实际 PAT 权限、API 返回、Micro 部署形状、数仓/Superset 实际查询、Naturehood/Lattice 真实 SDK 和 GitLab 发布流水线仍需项目接入后逐层验证。旧 `tracking-spec-validator.js`、`yaml-to-batch-payload.js`、`sandbox-check.js`、`tracker-publish-check.js` 使用旧数据格式或较窄匹配能力，不得作为此方案的 full-tree gate。

# GitLab CI 接入

模板 `tracking.yml` 是通用 job 编排。使用前由业务项目在自己的发行分支提交以下文件与变量；模板本身不会修改业务仓库、埋点平台或 GitLab 项目设置。

## 1. 固定 Skill 版本

将此模板复制到业务仓库，或用 GitLab `include:project` 引用 engineering/skills 的**完整 40 位 commit SHA**。业务变量 `TRACKING_SKILL_REF` 必须是同一个 SHA，使 YAML 与 CLI 同版本。跨项目 checkout 使用 `CI_JOB_TOKEN`，需要 engineering/skills 把业务项目列入 job token 读取 allowlist。Runner 需要 Node 20、git 和对 npm registry 的读取能力。`TMT_TOKEN` 应设为 app owner 的 protected、masked、environment scope 为 `tracking-api` 的变量；只有 readback/publish 两个 job 声明此环境，项目 hook jobs 不应获得发布凭据。该环境可配置为无人工审批。

示意（把两个 SHA 替换为同一已合并 commit；把 stage 放在生产部署前）：

```yaml
include:
  - project: engineering/skills
    ref: '0123456789abcdef0123456789abcdef01234567'
    file: /skills/observability/tracking-lifecycle/ci/gitlab/tracking.yml

stages: [build, test, tracking, deploy]

variables:
  TRACKING_SKILL_REF: '0123456789abcdef0123456789abcdef01234567'
  TRACKING_APPLICATION_ID: '14'
  TRACKING_RELEASE_ID: '123'
  TRACKING_VERSION: '1-0-7'
  TRACKING_RELEASE_REF: 'release/1-0-7'
  TRACKING_M2_ENABLED: 'false' # 有真实 Superset/数仓查询适配器后改为 true
  TRACKING_PLATFORM_BASE_URL: 'https://replace-with-actual-tracking-lifecycle'
  TRACKING_SANDBOX_BASE_URL: 'https://replace-with-actual-micro-service'
  TRACKING_PROD_SCHEMA_BASE: 'https://replace-with-read-only-prod-iglu'

# 现有生产部署 job 必须 needs: [tracking:publish]，且只部署
# tracking:local 阶段生成并沿 artifacts 传递的同一 artifact。
```

三个 endpoint 分别从目标环境的管理 API、Micro 服务和只读生产 Iglu 核对，不能根据 US/CN 名称互相推导；这些 URL 不含凭据。CI 模板不创建平台工单；开发人员在 L1 PASS 后用 CLI `create-workorder`，将真实 `releaseId/version` 配到受保护发行流水线。修改平台点位可在可信开发环境用 `post`；CI 只用 `readback-platform` 检查它是否等于所选 issue 的联合投影。生产工单通过 `tracking:publish` 自动调用现有 API。发行中只有一个活跃工单；同应用其他任务等待本次发布或并入选定包。

新应用首次发布时没有已发布 B：使用 `pull-bootstrap` 取得与现有唯一活跃工单绑定的空 `bootstrapBaseline`，把它作为模板中 `events/baseline.yaml` 的输入文件，不调用 `create-workorder`。先把本次工单全部新点位写成所选 change；已有平台注册点位也必须逐项声明，不能直接把当前平台树复制为已发布 B。`readback-platform` 会拒绝任何未归属点位。发布成功后重新 `pull-baseline` 并替换 bootstrap 文件；后续变更按常规流程。

## 2. 项目目录与 hook

模板约定：

```text
events/
  baseline.yaml
  selected.yaml
  staging-target.yaml                # 本次实际区域/环境的审核目标；L4/M2/publish 必需
  selected-metrics.yaml             # 仅启用 M2 时需要
  changes/<issue>.yaml
  metrics/<issue>.yaml              # 仅启用 M2 时需要
  scenarios/local.yaml
  scenarios/sandbox.yaml
  tracking.config.yaml
.tracking/                       # CI 临时文件，不提交
  usage.json
  local-capture.json
  artifact.bin                   # 实际发布文件，名称可改模板
  sandbox-event-ids.json
  staging-scenario.yaml
  staging-evidence.json
  metric-evidence.json              # M2 项目查询适配器输出
```

`tracking.config.yaml` 的 hook 格式：

```yaml
schemaVersion: 1
hooks:
  usage:
    argv: [node, ci/tracking/usage.js]
    outputs: [.tracking/usage.json]
  local:
    argv: [node, ci/tracking/capture-local.js]
    outputs: [.tracking/local-capture.json]
  artifact:
    argv: [node, ci/tracking/build-artifact.js]
    outputs: [.tracking/artifact.bin]
  sandbox:
    argv: [node, ci/tracking/send-sandbox.js]
    outputs: [.tracking/sandbox-event-ids.json]
  staging:
    argv: [node, ci/tracking/verify-staging-source.js]
    outputs: [.tracking/staging-scenario.yaml, .tracking/staging-evidence.json]
  metric:
    argv: [node, ci/tracking/query-metric.js]
    outputs: [.tracking/metric-evidence.json]
```

Hook 由 Skill 无 shell 执行，非零退出或任一输出缺失/空文件即失败。CLI 拥有契约合成、冲突检查、平台读写、Micro 查询、staging 验证和发布逻辑；项目适配器必须调用业务真实 SDK、构建和数仓，不应以手写测试 JSON 冒充真实证据。`examples/demo/adapter.js` 只是离线格式示例，不是业务验收适配器。

`usage` 应从实际业务代码生成事件列表；`local` 应运行 mock/test sink；`artifact` 应构建与生产部署完全相同的文件；`sandbox` 必须读取 `$TRACKING_NAMESPACE` 并记录 SDK 返回的 event IDs；`staging` 必须部署当前 artifact、生成新 runId、执行真实流程，查询 DWD/bad 并给出水位和物理表。数仓异步延迟应在 hook 内按有界预算等待，不得输出伪造空结果。

`staging-target.yaml` 使用 [L4 契约](../../references/tdd-contracts.md) 中的格式，由环境负责人按本次实际部署区域审核 Collector HTTPS 地址、数仓身份、事件与 bad 的物理表及分区。Skill 不限制 US/CN，也不从域名猜区域；hook 证据中的区域、Collector、数仓和源表须与目标文件一致。生产表不能填作 staging 目标。换区域或换 Collector 时，提交目标文件的变更并重新运行 L4；此前 L4 与 M2 报告摘要不再匹配。没有实际 staging Collector 或数仓表时，保持门禁失败并记录依赖。

启用 M2 时，`metric` hook 用固定版本 PRD/observability design 的指标契约，对本次 run 分别查询独立 staging SQL 和 Superset Chart，报告所有物理源表、env filter、区域、数仓身份与查询 ID；格式见 [指标验收](../../references/metric-acceptance.md)。该 job 使用 `tracking-metric` 环境，可把只读 Superset/数仓凭据仅限定在此环境。`tracking:publish` 在 `TRACKING_M2_ENABLED=true` 时强制复核 acceptance 报告、目标文件和指标契约摘要；没有真实查询适配器时保持 false，M1 仍可独立运行。

## 3. 发布 gate 与失败处理

`tracking:local` 可在 MR 运行，无平台 token。后续 jobs 仅在指定 `TRACKING_RELEASE_REF` 的 protected 非 MR 分支运行，按 `needs` 串联。`tracking:platform-readback` 与 `tracking:publish` 使用同一项目的 `resource_group`，避免本项目两条发行流水线同时操作该应用；跨项目写入、平台 UI 写入没有被 GitLab 锁覆盖，发布 API 自身也没有 CAS。

`tracking:publish` 使用 `TMT_TOKEN` 调用现有 `onlineRelease`，且不能执行项目 hook。结果是 `release.json`；非 PASS 时现有生产部署 job 不应运行。如果 API 结果未知、生产 Iglu 回读不一致或平台发布后 App 部署失败，先检查平台当前状态与 prod schema，处理已发生的副作用，不自动重发另一候选。

在目标 GitLab 实例用 CI Lint 检查 include、stage、job token allowlist 和实际 Runner 配置后再启用保护分支发布。当前模板在本仓库只经过 YAML 解析与 Skill 单测，没有真实 GitLab pipeline、平台 PAT 或 Naturehood staging 验收记录。

# Tracking TDD 契约与命令

所有 YAML 使用 schemaVersion 1。解析拒绝重复键。事件/参数摘要按排序后的规范形式计算 SHA-256，格式、注释和 YAML 键顺序不影响摘要。CLI 报错为 `{code,error}`，非零退出；任何缺失证据均是失败，不自动降级为人工通过。

指定历史快照 `GET /api/release/{releaseId}/snapshot` 使用 90 秒的有界读取预算，覆盖响应头及正文；普通平台 API 仍为 30 秒，不自动重试写操作。2026-10-12 生产实测 356、357 的完整快照分别约 30.7、27.2 秒，25 秒探测会提前断开，30 秒预算也可能误报网络失败。完整内容及生产 Schema 仍须全部校验通过，延长预算不产生 PASS；服务端耗时原因应另取查询/追踪证据，不从客户端超时推断 SQL 故障。

## B：已发布全量 baseline

`baseline.yaml`：`kind: baseline`、`host`、平台 `application`/`applicationId`、`release: {id,version,status: 3}`、`events`。每个 event 用 `type`（PAGE/MODULE/COMPONENT/SELF_DEFINE）、`trackerType`（BASE/CLK/EXP）、完整 `spm`、`point`、`name`、`parameters`。SELF_DEFINE 还需 `eventName`。参数必须有 `name`、`valueType`、`isRequired`；规范类型为 null/boolean/object/array/number/string/integer，必须保留平台类型名称，不猜测数字码，`float` 只在批量接口作为 number 别名。`baseSchemas` 是平台 ID 列表。分类 `category` 是可选字段。对同一物理 MODULE/COMPONENT，平台会生成 CLK/EXP 两个 schema；全量快照都保留。

`pull-baseline` 要求平台没有活跃工单，选最新 RELEASED 工单，读完整递归事件树与每个点位详情，并按版本核对生产 Iglu schema。真实生产 resolver 不可访问时返回 BLOCKED_DEPENDENCY；不能用当前平台树假装已发布 B。baseline 可能含过去为其他任务发布的所有点位，所以是 **application 全量**，不是本 issue diff。

### 历史名称兼容与 Schema 冲突

“历史问题”必须区分已发布基线、操作前全量树与本次所选变更。操作前存在、旧时间戳或手填 `status: 3` 均不能单独证明已发布；基线仍须走上述生产回读，或用 `pull-published-baseline` 核对指定发布的冻结快照及生产 Schema。历史证据缺失时记录待核实，不要求新增点位的人顺带改名。

| 情况 | 校验行为 |
|---|---|
| 已发布基线中的数字开头旧名称，例如 `4g_non_vip_alert`，身份原样继承 | 允许读取、投影和回读；不改名、不重写无关点位 |
| 本次新增数字开头的名称，包括首发和先新增后改身份字段的组合操作 | `INVALID_EVENT_IDENTITY`；操作输入及最终投影的新身份，其 SPM 各段仍须匹配 `[a-z][a-z0-9_]*`，在平台写入前拒绝 |
| 当前树多出基线及所选变更之外的记录，或无关字段被修改 | `UNOWNED_PLATFORM_CHANGE`；不因“操作前存在”继承兼容 |
| 重复事件身份，即使字段完全相同 | `DUPLICATE_EVENT`；不静默去重 |
| 不同身份生成同一 Schema 文件名，例如 PAGE `feed` 和 SELF_DEFINE `feed_pv` | `DUPLICATE_SCHEMA`；无论新旧、字段是否相同均阻断 |

读取层只补数字开头名称的兼容，不接受路径分隔符、遍历、空名称或大写名称。原有参数类型、必填性、已发布身份不可变、工单边界、平台 `getUnpassedEvents` / `validate` 和生产 Schema 回读要求继续保留。`check-contract` 只比较所声明的本地基线与变更，不能代替基线的线上来源核验。

首次发布使用 `pull-bootstrap` 生成 `kind: bootstrapBaseline`、`release: {id: <唯一活跃工单>, version: <该工单版本>, status: 0}` 和空 `events: []`。它只证明“目前无已发布工单且唯一活跃工单身份已回读”，不宣称平台当前树为空、也不宣称生产 Schema 存在。所有当前点位必须在选定 change 中以 `$event` 定义，`post`/`readback-platform` 对平台全树与投影严格比对。发布后重新拉取真正 B；若出现已发布工单、第二个活跃工单或工单版本变化，B0 立即失效。

## Δ：单 issue 的字段级 change

```yaml
schemaVersion: 1
kind: change
issue: "101"
host: demo
applicationId: 1
baseDigest: 520cce2c836cbfa2eb9572e12bde2633cd691bc3792e9247b0798dc0df117254
operations:
  - event: { type: PAGE, trackerType: BASE, spm: [home] }
    field: parameters.source
    before: { exists: false }
    after: { valueType: string, isRequired: false }
```

`event` 指向完整身份，不用裸 point。`field` 可用 `parameters.<name>`、`name`、`description`、`alias`、`category`、`baseSchemas` 等。新增点位使用 `$event`，`before: {exists: false}`，`after` 给完整事件；新增 MODULE/COMPONENT 要将同一物理点位的 CLK 和 EXP 目标都列入投影。已发布事件的 point、type、trackerType、完整 SPM 与 eventName 不可改，已发布参数名/类型不可变。当前点位 API 不表达逐事件 contexts 修改，`contexts` 变更返回 UNSUPPORTED_FIELD。移除元数据字段在 v1 不支持；可改为平台可表达的具体值。

多个 issue 同字段改不同目标值为 CONTRACT_CONFLICT；同目标值可组合，但需明确归属。`before` 必须等于 B；修正本 issue `after` 时继续以 B 的 `before` 为基础，交给 `post --previous-lock` 检查平台上一轮值。不同 issue 同时编辑同一 application 的共享主表，不能通过分别创建工单隔离。

## 选择与投影

`selected.yaml` 只包含本次发行的 change 文件路径，路径相对自身：

```yaml
changes:
  - changes/issue-101.yaml
  - changes/issue-102.yaml
```

已发布 `kind: baseline` 且当前无新变更时，可以显式设置 `changes: []`，用于 `check-contract` 和 `verify-local` 校验已有契约；不会创建新发行。bootstrap 基线、`build-candidate`、`post` 与平台发行流程仍要求非空 change，避免重复发布历史工单。

CLI `check-contract`/`build-candidate` 对 B 应用选定 changes，产出 P 的全量事件树和 selectedIssues。`usage.json`：`{"events":[{"type":"PAGE","trackerType":"BASE","spm":["home"],"issue":"101"}]}`，由项目代码扫描/编译适配器产生；未选 issue 或未定义事件即失败。构建产物从 `--artifact-file` 直接计算 `sha256:` 摘要。合并到 release 分支后，要对所有选定 issue 合成一份 P、重做三层报告，不能拼接各 issue 旧报告。

## L：平台活跃工单全量 lock

`post` 写之前检查活跃工单是所选 ID，平台当前树中每个字段只能为 B 或目标 P；逐物理点位 round-trip 完整参数，写入前再次读取点位详情并与首次全量树比较，写后回读。`platform.lock.yaml` 记录 host/application、releaseId/version、baseDigest、selectedIssues、digest 和 `events`。CI 的 `readback-platform` 是只读同样的全量核对。平台有未选任务字段、未归属新点位或版本漂移，返回错误并停止发布。平台没有原子 CAS，最后一次详情读取后的并发写仍可能发生。

sandbox 修正需 `--previous-lock=<上一轮 lock>`，要求上一轮 lock 属于相同应用/工单/selectedIssues、其摘要正确、当前全量树与之完全一致，且前后变化仍属于 selected field。它不能提供平台原子 CAS；并发写入仍可能发生，写后必须回读。新的 lock 令旧 L3/L4 报告失效。

## Scenario / capture / evidence

Scenario 基础字段：`schemaVersion: 1`、`kind: scenario`、`host`、`applicationId`、`expectations`，host 与 applicationId 必须匹配候选。每条 expectation 需 `event` 完整身份、准确 `count`、可选 `fields` 字段和值。staging 另需唯一 `runId`、正数 `maxLagSeconds`。L1/L2 capture JSON 为 `{ "events": [{"type":...,"trackerType":...,"spm":...,"fields":...}] }`，必须由项目实际业务触发和 SDK mock/test sink 输出。

L3 sandbox hook 运行真实 SDK 并输出本轮 `sandbox-event-ids.json`（JSON 字符串数组）；Micro 查询返回 namespace、queryLimit、good/bad。即使使用 `--evidence` 文件，CLI 也要求其 namespace 与 `--namespace` 完全一致，并把值写入报告 binding；发布 job 只接受当前 `CI_PROJECT_ID`/`CI_PIPELINE_ID` 前缀的报告。验证严格比对 ID 集合，bad 非零或查询达到上限失败，逐事件比版本、完整 Iglu URI、身份、次数及 unstructured event data 字段。唯一 namespace 格式 `ci-<project>-<pipeline>-<job>`；不复用旧 namespace。当前 Micro 归一化器不读取 Snowplow Context；`baseSchemas` 只存平台 ID，因此若有继承的 Base Schema，项目 SDK golden 测试必须另验 Context URI、必填字段及值，不能由通用 L3/L4 PASS 推断其正确。

Micro 的 `app_id` 请求字段使用 Application point（Snowplow `aid` 过滤键），数字 applicationId 仅用于平台工单与契约身份校验。新应用 `device_cloud` 的实测结果为数字 ID 查询 `/micro/good` 返回 404，而 point 查询返回本轮事件。

L4 staging hook 输出两份文件：

先在业务仓库提交经过环境负责人审核的 `events/staging-target.yaml`：

```yaml
schemaVersion: 1
kind: stagingTarget
environment: staging
region: <实际区域标识>
collectorEndpoint: https://<实际 staging Collector>
warehouseId: <实际数仓或连接身份>
allowedSourceTables:
  - <实际 staging 事件物理表>
  - <实际 staging bad 物理表>
```

区域名称不设 US/CN 枚举；Collector 必须为 HTTPS origin（域名及可选端口，不带路径、查询或凭据），物理表必须精确列出，不能用通配符。配置来源应有 Collector 部署、数仓连接、表 DDL/分区和查询权限的可审查证据。若地区或环境不同，分别维护并审核目标文件，发行流水线只选本次实际目标。此文件独立于 hook 输出，防止 hook 自报目标并自行放行。

- `staging-scenario.yaml`：新的 runId、maxLagSeconds、expectations。
- `staging-evidence.json`：`runId`、`region`、`collectorEndpoint`、`warehouseId`、`queryId`、`sourceTables`（与目标文件匹配的实际物理表）、`queryWindow: {from,to}`、`sentAt`、`now`、`watermark`、`rows`、`badRows`。每行带同一 runId、完整身份、schemaVersion、schemaUri 和字段。

水位未覆盖发送时间是 WAREHOUSE_DELAY，超过 scenario 等待预算是 WAREHOUSE_TIMEOUT。查询范围不覆盖测试运行、区域/Collector/数仓/物理表与审核目标不符、无来源表、bad 非零、字段或版本不符都失败。目标摘要写进 L4 报告，发布时按同一文件复核。当前通用验证器核对技术事件及适配器报告的来源；适配器仍须真实执行查询并保存查询 ID/SQL，通用验证器无法独立证明报告未造假。M2 的指标数值与 Superset env 路由有单独的可选验证器，AI 语义审查按 [指标验收](metric-acceptance.md) 出具独立报告。

## Report / release

`verify-*` 自动从候选/lock/commit 计算报告 binding，不接受项目自填 binding JSON。release job 要求 local/sandbox/staging 都 PASS，commit 与 P 摘要一致，L3/L4 lock digest、工单 ID/version 一致，L4 artifact digest 一致。`publish` 只在受保护、非 MR GitLab job 运行，还会从实际 artifact 文件重算 digest。发布前必须满足平台全应用 `getUnpassedEvents` 和 `validate`。调用 `onlineRelease` 之后读回指定工单状态 3、应用全量树与生产 Iglu schema。超时或未知结果不能盲重试。

平台现有发布 API 没有候选摘要 CAS，也不提供可靠的已发布全量参数历史 API。当前回读以平台当前树与生产 Iglu 为主，存在并发与历史一致性限制；若需真正原子隔离，另立平台改造，不把此 CLI 验证报告描述为平台原子保证。

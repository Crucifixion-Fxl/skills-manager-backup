---
name: device-cloud-test-management
description: 通过飞书/Casdoor SSO 在 Device Cloud 上预检、执行和诊断任意业务测试仓库中的 Behave 自动化用例，支持云端 Host 或本地 device-cloud-client、App 可选、手机及通用硬件资源。用户要查询 UID、按模块批量执行、创建/停止 TestPlan/Job 或诊断失败时使用。
---

# Device Cloud 测试管理

## 描述

负责 App、固件/嵌入式和硬件自动化用例的查询、执行、任务管理与日志诊断。

## 边界

本 Skill 只在执行测试用例时申请资源，不提供脱离用例的独立 Client/资源会话。测试代码来源、运行位置和 App 制品彼此独立：

- 测试仓库可以是任意可读 Git 仓库，也可以先从本地 checkout 查询。
- 云端 `run` 由 Host 执行；`run-local` 由本地 `device-cloud-client` 执行并使用云端资源。
- App 下载链接是可选运行制品，不决定自动化类型、测试仓库或资源类型。
- 无 App、无手机、无相机的固件/硬件用例是合法输入。
- 本地 `run-local` 执行时，业务测试仓不得提供 `features/environment.py`；Behave 生命周期和资源绑定由 Client 托管。

统一入口：

```text
skills/quality/device-cloud-test-management/scripts/device_cloud.py
```

## 完整工作流与职责

1. 先明确自动化类型、执行位置、测试仓库和用例范围，再收集所需固件及 App 输入。
2. 查询 Feature/Scenario，核对 UID、资源声明和来源提交；执行入口校验固件后通过 SSO 查询实时资源容量，形成完整预检。
3. 展示用例、资源、并发、版本及来源，取得当前预检的确认 token 后才创建 TestPlan 和 Job。
4. Skill 将两种固件分别写入 Job 的 `metadata.env`，同时记录版本和来源；它们不是 TestPlan 创建请求的顶层字段，不要求用户手改配置文件。
5. 云端由执行平台交付参数给 Client；本地由 Skill 创建唯一受管 Job、生成包含资源和 Plan/Job 身份的临时执行配置，并将版本传入 Client 子进程环境。
6. Client 把环境参数解析为资源配置，业务 Steps 再读取配置执行 Background 和 Scenario。参数传出、配置可读取和实际使用版本是三项不同证据。
7. 运行结束或异常后核验 Job 终态和资源释放；失败时继续使用诊断入口获取报告、时间线、日志及附件。

Skill 负责取参、预检、建单传参和任务闭环；Client 负责配置解析与用例执行。仅更新 Skill 即可创建携带版本的 Job，但执行端读取配测版本还需要包含对应配置支持的 Client；OTA 步骤是否使用配测版本必须另行核实。

## 规则

开始前需要确定：

1. 自动化类型：App、固件/嵌入式或硬件/厂测。
2. 执行位置：云端 Host 或本地 Client。
3. 测试仓库：Git URL，或本地仓库目录。查询本地目录可无 `origin`；执行本地 checkout 时必须有可访问的 `origin`，显式 `--tests-repo` 须指向同一仓库，不能替代它。
4. 用例范围：优先 UID，也可用模块、二级模块、标签或关键词。
5. 用例资源：来自 Feature/Scenario 中的显式资源表，或用户明确给出的 `--resource-json`。
6. 固件/嵌入式的待测版本：必填固件版本，必须是可输出调试日志的 `-d` 版本；配测版本可选，默认等于待测版本；显式提供时同样要求 `-d`。
7. App URL：仅当用例确实需要安装/使用 App 时提供。App 版本、包名和业务环境均从用户下载链接解析，不另行索要。

自动化类型由用户明确意图、测试仓库或经核实的 UID 确定，并记录判断来源。只有 App URL 或模糊任务、仍无法判定类型时，先询问是 App、固件/嵌入式还是硬件自动化；已有依据时不重复询问。不得因为链接是 Kiwibit/VicoHome 就擅自选择 `kb-tests`。

待测版本用于 Background 默认固件升级；配测版本用于 OTA 等需要另一份固件的步骤，两者都是固件。设备当前实际固件由运行时读取，不向用户索要第三个固件版本。App URL 解析器通过集中身份表支持 Kiwibit、VicoHome、VicoNature、Guard Android APK，兼容品牌目录与文件名两种形式；目录与文件名中的显式品牌/平台声明必须全部一致，暂未适配品牌也参与冲突检测；未知或混合品牌/平台、非 APK、版本缺失/不唯一及未知业务环境应明确拒绝，不默认套用 Kiwibit 参数。文件名或目录没有业务环境时拒绝解析，不能猜为 prod。根据使用方反馈，Safemo、Golf 的 staging 包下载 URL 存在缺少 staging 标识的情况，无法仅凭 URL 安全识别环境，本轮暂不适配这两个 App。iOS IPA 与 Android AAB 也暂不适配。App 身份不改变测试仓库，账号地区仍由资源或 `--country` 明确提供。

不要向用户索要 GitLab Token、Device Cloud API Key、Host/设备 ID 或业务运行密钥。SSO 只处理 Device Cloud 身份；查询私有测试仓仍需用户本机已有 Git 凭据。云端 Host 使用平台托管凭据拉取仓库。

## 测试仓库契约

详见 [测试仓库契约](references/test-repository-contract.md)。关键规则：

- 仓库根目录包含 `features/**/*.feature`。
- 每个可执行 Scenario 必须有自己的唯一 `@uid=<value>`。
- 模块使用有效标签 `@testCase_modules=<module>`，二级模块使用 `@testCase_submodule=<submodule>`；标签可位于 Feature、Rule 或 Scenario。
- 资源表至少包含逻辑名和类型，例如 `| robot | ROBOT_ARM |`。资源类型不限制为手机/相机。
- 云端执行必须使用已推送且可从远端获取的不可变提交；本地目录、远端仓库、提交 SHA 和目录摘要都进入预检。

固件/嵌入式任务未指定测试仓库时，交互明确选用 `https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git`；用户明确指定其他仓库时尊重该来源。旧 CLI 的 `DEVT/kb-tests` 回退只用于兼容，新交互必须明确展示最终仓库、模块和自动化类型，不因 App URL 改选仓库。

`--tests-ref` 的公开输入是远端分支或轻量标签名，默认 `main`。执行预检解析并绑定该来源的提交 SHA，再将 SHA 交给 Host；当前不支持直接输入裸 SHA 或附注标签。

## 查询用例

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --tests-repo 'https://gitlab.addx.ai/DEVT/test-suite/hardware-tests.git' \
  --module 'factory'
```

已有本地 checkout：

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --cases-root '<LOCAL_TEST_REPO>' \
  --module 'factory'
```

必须展示仓库、提交/分支、匹配数量以及每条 Scenario 的 UID、名称、文件和资源。如果用户指定模块，自动枚举该模块下的全部 Scenario UID：

- 只有 1 条时可收窄为该 UID。
- 多条时先完整展示 UID 清单；只有用户明确同意批量执行才添加 `--allow-multiple`。
- 任一 Scenario 缺少 UID，或 UID 在选择范围内重复，停止批量执行。
- `--submodule` 必须和 `--module` 同时使用。

## 资源预检

优先使用用例中的显式资源表。支持平台已注册的任意资源类型，例如 `PHONE`、`BATTERYCAM`、`PLUGINCAM`、`ROBOT_ARM`、`TEMPERATURE_CONTROLLER`、`FIXTURE`、`SMART_PDU` 等。不得强制添加 `PHONE`，也不得从 App 链接或空闲池猜测业务资源。

无 App 用例没有显式资源表时，要求用户确认资源 JSON；禁止默认申请手机：

```json
[
  {"name": "robot", "type": "ROBOT_ARM", "conditions": {}},
  {"name": "chamber", "type": "TEMPERATURE_CONTROLLER", "conditions": {}}
]
```

创建前展示每个 Job 的逻辑资源组、按类型/型号汇总的实时空闲容量、计划并发，以及将创建的 Plan/Job/Scenario 数。不要展示设备 ID、序列号、Host、IP、端口或连接信息。

### 嵌入式任务：建单前同机柜优先

仅对 `automationType=embedded` 且至少有两个资源别名的 Job，统一入口使用实时空闲快照检查全部资源需求：有同柜完整可行组合时，增加覆盖全部别名的原生同柜 AFFINITY；没有时，保留原有硬约束并允许跨柜。不为 App、普通硬件或单资源任务新增默认偏好。不能只找到 phone/camera 配对就忽略其他资源；相关拓扑不完整且无法证明存在完整同柜组合时停止，不把未知当作无同柜资源。

预检的每个 `resourceGroups[].placement` 展示策略、模式和原因；模式为 `same_cabinet` 或 `cross_cabinet_allowed`。模式及最终约束进入 token，执行前重新查询；同柜模式变为跨柜模式或反向变化时必须重新确认。Feature/用户显式硬约束始终保留，不能因默认偏好而降级；多资源别名目前均要求 `count=1`。

这是 Skill 建单前选择，不是 Server 动态软亲和调度，也不是资源预留。建单后资源被占用时不自动删除约束、放宽要求或重建任务；依照原有终态/释放流程处理后重新预检。新增同柜约束沿用原生持久化、实际绑定回读与异常清理能力；带约束的 `run-local` 仍按既有能力边界在建单前拒绝。

### 同 Host / 同机柜亲和性

用户要求资源同 Host 或同机柜时，必须使用统一入口的 `--resource-constraints` JSON 数组。仅支持 `AFFINITY`，拓扑键为 `belong_to_host` / `belong_to_cabinet`；`resources` 是同一 Job 中至少两个不同的逻辑资源名。需要同时满足两者时传两条约束，不能把“同机柜”当作“同 Host”。

例如 phone 与 camera 同 Host、同机柜：

```json
[
  {"type":"AFFINITY","topologyKey":"belong_to_host","resources":["phone","camera"]},
  {"type":"AFFINITY","topologyKey":"belong_to_cabinet","resources":["phone","camera"]}
]
```

将该数组作为 `--resource-constraints` 的值，并保留原用例、固件、App、资源和并发参数先执行 `--dry-run`。Feature 顶部已有的 `# @firmware.affinity=belong_to_cabinet` 也会生成 phone/camera 约束；CLI 约束与其合并，不覆盖或削弱声明。

入口在内部查询资源所属 Host/机柜，验证整组资源及并发；拓扑缺失、资源列表截断或没有完整同域组合时不创建任务。预检逐 Job 展示约束，约束进入确认 token，变化必须重新确认。有亲和约束的 Job 中每个资源别名仅支持 `count=1`，其他 count 明确拒绝，不静默降级。

亲和性执行当前仅支持云端 `run`。带 Feature/CLI 亲和约束的 `run-local` 在建单前拒绝；不能在 legacy Client 建连之前等待资源分配，否则形成循环等待。无约束本地执行保持原流程。

正式建单将约束写到 `CreateJobInput.constraints`，并读回 Job 确认持久化；创建后及运行轮询中核验实际绑定的逻辑名和拓扑。约束丢失、跨域、缺拓扑或确认仍运行但绑定不全时，进入取消、终态与资源释放闭环；状态与绑定分次读取产生冲突时先复核终态。未观测到完整正确分配不能报告通过。带约束的内部 `--no-wait` 同样在建单前拒绝。云端结果仅输出每 Job 的 `affinityVerification` 验证状态，不输出实际拓扑 ID。

此能力不等于已完成生产部署验收，也不修复 Server 的独立 `$in` 查询问题；离线自测不能描述为实际云测通过。禁止绕过统一入口或靠反复建单碰运气。

## 云端执行

先只生成预检：

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py run \
  --automation-type embedded \
  --tests-repo 'https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git' \
  --tests-ref '<REMOTE_BRANCH>' \
  --uid '<UID>' \
  --firmware-version '1.19.35-d' \
  --dry-run
```

示例省略配测版本，最终配测固件为 `1.19.35-d`；用例需要另一版本时添加 `--companion-firmware-version '<FIRMWARE_VERSION>'`。需要 App 时添加 `--app-url '<APP_URL>'`；链接只解析 App 运行参数，不改变测试来源或两种固件版本。

当前入口将待测版本传为 `DEVIUM_FIRMWARE_VERSION`，由 Client 读取为 `firmware_version`。配测版本传为 `DEVIUM_COMPANION_FIRMWARE_VERSION`，配套 Client 配置读取支持将其解析为 `companion_firmware_version`，未提供时使用有效待测版本；旧 Client 缺少这一字段和映射，不能仅凭 Job 已带参数就声称配置已生效。Background 升级还依赖 App 产品、环境、国家和手机，缺项时当前 Client 会跳过或中止；提供固件参数不等于已刷入。`companionVersionConsumer: null` 表示当前尚无业务 OTA Step 消费配测字段，配置解析支持也不代表 OTA 已实现。如配测步骤尚无匹配实现，停止并报告能力缺口，不随机替换版本或宣称 OTA 已执行。详细字段见 [CLI 契约](references/cli-contract.md)。

向用户展示完整预检，包含自动化类型及来源、待测/配测固件及来源、`planName` 和 `confirmation.token`。只有用户明确确认该用例、固件、资源和并发快照后，才保留全部预检参数，并用原样的计划名和 token 创建：

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py run \
  --automation-type embedded \
  --tests-repo 'https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git' \
  --tests-ref '<REMOTE_BRANCH>' \
  --uid '<UID>' \
  --firmware-version '1.19.35-d' \
  --plan-name '<PREFLIGHT_PLAN_NAME>' \
  --confirm '<PREFLIGHT_SHA256>'
```

真实执行前会重新预检；自动化类型、两种固件及来源、仓库提交、用例内容、资源需求与约束、有效并发、国家、优先级或超时变化都会使确认失效。`capacitySnapshot` 仅用于实时容量展示，不把全局空闲数量绑定到 token：无关资源变化，或容量数量变化但仍满足原需求、约束及有效并发时，无需重复确认。容量不足、缺失拓扑或无法满足完整亲和组合时仍在建单前拒绝，不因 token 有效而放行；有效并发变化必须重新确认。这不是资源预留，也不固定某台物理设备。统一入口禁止 `--no-wait`，因为返回结果前必须完成 Job 终态和资源释放核验。

验证 Client 分支镜像时，给 `run` 追加 `--client-image '<TAG>'`（如 `test-<短SHA>`）。它写入 Job `metadata.image`，预检显示 `clientImage` 并参与确认 token，不传则使用 Host 默认镜像；只接受标签，`run-local` 不支持。详见 [CLI 契约](references/cli-contract.md)。

## 本地 Client 执行

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py run-local \
  --automation-type embedded \
  --cases-root '<LOCAL_FIRMWARE_TEST_REPO>' \
  --tests-repo 'https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git' \
  --uid '<UID>' \
  --firmware-version '1.19.35-d' \
  --client-root '<DEVICE_CLOUD_CLIENT_ROOT>' \
  --dry-run
```

本地与云端使用同一套自动化类型、固件、仓库、UID 和资源预检；App 和配测版本仍按用例需要添加。`run-local` 只能绑定一个 Job；先由 Skill 创建唯一的受管 Plan/Job，再让本地 Client 绑定该 Job。禁止直接运行 `device-cloud-client/run_tests.py`。Client 版本必须包含 Client 自有 Behave runner/hooks 和 managed-local 协议；业务仓存在 `features/environment.py` 时立即停止。

`trigger_cloud_job.py` 是内部兼容助手，不是用户执行入口。`run-local` 内部用其 `--no-wait` 创建唯一 Plan/Job 后，才由统一入口启动 Client 并负责终态及释放；这只是生命周期交接，不是测试已完成。不得直接调用底层脚本绕过统一入口。

## 环境

- Device Cloud 默认 `prod-cn`。只有用户明确要求 Host2、staging 或迁移验收时才使用 `staging-cn`。
- App 包中的 `prod/staging/pre` 是 App 服务环境，与 Device Cloud 环境正交；旧 `_test_` 标记和其他未知环境不受支持。
- 不接受、不读取、不输出业务运行密钥；凭据由 Server/Host/Vault 托管。

## 停止与诊断

```bash
python skills/quality/device-cloud-test-management/scripts/device_cloud.py cancel job --job-id <JOB_ID>
python skills/quality/device-cloud-test-management/scripts/device_cloud.py cancel plan --plan-id <PLAN_ID>

python skills/quality/device-cloud-test-management/scripts/device_cloud.py diagnose \
  full --job-id <JOB_ID> --output-dir '<OUTPUT_DIR>'
```

### 强制终态与资源释放闭环

`run` 和 `run-local` 在取得 Plan/Job 标识后的所有退出路径都必须执行清理，包括成功、用例失败、排队/执行超时、初始化异常、Client/Host 中断和用户中断：

1. 正常结束时，等待并确认每个 Job 已进入终态。
2. 非正常结束立即执行 fallback cancel；正常路径未在清理时限内进入终态时也 fallback cancel。
3. Job 终态后查询手机和通用设备连接，确认没有资源仍绑定该 Job。
4. 只有“Job 已终态且资源绑定数为 0”才算清理成功；不得因 Client 零退出码或测试本身通过而跳过核验。
5. 清理失败必须让本次执行返回失败，并在 `[RESULT_JSON]` 的 `cleanup` 中保留 Plan ID、Job ID、最终状态、是否请求取消、终态/释放核验结果和错误原因，供后续人工清理。

本地建单助手已输出有效 Plan/Job 标识后再非零退出时，入口保留标识并清理，不启动 Client；即使清理成功，本次执行仍失败。若未获得可验证的标识，明确报告无法核验释放，不按可能重复的计划名猜测并取消任务。

手动停止后同样确认全部目标 Job 已到终态并释放资源。失败诊断必须依次完成报告、首个失败步骤、时间线、失败窗口日志、证据下载与分类；不得只根据页面错误文本下结论。低层命令见 [CLI 契约](references/cli-contract.md)。

## 只读登录与资源验收

优先保留 owner CLI 的同进程 session、Token 和系统 keyring 流程。网页登录辅助验收时，前端身份入口为 `/api/auth/userinfo`，直接 Server 的入口为 `/auth/userinfo`；正常 OAuth Bearer 经服务端签名、issuer、audience、expiry 校验后返回身份。该登录生命周期可能同步本地用户目录、角色初始化标记和 `lastLoginAt`，即使已有用户也可能更新；明确记录这些认证副作用，不能把身份 GET 描述为纯 SELECT，也不能将它们与资源业务写入混为一谈。先核对服务端返回的预期邮箱、姓名和访问级别，再查询业务资源；本地解码 JWT 不能替代服务端身份核验。

前端资源 GraphQL 使用配置的同域 `/graphql`，不要从 `/api/auth/userinfo` 推导出 `/api/graphql`。只读元数据候选为固定 query `deviceConnection(first: 10)`，仅选择 `edges { node { resourceType deviceModel online } }`、`totalCount` 和 `pageInfo { hasNextPage }`，按类型、型号、在线状态分组计数；不得顺带读取设备标识、连接信息、账号、任务内容或触发 mutation。执行前仍须追踪 resolver、主查询、自动辅助查询和懒初始化的完整副作用链，匹配目标部署契约。请求携带 Bearer、身份端点成功或 GraphQL 返回数据，都不能证明资源 resolver 将请求绑定到该身份；保护和资源权限未被证实时明确标记“授权未覆盖”，不报告完整授权 PASS。

与网页对照时，分别核对原始类型枚举与 UI 中文显示映射、同筛选和同页的分组数量、当前返回行数及页面总数；返回数量不等于全量，动态快照也不证明跨页实体一致。正常 SignOut 可能跳到 Casdoor `/api/logout` 并返回 `status: ok`；精确记录实际退出响应、页面状态、临时凭据与文件清理结果，不臆测已返回登录页，也不将退出响应当作所有会话已撤销的证明。

## 终态输出

无论成功或失败，都明确输出：Device Cloud 环境、自动化类型及来源、测试仓库及解析提交、待测/配测固件及来源、App（有则输出，无则标明未提供）、Plan ID、Job ID、Scenario/UID、资源组、最终状态、首个失败原因和分类、终态与资源释放核验结果、附件绝对路径。结构化结果以 `[RESULT_JSON]` 开头；非零退出码或 `cleanup.verified=false` 必须如实视为失败。

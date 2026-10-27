# CLI 契约

## 统一入口

```text
python skills/quality/device-cloud-test-management/scripts/device_cloud.py <command>
```

Windows 使用 Credential Manager；macOS/Linux 需安装 Python `keyring` 并配置系统安全存储。缺少安全后端时认证失败，不降级保存明文 refresh token。

## 测试代码来源参数

查询和执行命令共同支持：

```text
[--tests-repo GIT_URL]
[--tests-ref REMOTE_BRANCH_OR_LIGHTWEIGHT_TAG]
[--tests-module MODULE]
[--cases-root LOCAL_PATH]
[--refresh-cases]
```

`--tests-repo` 是云端 Host 下载地址，`--cases-root` 是查询/预检/本地执行读取目录。未给 `--tests-module` 时从仓库名推导。查询可以直接读取没有 `origin` 的本地目录；`run` / `run-local` 使用本地 checkout 时，必须配置可访问的 Git `origin`，预检通过它核验所选远端 ref。显式 `--tests-repo` 必须与该 `origin` 指向同一仓库，不能替代 `origin`。

`--tests-ref` 当前支持远端分支或轻量标签名，默认 `main`，不支持裸提交 SHA 或附注标签。预检把请求的命名来源解析为 `resolvedCommit` 并绑定确认；实际交给 Host 的是该不可变 SHA。查询显式本地 checkout 时读取该目录现有文件，不会因该参数切换本地分支。

固件/嵌入式交互未指定其他来源时，明确传入 `https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git`。CLI 的 `--automation-type embedded` 本身不修改来源；未提供来源的旧调用仍兼容回退 `DEVT/kb-tests`，新交互不得依赖此回退。

## 查询用例

```text
device_cloud.py search-cases
  [--uid UID | --module NAME | --tags EXPR]
  [--submodule NAME]
  [--text TEXT]
  [测试代码来源参数]
  [--limit N]
```

`--submodule` 必须与 `--module` 一起使用。模块标签按 Feature/Rule/Scenario 继承，执行 UID 只取 Scenario 自身标签。输出包含匹配总数、UID、文件、行号和逻辑资源。

## 自动化类型、固件与 App 输入

### Android APK 身份解析

`inspect-app --app-url URL` 可离线检查解析结果，不认证、不建单或安装 App；
`run` / `run-local` 的 `--app-url` 复用同一解析器。

| 品牌标识（目录或文件名，不区分大小写） | App 名称 | Android 包名 |
| --- | --- | --- |
| `kiwibit_android` / `kiwibit/android` | `BAPP` | `com.kb.kiwibit` |
| `vicohome_android` / `vicohome/android` | `VicoHome` | `com.smartaddx.vicohome` |
| `viconature_android` / `viconature/android` | `VicoNature` | `com.smartaddx.vicohome.nature` |
| `guard_android` / `guard/android` | `QuanCheng` | `com.ai.addx.guard` |

映射集中在 `scripts/device_cloud.py::ANDROID_APP_IDENTITIES`，扩展品牌须先核对 Client
运行名及实际包名；未知品牌不得拼接包名或回退为 Kiwibit。本轮仅支持 Android APK，
iOS IPA 和 Android AAB 明确拒绝；不代表其他 App 的 UI Steps 或 iOS 调度已适配。

支持 `prod`、`staging`、`pre` 业务环境，解析版本、可选构建号、提交摘要和构建时间。
只从 URL 路径解析，不把查询参数当成品牌标识；未知/混合品牌、非 APK、版本缺失或
不唯一、未知环境会报错。目录与文件名中的所有显式品牌/平台声明（包括暂未适配品牌）
必须一致，同一文件名内的冲突也会拒绝；不能用已知品牌目录掩盖另一品牌文件名。
解析 URL 不代表已校验 APK 二进制或完成安装。

文件名或目录未携带业务环境时拒绝解析，不猜为 `prod`。
根据使用方反馈，Safemo、Golf 的 staging 包下载 URL 存在不含 `staging`
环境标识的情况，无法仅凭 URL 安全区分业务环境，因此本轮暂不适配这两个 App。

例如 `vicohome_android_4.7.0-290805_staging_d00ac3e_20261020-175915.apk`
解析为 VicoHome / Android / 4.7.0 / staging；US 或 EU 仍由资源/`--country` 指定。
输出中的 `app_type` 是历史兼容标签（Kiwibit 为 `kb-tests`，VicoHome 为 `vh-tests`），
不是实际执行来源：执行仓库及模块仍由 `--tests-repo` / `--tests-module` 决定。
VicoNature 为 `vn-tests`；Guard 使用品牌标签，不虚构测试仓库名。

### 执行参数

`run` 和 `run-local` 共同支持：

```text
[--automation-type app|embedded|hardware]
[--firmware-version TESTED_FIRMWARE_VERSION]
[--companion-firmware-version COMPANION_FIRMWARE_VERSION]
[--app-url URL]
```

- `--automation-type`：以用户明确类型、仓库或经核实的 UID 为依据。CLI 可从仓库名 `firmware-tests` 或 `--tests-module firmware-tests` 推断 `embedded`；与该来源冲突的显式 `app`/`hardware` 会失败。其他仓库可显式指定 `embedded`。只有 URL 或模糊任务且类型仍不明时先询问，App URL 不参与类型或测试仓选择。
- `--firmware-version`：待测固件，`embedded` 必填，供 Background 默认升级使用。显式固件版本均须无空白/控制字符并以 `-d` 结尾；版本格式不限定为 `x.y.z`。
- `--companion-firmware-version`：配测固件，可选，用于 OTA 等需另一份固件的步骤；省略时等于待测版本，显式提供时同样要求 `-d`。两者均不是 App 版本。设备当前实际固件由运行时读取，不索要第三个版本。
- `--app-url`：仅在用例需要 App 时提供。App 版本、包名和业务环境从用户下载链接解析，不另行索要；当前解析器支持可识别的 Kiwibit、VicoHome、VicoNature、Guard Android APK（身份表及拒绝规则见上文）。无法解析时报告限制，不猜测字段。

预检记录类型及固件字段；已确定类型或提供固件参数时，Job metadata 同步相同记录。例如从仓库推断类型、显式提供待测版本并默认配测版本时：

```json
{
  "automationType": "embedded",
  "automationTypeSource": "repository",
  "firmware": {
    "testedVersion": "1.19.35-d",
    "testedVersionSource": "explicit",
    "companionVersion": "1.19.35-d",
    "companionVersionSource": "tested-version",
    "companionVersionConsumer": null
  }
}
```

`automationTypeSource` 为 `explicit`、`repository`、`module` 或旧调用的 `unspecified`；未确定类型为 `null`。`testedVersionSource` 为 `explicit`/`not-provided`，`companionVersionSource` 为 `explicit`/`tested-version`/`not-provided`。这些字段和最终版本均进入不可变确认，变化后必须重新预检。

两种固件使用同一条参数链：Skill 取参及默认值处理 → 预检和确认 → Job `metadata.env` → Client 配置读取 → 业务 Steps。TestPlan 创建请求只承载名称、类型、发起人和状态；固件值随其下的 Job 传递，不要求用户另填配置文件。

| 含义 | 公开参数 | Job 环境变量 | Client 字段 | YAML 字段 |
|---|---|---|---|---|
| 待测固件 | `--firmware-version` | `DEVIUM_FIRMWARE_VERSION` | `firmware_version` | `firmwareVersion` |
| 配测固件 | `--companion-firmware-version` | `DEVIUM_COMPANION_FIRMWARE_VERSION` | `companion_firmware_version` | `companionFirmwareVersion` |

配测字段属于配套 Client 配置读取支持：环境变量优先于 YAML；配测未提供时使用有效待测值，资源合并后仍须保留。旧 Client 未包含这一字段与映射时，Job 仍能携带参数，但执行配置不会读取配测值；不得把传参成功当成执行配置已支持。本地跨仓契约测试须用 `DEVICE_CLOUD_CLIENT_ROOT` 指向包含该支持的真实 Client checkout；CI 未提供 checkout 时会明确跳过相应测试，不能声称已完成该层验证。

当前业务消费边界：Background 使用待测字段，升级还要求 `app_name`、`env`、`country` 与手机，缺项会跳过或中止。`companionVersionConsumer` 固定为 `null`，指业务 OTA Step 尚未使用配测字段，而不是用它判断 Client 是否支持配置解析。本入口未实现刷机或缺失的 OTA Steps。若用例的配测 OTA 步骤尚未实现，停止并报告能力缺口，不随机回退到其他版本，也不声称升级成功。

## 云端 Host 执行

```text
device_cloud.py run
  [--automation-type app|embedded|hardware]
  [--firmware-version TESTED_FIRMWARE_VERSION]
  [--companion-firmware-version COMPANION_FIRMWARE_VERSION]
  [--app-url URL]
  (--uid UID | --module NAME | --tags EXPR | --feature-id ID)
  [--submodule NAME]
  [测试代码来源参数]
  [--device-cloud-env prod-cn|staging-cn]
  [--resource-profile auto|phone|phone+battery-camera|phone+plugin-camera]
  [--resource-json JSON]
  [--resource-constraints JSON]
  [--allow-multiple]
  [--country US]
  [--plan-name NAME]
  [--priority N]
  [--poll-interval SECONDS]
  [--timeout SECONDS]
  [--queue-timeout SECONDS]
  [--cleanup-timeout SECONDS]
  [--client-image TAG]
  [--max-concurrency N]
  [--dry-run]
  [--confirm PREFLIGHT_SHA256]
```

`--app-url` 缺省时不注入 App 运行参数；固件参数独立传输。App 不修改测试仓库、模块或固件版本。

`auto` 优先读取用例的显式资源表，接受任意合法资源类型。仅旧 App 用例没有资源表时保留手机/相机推导。无 App 且没有显式资源表时必须提供 `--resource-json`，不会默认添加 PHONE。实时容量查询保留所有 Device Cloud 资源类型。

模块或标签匹配多条时，只有显式 `--allow-multiple` 才执行；按 Feature 拆分 Job，并把该 Feature 的 Scenario UID 组合为精确标签表达式。任一 UID 缺失或重复即失败。统一入口不接受 `--no-wait`，避免在 Job 仍占用资源时提前返回。

`--dry-run` 输出完整预检和 `confirmation.token`，不创建计划。真实创建必须原样携带预检生成的 `--plan-name` 和 `--confirm`。自动化类型及来源、两种固件及来源、仓库提交、目录摘要、选择器、资源需求与约束、有效并发或其他执行参数变化都会使 token 失效。`execution.capacitySnapshot` 保留在预检输出中供实时展示，但其绝对数量不参与 token；无关资源或数量变化只要仍满足原资源需求、约束及有效并发，不要求重复确认。执行前仍重新验证整组实时容量与亲和性；容量不足或拓扑不满足时禁止建单，有效并发变化时必须重新确认。确认不等于预留资源或锁定物理设备。

`--client-image TAG` 指定本次 Job 使用的 Client 镜像标签（例如 device-cloud-client 分支镜像 `test-<短SHA>`），写入 Job `metadata.image`，与固件 metadata 合并而不覆盖。只接受标签（`[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`，即 Docker tag 语法），不接受镜像地址；不合法在建单前失败。预检输出 `clientImage`，且该值参与确认 token，更换镜像必须重新确认。不传时不写 `metadata.image`，Host 使用默认镜像，预检和 token 与此前一致。仅 `run` 支持，`run-local` 不接受。

### 嵌入式建单前机柜选择

`automationType=embedded` 且一个 Job 至少两个资源别名时，按当前完整空闲快照检查整个资源组：存在同柜完整组合时加入覆盖全部别名的同柜 AFFINITY；不存在时保留所有原有硬约束，允许跨柜。App、普通硬件及单资源任务不新增默认偏好。目前多资源嵌入式任务每个别名仅支持 `count=1`；缺少实时快照，或无法找到完整同柜组合且相关候选缺失机柜拓扑时拒绝。

`resourceGroups[].placement` 包含 `policy=same_cabinet_first`、`mode`（`same_cabinet` / `cross_cabinet_allowed`）及 `reason`，与最终约束一起绑定确认 token。执行前重新预检，模式变化必须重新确认；同柜组合仍可行时，无关数量或枚举顺序变化不使确认失效。

这只保证建单前的选择，不是资源预留或服务端动态回退。建单后不自动放宽约束、不取消重建探测空闲；既有终态及资源释放要求保持不变。带同柜约束的 `run-local` 仍在创建计划前拒绝。

## 本地 Client 执行

```text
device_cloud.py run-local
  [--automation-type app|embedded|hardware]
  [--firmware-version TESTED_FIRMWARE_VERSION]
  [--companion-firmware-version COMPANION_FIRMWARE_VERSION]
  [--app-url URL]
  (--uid UID | --module NAME | --tags EXPR)
  [--submodule NAME]
  [测试代码来源参数]
  [--client-root PATH]
  [--device-cloud-env prod-cn|staging-cn]
  [--resource-profile ...]
  [--resource-json JSON]
  [--resource-constraints JSON]
  [--allow-multiple]
  [--country US]
  [--plan-name NAME]
  [--priority N]
  [--poll-interval SECONDS]
  [--timeout SECONDS]
  [--queue-timeout SECONDS]
  [--cleanup-timeout SECONDS]
  [--max-concurrency N]
  [--dry-run]
  [--confirm PREFLIGHT_SHA256]
```

`run-local` 复用相同的自动化类型、固件、仓库、UID、资源和确认契约，只允许单 Job。入口先创建受管云端 Plan/Job，再运行本地 Client 并绑定该 Job。Client 必须自带 Behave runner/hooks；业务仓若有 `features/environment.py` 会被拒绝。

## 终态与资源释放

### 亲和约束执行契约

`platform` 的 `$eq:` / `$in:` 值在生成预检与建单载荷前统一为小写，例如 `$eq:Android` 规范为 `$eq:android`；型号和拓扑值保持大小写精确匹配。不得仅在本地忽略大小写、却把未规范化的条件交给服务端，造成预检有资源但调度命中为空。

云端 `run` 接受 `--resource-constraints` JSON 数组；`run-local` 遇到非空约束在建单前拒绝。例如：

```json
[
  {"type":"AFFINITY","topologyKey":"belong_to_host","resources":["phone","camera"]},
  {"type":"AFFINITY","topologyKey":"belong_to_cabinet","resources":["phone","camera"]}
]
```

资源条件仍通过 `--resource-json` 指定（例如 camera 型号 `device_model=$eq:KF451`）；亲和约束不是 resources 条件，也不是 metadata。每条只接受 `type`、`topologyKey`、`resources`，未知类型、拓扑、资源别名和不支持的 count 在创建 Plan 前失败。Feature 的 `# @firmware.affinity=...` 声明生成 phone/camera 约束，与 CLI 条件合并。

约束必须出现在 `preflight.resourceGroups[].constraints` 和 `CreateJobInput.constraints`，并进入确认 token。实时容量按全部约束同时成立的资源组合计算，使用回溯避免先挑到错误 Host 后错误拒绝可用组合；资源被占用、缺拓扑或查询截断时不得乐观放行。查询只读取调度元数据，不扩大读取账号、密钥或连接端口。

有约束云端 Job 创建后立即读回持久化约束；拿到 Job ID 即记录，保证后续读取失败也能清理。运行中检查资源的 `jobId`、`jobRequirementName`、`belongToHost`、`belongToCabinet`。错配或无法证明会失败并取消/核验释放；绑定缺失与运行状态冲突时重新读取终态，避免两次请求之间正常结束释放被误判。云端结构化结果中的 `affinityVerification` 只含 `jobId`、`verified`，不输出拓扑 ID。正常结束的资源自动释放不取消此前正确的核验记录；从未观测到完整正确分配不得报告成功。

带亲和约束的 `run-local` / 内部 `--no-wait` 在创建 Plan 前明确拒绝，提示使用云端 `run`。legacy Client 必须先建立 Plan 会话才能获得资源；本次不扩展该交接协议。无约束的本地执行维持原流程。亲和 Job 内的所有别名均要求严格整数 `count=1`，包括未参与约束的附加资源。

Server 源码契约核对基线：`DEVT/device-cloud-server@1e551dea1f50b3969ab5c9637a9717269500e8cd` 的 `CreateJobInput`、`ResourceAllocatorServiceImpl.checkAffinityConstraint` 和 `graphql/rescource.graphqls`。该基线仅证明源码接口，不证明目标部署版本或已做生产运行验收。Server 的 `$in` 多值筛选问题独立处理，不通过删除亲和条件绕过。

`run` / `run-local` 取得 Plan/Job 标识后，无论成功、用例失败、超时、初始化异常还是 Client/Host/用户中断，都必须在返回前：

1. 对非正常退出请求 `cancelJob`；正常路径超过清理等待时间仍未终态时也请求取消。
2. 轮询确认 Job 进入终态。
3. 查询 `phoneConnection` 和 `deviceConnection`，确认没有节点的 `jobId` 仍等于目标 Job。

`run-local` 在建单助手输出有效 `[RESULT_JSON]` 后即保存 Plan/Job 标识。助手随后非零退出时，仍以失败身份清理已知计划及 Job，不启动 Client，清理成功也不会将建单失败改为成功。没有可验证标识时明确报错，不能确认释放，也不能按可能重复的计划名取消其它任务。

资源绑定查询的单页上限为 1000 条；任一连接返回达到上限时，当前入口无法证明全量释放，必须报告清理未验证，不能把首屏无绑定当作释放成功。网络超时同样保留在结构化清理结果中。

`--cleanup-timeout` 默认 60 秒，控制每个自然终结/取消核验窗口。清理失败会使命令失败，并在结构化结果中保留：

```json
{
  "planId": 12,
  "jobIds": [34],
  "finalStatus": "FAILED",
  "firstFailureReason": "Job 终态或资源释放核验失败",
  "cleanup": {
    "verified": false,
    "jobs": [{
      "jobId": 34,
      "cancelRequested": true,
      "verifiedTerminal": true,
      "resourcesReleased": false,
      "finalStatus": "ABORTED_BY_WEB",
      "error": "job#34 终态后资源仍未释放"
    }]
  }
}
```

## 兼容资源档案

`phone`、`phone+battery-camera` 和 `phone+plugin-camera` 只为旧 App 用例保留。通用固件/硬件资源应来自资源表或 JSON，例如：

```json
[
  {"name": "robot", "type": "ROBOT_ARM", "conditions": {}},
  {"name": "chamber", "type": "TEMPERATURE_CONTROLLER", "conditions": {}}
]
```

## 停止与诊断

```text
device_cloud.py cancel job --job-id ID [--device-cloud-env ...]
device_cloud.py cancel plan --plan-id ID [--device-cloud-env ...]

device_cloud.py diagnose [--device-cloud-env ...] full --job-id ID --output-dir DIR
device_cloud.py diagnose [--device-cloud-env ...] job-report --job-id ID
device_cloud.py diagnose [--device-cloud-env ...] timeline --job-id ID --scenario-id ID
device_cloud.py diagnose [--device-cloud-env ...] logs --job-id ID --item-uuid UUID
device_cloud.py diagnose [--device-cloud-env ...] evidence --job-id ID --item-uuid UUID
device_cloud.py diagnose [--device-cloud-env ...] evidence-content \
  --job-id ID --content-path PATH --output FILE [--preview]
```

## 内部参数

不向用户索要 `--app-type`、`--client-mode`、`--job-type`、`--plan-type`、任何 `DEVIUM_APP_*` 字段或运行密钥。`--tests-repo`、`--tests-ref`、`--tests-module` 已是公开输入，必须出现在预检中。

`trigger_cloud_job.py` 的底层 `--no-wait` 保留给兼容调用者交接生命周期；它不是统一 `run` / `run-local` 的用户选项。本地受管创建助手必须先返回 Plan/Job，再由 `run-local` owner 绑定 Client 并完成取消、终态及资源释放核验。Agent 只能使用 `device_cloud.py` 统一执行入口，不能直接调用底层脚本把创建返回当成测试完成。

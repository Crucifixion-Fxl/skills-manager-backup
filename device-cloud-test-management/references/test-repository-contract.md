# 测试仓库契约

## 目录与提交

- 仓库根目录必须包含 `features/`，用例文件为 `features/**/*.feature`。文件按被测功能命名，例如 `features/upgrade/camera-ota.feature`，不由 App URL 或品牌决定。
- 固件/嵌入式任务未指定其他测试仓时，交互默认明确使用 `https://gitlab.addx.ai/DEVT/test-suite/firmware-tests.git`；用户指定其他仓库时尊重该来源，不能因附带 App URL 回退到 `DEVT/kb-tests`。
- `--cases-root` 查询可读取无 `origin` 的本地目录。`run` / `run-local` 使用本地 checkout 时必须有可访问的 `origin`，用它核验所选远端 ref；显式 `--tests-repo` 必须指向同一仓库，不能替代 `origin`。两者仓库身份不一致时预检拒绝。
- `--tests-module` 缺省从仓库 URL 的末级名称推导，例如 `firmware-tests.git` 推导为 `firmware-tests`。
- 云端 Host 执行使用远端可达的提交 SHA。本地 HEAD 不是所选远端 ref 的当前提交，或 `features/` 有未提交改动时，云端预检失败。
- 公开 `--tests-ref` 使用远端分支或轻量标签名；预检解析后固定 SHA。裸 SHA 和附注标签当前不属于支持的请求输入。
- 不要求业务仓提供 `features/environment.py`；若存在该文件，本地 Client 执行会拒绝，避免覆盖 Client 托管 hooks。

## UID 与模块

每个 Scenario 在自身标签块中声明唯一 UID：

```gherkin
@testCase_modules=firmware @testCase_submodule=upgrade
Feature: firmware upgrade

  @uid=firmware-upgrade-001
  Scenario: upgrade succeeds
```

Feature 和 Rule 标签会按 Gherkin 语义继承，Scenario 执行身份只取 Scenario 自身的 `@uid`。模块批量选择会枚举匹配范围内所有 UID；缺失或重复 UID 都会阻止创建计划。

## 资源表

在 Background 或 Scenario 中声明逻辑资源名和平台资源类型：

```gherkin
Feature: production station
  Background:
    | robot   | ROBOT_ARM             |
    | chamber | TEMPERATURE_CONTROLLER |

  @uid=factory-001 @testCase_modules=factory
  Scenario: calibrated motion
```

资源类型会原样大写传给 Device Cloud，不限于 Skill 内置枚举。单个 Scenario 的资源声明覆盖 Background 同名条目，同一资源表不要重复名称。同一 Job 合并多个 Scenario 的最终资源声明时，若同一逻辑名要求不同类型，预检失败；不同 Feature 各自创建 Job，可以使用同名资源。

为避免把日志断言、Examples 等普通 Gherkin 数据表误当成资源，资源表使用两列，并满足以下任一形式：直接位于 `Background` / `Scenario`，或“resources 配置”“resources config”“resources configured”等当前可识别步骤之后；或者首行为 `name | type`（也接受“资源名 | 资源类型”）表头。英文步骤采用其它表达时应加表头；当前不承诺识别任意意译，例如无表头的 `Given these resources are configured` 不会被当作资源表。其他数据表不会参与资源调度。

无法在用例表中表达的条件可通过 `--resource-json` 提供：

```json
[
  {"name": "fixture", "type": "FIXTURE", "conditions": {}},
  {"name": "camera", "type": "BATTERYCAM", "conditions": {"device_model": "$in:KF126"}}
]
```

实时容量预检目前支持无条件资源，以及 `platform`、`device_model`、`belong_to_host`、`belong_to_cabinet` 的 `$eq:`/`$in:` 条件。不支持的条件会在创建 TestPlan 前失败。

### 资源亲和声明

Feature 顶部可声明 phone/camera 的亲和性：

```gherkin
# @firmware.affinity=belong_to_host,belong_to_cabinet
Feature: resources from the same test station
  Background:
    Given 我有resources配置
      | phone  | PHONE      |
      | camera | BATTERYCAM |
```

仅支持 `belong_to_host` 与 `belong_to_cabinet`，可用逗号同时声明。已有单独 `belong_to_cabinet` 声明保留原意，不自动推断同 Host。缺少 phone/camera、声明格式错误或未知拓扑时预检失败，不忽略声明。

其他逻辑别名或用户额外约束使用 `--resource-constraints`，格式见 CLI 契约。约束与 Feature 声明合并、排序去重，逐 Job 进入预检、确认摘要和 Server 建单载荷。有亲和约束的 Job 内所有资源的 `count` 只允许严格整数 1。亲和性执行仅支持云端 `run`；带约束的 `run-local` 在建单前拒绝。内部拓扑数据只用于组合校验，不公开实际 ID；不能将全局在线资源数量当作同域可分配容量。

资源条件还可按 `belong_to_host` / `belong_to_cabinet` 核对内部拓扑，但等值筛选不能代替亲和约束的持久化与执行回读。本次只补齐 Skill 链路，不承诺解决 Server 对多值 `$in` 条件的独立兼容问题。

## 固件输入与当前消费者

固件/嵌入式执行必须提供待测固件 `--firmware-version`，且为 `-d` 调试版本；Background 默认升级以它为目标。配测固件 `--companion-firmware-version` 可选，默认等于待测版本，用于 OTA 等需另一固件的步骤；显式提供时同样要求 `-d`。两者都是固件；设备当前实际版本由运行时读取，不要求用户另报。

Skill 负责取参、预检和将版本写入 Job metadata，不要求用户手改资源配置。待测版本经 `DEVIUM_FIRMWARE_VERSION` 进入 `ResourceConfig.firmware_version`；配测版本经 `DEVIUM_COMPANION_FIRMWARE_VERSION` 传递，读取为 `ResourceConfig.companion_firmware_version` 需要配套 Client 配置支持。旧 Client 无该字段和映射；包含支持的 Client 应在配测未提供时使用有效待测值，并在资源合并后保留。字段对应关系见 [CLI 契约](cli-contract.md)。

配置解析与业务使用分别验收：Client Background 仍要求 App 产品、环境、国家及手机，缺项可能跳过或中止，版本注入不能证明已升级。业务 OTA Steps 当前尚未使用独立配测字段，预检固定标记 `companionVersionConsumer: null`。Feature 已在主分支不代表相关步骤已实现或用例已通过；遇到无匹配 Step 或无 DebugOTA 能力时，停止并报告具体阻塞，不随机选择固件或宣称 OTA 已支持。

## App 是可选制品

需要 App 的 Scenario 可额外提供 `--app-url`。App 版本、包名和业务环境只从用户下载链接解析，不另行索要；当前解析器仅支持可识别的 Kiwibit Android 包。App URL 不选择自动化类型、测试仓、文件名或固件版本。例如 Kiwibit App 参与固件升级用例时，仍使用已确定的固件测试仓和模块。只有模糊任务或 URL、仍不能确定类型时先询问；用户明确类型、仓库或经核实的 UID 足以判定时不重复询问。

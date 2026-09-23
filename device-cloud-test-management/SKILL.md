---
name: device-cloud-test-management
description: 通过一次飞书/Casdoor SSO 查询并运行 kb-tests 云测用例，管理 TestPlan/Job，并按固定流程查询日志、时间线、截图、XML 和录像。用于用户要求创建 Device Cloud 测试计划、运行单条/批量用例、停止任务、分析失败或下载附件时；从 App 链接和用例元数据自动推导普通参数，不索要 API Key。
---

# Device Cloud 测试计划与诊断

## 描述

本 Skill 负责查询用例、创建和停止 Device Cloud 测试计划，以及获取日志和附件并完成失败分类。所有业务密钥由平台托管，用户身份只通过飞书/Casdoor SSO 获取。

只使用本 Skill 自带入口，不依赖 `DEVT/device-cloud` checkout：

```text
skills/device-cloud-test-management/scripts/device_cloud.py
```

## 使用前提：AI 必须能读取用例定义

飞书/Casdoor SSO 只负责 Device Cloud 身份认证，不会自动授予 GitLab 仓库权限。AI 要知道有哪些用例、UID 和资源依赖，必须满足以下任一条件：

- 本机已有可读取的 `kb-tests` 仓库，通过 `--cases-root <PATH>` 或 `KB_TESTS_ROOT` 指定；
- 本机 Git 凭据拥有 `https://gitlab.addx.ai/DEVT/kb-tests.git` 的只读权限，脚本可自动缓存克隆。

首次使用先运行一次用例查询作为前置检查：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --text '<用例关键词>'
```

查询失败且本地没有仓库时，停止创建 TestPlan，并提示用户申请 `DEVT/kb-tests` 只读权限。禁止要求用户在聊天或命令行提供 GitLab Token。云端真正执行用例时，Host 仍使用平台托管凭据下载仓库，不依赖个人 GitLab 权限。

## 用户只需提供两类输入

1. App 下载链接。
2. 跑什么：UID、模块/功能描述或用例关键词。

不要询问仓库、分支、App 名、包名、版本、App 环境、Host、机房、机柜、设备 ID 或任何密钥。App 链接映射见 [App 链接映射](references/app-link-mapping.md)。创建前仍必须把自动生成的资源与并发计划展示给用户，获得明确确认；这是执行确认，不是新增输入。

## 环境边界

- Device Cloud 默认是 `prod-cn`。只有用户明确说 Host2、staging 或迁移验收时才用 `staging-cn`。
- App 包中的 `_prod_`、`_test_`、`_pre_` 是 App 服务环境，只生成 `DEVIUM_ENV`。
- 两种环境互不推导。

## 规则

- 创建前必须展示实际匹配场景、最终资源组、实时容量快照和计划并发，并获得明确确认。
- 创建前必须确认本地能读取 `kb-tests` 用例定义；飞书 SSO 不能替代 GitLab 只读权限。
- “一条用例”只能执行唯一 UID；匹配数量不是 1 时不得创建。
- 从用例步骤和显式资源表识别逻辑资源别名；`config/resources.yml` 只是运行模板，不是调度事实。
- 相机类型不能从空闲池反推；用例未显式声明时必须让用户选择资源档案。
- 批量用例按 Feature 拆成独立 Job；每个 Job 内保留该 Feature 的逻辑资源别名，并按实时空闲容量限制并发。
- 诊断必须完成报告、时间线、日志、证据下载和失败分类全链路。
- 不接收、不读取、不输出业务运行密钥。

## 强制执行流程

### 1. 解析 App

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py inspect-app \
  --app-url '<APP_URL>'
```

链接无法唯一识别 App、版本或业务环境时停止，不猜测。

### 2. 先查询用例，再执行

用户没有给 UID 时，先搜索：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --module '直播'

python skills/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --module '设备设置' \
  --submodule 'video'

python skills/device-cloud-test-management/scripts/device_cloud.py search-cases \
  --text '快速进入直播'
```

查询结果必须输出 UID、名称、匹配数量、是否引用 camera、相机类型和资源来源。

当用户说“一条用例”时：

- 匹配 1 条：直接使用该 UID，不再追问。
- 匹配 0 条：停止并说明没有候选。
- 匹配多条：列出候选 UID/名称，让用户选择；不得直接用 `--module` 创建任务。
- 只有用户明确允许批量时才使用 `--allow-multiple`。

`--submodule` 只能与 `--module` 一起使用，等价于同时匹配 `@testCase_modules=<module>` 与 `@testCase_submodule=<submodule>`。本地目录按 Gherkin 语义合并 Feature、Rule 和 Scenario 标签；Scenario 的执行 UID 仍只取 Scenario 自身的 `@uid`，不会误用 Feature UID。

### 3. 创建前资源预检

默认 `--resource-profile auto`。脚本先查询 Device Cloud 实时在线、未占用资源，再从以下真实来源推导每个 Feature 的逻辑资源：

1. Feature `Background` 或 Scenario 内的显式资源表；
2. 实际设备操作步骤中的手机别名，例如 `on admin`、`on sharer`；
3. 步骤中对 `camera` 的引用。

账号角色名不等于物理手机别名；只有出现在设备操作语义中的名称才计入资源。不得从 `@deviceType` 或 `config/resources.yml` 推断调度资源。

`auto` 仅在用例显式指定唯一相机类型时自动选择。用例没有类型或存在多种候选时停止，让用户选择 `phone+battery-camera` 或 `phone+plugin-camera`；空闲池只有一种类型也不能替代业务需求。用例引用 camera 而最终资源没有相机时，必须在创建 TestPlan 前报错。

批量选择按 Feature 拆分 Job，同一 Feature 的 Scenario 用 UID 精确组合，避免模块标签变化造成误跑。预检必须展示：UID、名称、匹配数量、每个 Job 的逻辑资源别名、最终资源组、实时空闲容量、计划并发，以及将创建的 Plan/Job/Scenario 数量。只展示按类型和型号汇总的容量，不展示设备 ID、序列号、Host、IP、端口或连接信息。

### 4. 执行

先生成预检，不创建 TestPlan：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py run \
  --app-url '<APP_URL>' \
  --uid '<UID>' \
  --dry-run
```

把完整预检结果及其中的 `confirmation.token`、`planName` 展示给用户。只有用户明确确认该资源分组和并发计划后，才原样携带该摘要和计划名执行：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py run \
  --app-url '<APP_URL>' \
  --uid '<UID>' \
  --plan-name '<PREFLIGHT_PLAN_NAME>' \
  --confirm '<PREFLIGHT_SHA256>'
```

执行命令会重新预检；不可变用例提交、用例内容、国家、优先级、超时、场景、资源容量、资源分组或并发任一变化都会导致摘要不匹配并拒绝创建，必须展示新预检后重新确认。云端 Job 使用远端可达的提交 SHA，避免确认后分支前移；本地 Feature 有未提交改动时禁止执行。批量时同时使用 `--allow-multiple`；默认最多并发 4 个 Job，可用 `--max-concurrency` 下调。实时容量不足时不得创建 TestPlan；多 Job 不允许 `--no-wait`，避免绕过并发窗口。

上面是默认的云端 Host 容器执行。用户明确要求“本地 Client 使用云端资源”时，必须使用受控入口：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py run-local \
  --app-url '<APP_URL>' \
  --uid '<UID>' \
  --client-root '<DEVICE_CLOUD_CLIENT_ROOT>' \
  --plan-name '<PREFLIGHT_PLAN_NAME>' \
  --confirm '<PREFLIGHT_SHA256>'
```

禁止直接调用 `device-cloud-client/run_tests.py`。`run-local` 会把干净的 Client 提交、内容摘要和路径纳入确认摘要，再由 Skill/Server 按预检结果
创建唯一的 TestPlan/Job，再让本地 Client 绑定并执行这一个既有 Job。Client 不得从
`kb-tests/config/resources.yml` 重新生成资源需求或创建第二个 Job。Skill 会在创建计划前验证
Client 和 `kb-tests` 是否包含既有 Job 绑定、飞书 SSO 邮箱和平台托管运行凭据能力；版本过旧时直接终止。
真实执行前还会用当前 Python 检查 Behave、Pillow 和 GraphQL HTTP 依赖；依赖不兼容时在
打开 SSO 或创建 TestPlan 前失败。Client 即使错误地以零退出码结束，只要输出包含致命导入/
初始化异常，Skill 也必须返回失败，禁止报告“执行成功”。
本地执行的计划名与云端执行统一为 `ai-<app>-*`；`<app>` 从 App 链接识别出的正式应用名映射生成（例如 `Kiwibit_Android → Kiwibit → kb`）。`kb` 只属于 Kiwibit，不能对其他 App 硬编码复用，也不能使用运行时类型 `BAPP` 代替应用名。负责人必须是本次 SSO 返回的邮箱，
不得回退为 Windows/macOS 系统用户名或显示为“未知用户”。

只预览时加 `--dry-run`；确认执行时把预检摘要传给 `--confirm`；明确批量时加 `--allow-multiple`；`--no-wait` 仅限单 Job。

不得通过命令行、聊天、`--env-file`、`--inject-env` 或 Job metadata 主动提交 `GITLAB_TOKEN`、`CLIENT_GITLAB_TOKEN`、`PIR_SIGN_SECRET`、`RP_API_KEY`、`LITELLM_API_KEY`、`LANGCHAIN_API_KEY`、`REDIS_PASSWORD`。Skill 不扫描、不删除也不限制用户电脑已有的环境变量；运行凭据由 Server/Host/Vault 托管。

### 5. 停止

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py cancel job \
  --job-id <JOB_ID>

python skills/device-cloud-test-management/scripts/device_cloud.py cancel plan \
  --plan-id <PLAN_ID>
```

停止后必须查询并确认 Job 已到终态。平台没有 `cancelTestPlan` mutation；`cancel plan` 会取消并核验计划内全部未结束 Job，不删除历史记录。

### 6. 失败诊断必须走完整 SOP

禁止只看页面错误文本就下结论。默认运行：

```bash
python skills/device-cloud-test-management/scripts/device_cloud.py diagnose \
  full --job-id <JOB_ID> --output-dir '<OUTPUT_DIR>'
```

该命令固定执行：

```text
job-report
→ 首个失败步骤
→ timeline
→ 失败时间窗口 logs
→ evidence
→ 下载并检查截图/XML
→ 失败分类
```

需要单独查询时再使用 [CLI 契约](references/cli-contract.md) 中的低层诊断命令。

失败分类只能是：产品功能失败、业务断言失败、用例脚本过时、元素/导航不兼容、资源申请错误、平台或基础设施失败。

## SSO

每次认证会在标准错误明确输出一种状态：

```text
SSO: reused cached refresh token
SSO: browser launched at HH:MM:SS
```

- access token 只存在当前进程内存。
- refresh token 存在操作系统安全凭据存储。
- 云端 Host `run` 的同一次 CLI 操作必须复用一个已认证会话；查询资源、创建计划和轮询 Job 不得重复读取系统凭据。
- refresh token 轮换时必须原地更新已有系统凭据，保留用户授予的访问控制；不得通过删除再新建覆盖已有条目。
- 服务端返回的 refresh token 未变化时不得重复写入系统凭据。
- Windows 使用系统 Credential Manager；macOS/Linux 必须预先安装 Python `keyring` 并配置可用的系统安全存储后端。缺少安全后端时认证会明确失败，禁止降级为明文文件。
- 没有有效会话时只允许一个进程打开系统默认浏览器。
- Server 校验 token 并使用登录邮箱写入真实 `launchedBy`。
- 不输出任何 token 或 Secret 值。

## 终态输出

无论成功或失败，都必须明确输出：

- Device Cloud 环境和 App 业务环境；
- Plan ID、Job ID；
- 最终资源组和场景数；
- 最终状态；
- 首个失败原因及失败分类；
- 已下载附件的绝对路径。

CLI 的结构化终态以 `[RESULT_JSON]` 开头。非零退出码表示测试或诊断失败，不等于 TestPlan/Job 没有创建。

## 示例

### Good Example

用户说：“用这个 App 链接跑一条快速直播用例。”先用 `search-cases --module 直播 --text 快速` 查询；若唯一匹配，则以该 UID 生成预检。预检显示一个 Scenario、同组 `PHONE + PLUGINCAM`、实时容量和并发 1；用户确认后以 `--confirm` 创建，并最终返回 Plan ID、Job ID、状态和附件路径。

### Bad Example

用户说“一条直播用例”，却直接运行 `--module 直播`，在未显示匹配数量、未解析 camera 依赖的情况下创建包含多条 Scenario 的 Job；或者向用户索要 GitLab Token、RP Key、Host/设备 ID。以上做法均禁止。

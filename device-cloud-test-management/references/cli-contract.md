# CLI 契约

## 统一入口

```text
python skills/device-cloud-test-management/scripts/device_cloud.py <command>
```

认证、触发和诊断脚本已打包在 Skill 中。Windows 只依赖 Python 3 标准库并使用 Credential Manager；macOS/Linux 还必须安装 Python `keyring`，并配置可用的系统安全存储后端。缺少安全后端时认证会失败，不会把 refresh token 降级写入明文文件。

## 查询用例

```text
device_cloud.py search-cases
  [--uid UID | --module NAME | --tags EXPR]
  [--submodule NAME]
  [--text TEXT]
  [--cases-root PATH]
  [--tests-ref main]
  [--refresh-cases]
```

没有本地 `kb-tests` 时，脚本把 `https://gitlab.addx.ai/DEVT/kb-tests.git` 缓存到用户目录。仅此查询步骤需要用户具有该测试仓库的读取权限；云端 Client 下载测试代码仍使用平台托管凭据。

## 创建 TestPlan + Job

```text
device_cloud.py run
  --app-url URL
  (--uid UID | --module NAME | --tags EXPR | --feature-id ID)
  [--submodule NAME]
  [--device-cloud-env prod-cn|staging-cn]
  [--resource-profile auto|phone|phone+battery-camera|phone+plugin-camera]
  [--resource-json JSON]
  [--allow-multiple]
  [--cases-root PATH]
  [--country US]
  [--tests-ref main]
  [--refresh-cases]
  [--plan-name NAME]
  [--priority N]
  [--poll-interval SECONDS]
  [--timeout SECONDS]
  [--queue-timeout SECONDS]
  [--dry-run]
  [--confirm PREFLIGHT_SHA256]
  [--max-concurrency N]
  [--no-wait]
```

`run` 每次都会通过飞书/Casdoor SSO 查询实时资源容量。`--dry-run` 只输出预检和 `confirmation.token`；真实创建必须把该摘要作为 `--confirm` 的值，并把预检生成的 `planName` 原样传给 `--plan-name`。执行前重新计算的不可变用例提交、用例内容、国家、优先级、超时、场景、容量、资源或并发发生变化时，摘要校验失败并要求重新确认。云端执行使用已验证远端可达的提交 SHA，不直接执行可能前移的分支名。

`auto` 只使用用例显式声明的唯一相机类型，不能从空闲池、`config/resources.yml` 或 `@deviceType` 猜测。批量选择按 Feature 拆 Job，并用精确 UID 表达式执行；默认 `--max-concurrency 4`，实际并发不会超过实时空闲容量。多 Job 禁止 `--no-wait`。

`--submodule` 必须与 `--module` 一起使用，选择表达式为 `@testCase_modules=<module> and @testCase_submodule=<submodule>`。目录解析遵循 Feature/Rule/Scenario 标签继承，但执行身份只取 Scenario 标签块中的 `@uid`。

`--module`/`--submodule`/`--tags` 匹配多条时默认拒绝，只有明确添加 `--allow-multiple` 才允许批量。`--feature-id` 无法在本地确认场景数和资源元数据，因此必须明确配合 `--allow-multiple`，并指定非 `auto` 的 `--resource-profile` 或提供 `--resource-json`。缺少这些信息时会在创建 TestPlan 前终止。

## 本地 Client 使用云端资源

```text
device_cloud.py run-local
  --app-url URL
  (--uid UID | --module NAME | --tags EXPR)
  [--submodule NAME]
  [--client-root PATH]
  [--device-cloud-env prod-cn|staging-cn]
  [--resource-profile auto|phone|phone+battery-camera|phone+plugin-camera]
  [--resource-json JSON]
  [--allow-multiple]
  [--cases-root PATH]
  [--country US]
  [--tests-ref main]
  [--refresh-cases]
  [--plan-name NAME]
  [--priority N]
  [--poll-interval SECONDS]
  [--timeout SECONDS]
  [--queue-timeout SECONDS]
  [--dry-run]
  [--confirm PREFLIGHT_SHA256]
  [--max-concurrency N]
```

该入口复用与 `run` 相同的用例唯一性和资源预检，但执行进程在本机。它会先检查
`device-cloud-client` 和 `kb-tests` 是否具备受控本地运行协议，再把统一计划名传给 Client；
Client 通过飞书/Casdoor SSO 获取用户邮箱并创建 Job。禁止直接运行 `run_tests.py`，因为旧版本
会生成 `TestPlan-*` 默认名称，并可能把系统登录名写成负责人。
真实执行要求当前 Python 是该 Client 已同步依赖的虚拟环境；入口会在 SSO 和创建计划前检查
Behave、Pillow 与 GraphQL HTTP 依赖。Client 输出致命导入或 Behave 初始化异常时，即使其
退出码为 0，入口仍返回失败。

特殊资源 JSON 仍受 camera 防呆校验：

```json
[
  {"name": "phone", "type": "PHONE", "conditions": {"platform": "$eq:android"}},
  {"name": "camera", "type": "BATTERYCAM", "conditions": {"device_model": "$in:KF126"}}
]
```

## 停止

```text
device_cloud.py cancel job --job-id ID [--device-cloud-env ...]
device_cloud.py cancel plan --plan-id ID [--device-cloud-env ...]
```

## 诊断

完整 SOP：

```text
device_cloud.py diagnose [--device-cloud-env ...] full --job-id ID --output-dir DIR
```

低层命令：

```text
device_cloud.py diagnose [--device-cloud-env ...] job-report --job-id ID
device_cloud.py diagnose [--device-cloud-env ...] timeline --job-id ID --scenario-id ID
device_cloud.py diagnose [--device-cloud-env ...] logs --job-id ID --item-uuid UUID
device_cloud.py diagnose [--device-cloud-env ...] evidence --job-id ID --item-uuid UUID
device_cloud.py diagnose [--device-cloud-env ...] evidence-content \
  --job-id ID --content-path PATH --output FILE [--preview]
```

## 不属于公共输入的参数

不向用户询问：`--app-type`、`--tests-repo`、`--tests-module`、默认分支、`--client-mode`、`--job-type`、`--plan-type`、`DEVIUM_APP_NAME`、`DEVIUM_PACKAGE_NAME`、`DEVIUM_VERSION`、`DEVIUM_ENV` 或任何运行密钥。

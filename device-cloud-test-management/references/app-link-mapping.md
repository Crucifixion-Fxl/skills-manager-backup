# App 链接映射

## 定位

App URL 是可选运行制品。它只提供安装包及 App 运行环境，不选择自动化类型、测试仓库、测试模块或设备资源。

如果用户只提供 App URL，先确认要执行 App、固件/嵌入式还是硬件自动化，再确定测试仓和用例。用户已指定测试仓、模块或 UID 时，以显式信息为准。例如 Kiwibit App 参与固件用例时，测试来源仍可为 `firmware-tests`。

## 当前可解析链接

当前 CLI 可确定性解析 Kiwibit Android APK：

```text
.../static/apk/Kiwibit_Android/<version>/kiwibit_android_<version>-<build>_<env>_<commit>_<built-at>.apk
```

| 链接内容 | 运行参数 |
|---|---|
| 原始 URL | `DEVIUM_APP_URL` |
| 唯一 `x.y.z` | `DEVIUM_VERSION` |
| `_prod_` / `_staging_` / `_pre_` | `DEVIUM_ENV` |
| 固定 Kiwibit Android 映射 | `DEVIUM_APP_NAME=BAPP`、`DEVIUM_PACKAGE_NAME=com.kb.kiwibit` |
| `--country`，默认 US | `DEVIUM_COUNTRY` |

build number、commit、build time 只用于审计。无法唯一解析 App、版本或服务环境时停止，不猜测。

## 与测试仓解耦

`--tests-repo`、`--tests-ref`、`--tests-module` 由测试意图和仓库决定。内部 `appType` 也使用测试模块，而不是 App 品牌。Agent 必须检查实际选中的 Feature Background 和 Scenario 步骤：包含卸载、安装、重新安装、升级/降级或切换指定版本 App 的用例缺少 `--app-url` 时，不得执行、建单或申请资源；只有所选用例的实际步骤不需要安装包时，才可以不注入 `DEVIUM_APP_*`。URL 解析出的 App 身份必须与全部所选用例的 `@app` 一致；一个选择器命中多个 App 时按 App 拆分计划；需要安装包但缺少 `@app` 时停止。

## 环境正交

Device Cloud 的 `prod-cn` / `staging-cn` 与 App 的 `prod` / `staging` / `pre` 互不推导。App 的旧 `_test_` 标记和其他未知环境不受支持；默认平台为 `prod-cn`，只有用户明确要求 staging/Host2 验收时才改变。

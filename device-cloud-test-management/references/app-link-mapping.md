# App 链接映射

## 当前支持范围

当前执行仓库是 Kiwibit 的 `kb-tests`，已确认的 Android 链接示例结构：

```text
.../static/apk/Kiwibit_Android/<version>/kiwibit_android_<version>-<build>_<env>_<commit>_<built-at>.apk
```

## 自动映射

| 链接内容 | 生成参数 | 说明 |
|---|---|---|
| 原始 URL | `DEVIUM_APP_URL` | 原样使用，不下载 APK 到本机 |
| `Kiwibit_Android` / `kiwibit_android` | `appType=kb-tests` | 同时固定测试模块 |
| `.apk` / `Android` | 手机条件 `platform=android` | 当前只支持 Android |
| 唯一的 `x.y.z` | `DEVIUM_VERSION` | 版本无法唯一识别时停止 |
| `_prod_` | `DEVIUM_ENV=prod` | App 服务环境，不是 Device Cloud 环境 |
| `_test_` | `DEVIUM_ENV=test` | 同上 |
| `_pre_` | `DEVIUM_ENV=pre` | 同上 |
| 固定映射 | `DEVIUM_APP_NAME=BAPP` | Kiwibit 在 Client 中使用的 App 名 |
| 固定映射 | `DEVIUM_PACKAGE_NAME=com.kb.kiwibit` | Android 包名 |
| 默认值 | `DEVIUM_COUNTRY=US` | 用户明确要求其他国家时覆盖 |

以下信息仅用于审计和生成可读计划名，不是创建 Job 的必填项：build number、commit、build time。

## 固定测试代码来源

| 参数 | 值 |
|---|---|
| `appType` | `kb-tests` |
| `TESTS_REPO` | `https://gitlab.addx.ai/DEVT/kb-tests.git` |
| `TESTS_REF` | `main` |
| `TESTS_MODULE` | `kb-tests` |

Host 使用平台托管的 GitLab 凭据获取测试代码，用户不传 GitLab Token。创建前的本地用例搜索会读取本地 `kb-tests`，或使用用户现有的 Git 凭据只读克隆该仓库；它不依赖 `DEVT/device-cloud` checkout。

## 环境正交关系

| Device Cloud | App 包 | 正确含义 |
|---|---|---|
| `prod-cn` | `_prod_` | 线上云测平台运行 prod App |
| `prod-cn` | `_test_` | 线上云测平台运行 test App（允许） |
| `staging-cn` | `_prod_` | Host2/staging 验收运行 prod App（允许） |
| `staging-cn` | `_test_` | Host2/staging 验收运行 test App |

两列互不推导。

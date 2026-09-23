# GitLab 权限与开发签名

## 目录

- [GitLab 权限门禁](#gitlab-权限门禁)
- [当前 release 已知仓库](#当前-release-已知仓库)
- [开发证书门禁](#开发证书门禁)
- [获取开发证书](#获取开发证书)
- [真机运行检查](#真机运行检查)

## GitLab 权限门禁

在目标 release checkout 后动态扫描，静态清单只作参考：

```bash
~/.codex/skills/ios-dev-setup/scripts/scan_gitlab_access.rb /path/to/IosProjects
~/.codex/skills/ios-dev-setup/scripts/scan_gitlab_access.rb --check /path/to/IosProjects
```

`--check` 对每个唯一仓库执行 `GIT_TERMINAL_PROMPT=0 git ls-remote <ssh-url> HEAD`。按失败类型处理：

- `Permission denied (publickey)` / repository not found：检查 SSH key，并申请仓库或上级 Group Reporter/Developer 权限。
- DNS/timeout/no route：连接公司网络或 VPN；这不是仓库权限不足。
- host key verification failed：核对主机指纹后写入 `known_hosts`。
- 只在 `pub get`/`pod install` 失败：仍回到扫描结果，确认 lockfile 新增的传递依赖权限。

不要让用户提供 GitLab 密码或 token；优先使用其本机已配置的 SSH key。脚本只读，不 clone、不写远端。

## 当前 release 已知仓库

以下来自当前 `g0-ios`、`g0-flutter-module` 和 `smartdevicecoresdk-ios` 声明；release 切换后必须重扫。

| 类别 | 仓库 / 源 |
|---|---|
| 主仓 | `SWCLIEN/g0-ios`, `SWCLIEN/g0-flutter-module`, `SWCLIEN/smartdevicecoresdk-ios` |
| Submodule | `AUD/IJK`, `AUD/app/safertc_live/a4xlivesdk` |
| CocoaPods specs / Gem | `192.168.31.7:7999/swclien/modulespecs`, `IOS_MODULE/cocoapods-hooks` |
| Native Pod git | `SWCLIEN/LocalHttpServer`, `IOS_MODULE/payment-core`, `services/value-added/engagement`, `FLUTTER/c_logger` |
| Flutter direct/override/lock | `FLUTTER/flutter_plugin_sdk`, `IOS_MODULE/cms_payment_plugin`, `FLUTTER/flutter_payment`, `FLUTTER/flutter_boost_channel_plugin`, `services/customer-care`, `FLUTTER/flutter_network_plugin`, `FLUTTER/event_tracker`, `FLUTTER/flutter_dev_tool`, `FLUTTER/snowplow_tracker` |

私有 Nexus hosted packages 不是 GitLab 权限，但 `fvm flutter pub get` 仍可能需要 Nexus 访问；遇到 401/403 时单独申请 Nexus 权限，不要误判为 GitLab。

## 开发证书门禁

仅运行模拟器无需 Apple 开发证书。运行 iPhone/iPad 真机或 Apple Silicon Mac 时，在安装依赖前后都应提示：

1. 使用者必须被邀请进公司正确的 Apple Developer Team。
2. Keychain 中必须存在带私钥的 `Apple Development` signing identity；只有 `.cer`、没有 private key 不能签名。
3. `AddxAi`、`AddxPushContent`、`AddxPushService` 三个 target 的 Team、Bundle ID、entitlements 必须分别与 profile 一致。
4. 真机 UDID 必须注册并包含在 development provisioning profile 中；自动签名时 Xcode 可代管 profile 和连接设备注册，但仍依赖团队权限。

先检查本机 identity：

```bash
security find-identity -v -p codesigning
```

若没有有效 `Apple Development:` identity，将真机运行标记为阻塞，并明确提示使用者咨询 iOS 开发同学、获取团队签名材料；继续模拟器构建仍可进行。

## 获取开发证书

g0 iOS 使用团队提供的证书和签名材料，不让使用者自行创建或替换开发证书：

1. 先咨询 iOS 开发同学，确认当前适用的 Team、证书、profile 和导入方式。
2. 打开[团队证书与签名文档](https://a4x-paas.feishu.cn/wiki/BS8QwCVxCibFerkg5V0cn5tknyc)，按开发同学指导下载当前有效的证书和签名材料；无文档权限时请开发同学协助开通。
3. 由使用者本人按文档导入证书、私钥和 provisioning profile，并在 Xcode Signing & Capabilities 选择开发同学确认的 Team。
4. 导入后运行 `security find-identity -v -p codesigning`，确认存在有效的 `Apple Development` identity，再检查三个 target 的签名配置。

证书包、`.p12`、密码、私钥、provisioning profile 和 `fastlane_sign.plist` 都不得上传到对话、打印到日志或提交进 Git。缺少私钥、密码或材料不匹配时继续咨询 iOS 开发同学，不自行生成新证书绕过。

手动 profile 需要 App ID、开发证书和注册设备，由 Account Holder/Admin 生成。过期、撤销证书或新增设备后需重新生成。

## 真机运行检查

连接并信任设备后检查：

```bash
xcrun xctrace list devices
security find-identity -v -p codesigning
xcodebuild -workspace AddxAi.xcworkspace -scheme AddxAi -showBuildSettings \
  | rg 'DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER|CODE_SIGN_STYLE|PROVISIONING_PROFILE_SPECIFIER'
```

在 Xcode Signing & Capabilities 中确认所有 app/extension target 都无红色签名错误，再运行。`Provisioning profile doesn't include device` 时注册当前 UDID并刷新/重建 profile；不要换成无关 Team 或关闭 code signing。

若 profile 报 `doesn't include signing certificate`，说明 Xcode 当前 identity 不在该 profile 中。停止自行调整，联系 iOS 开发同学确认并重新下载相互匹配的证书、私钥和 profiles；仅下载 `.cer` 而没有对应私钥无效。

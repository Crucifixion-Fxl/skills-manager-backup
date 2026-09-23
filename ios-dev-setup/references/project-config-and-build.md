# g0 iOS 项目配置、构建与签名边界

## 三仓关系

`g0-ios` 是 Host App，`g0-flutter-module` 是 Flutter add-to-app 模块，`smartdevicecoresdk-ios` 承载部分原生设备能力。业务修改可能实际落在 SDK，不要只检查 Host App。三仓应保持兄弟目录，并分别确认目标 release；同一产品前缀不保证三仓版本号或当前分支相同。

本地模式下，Podfile 直接引用两个兄弟仓。CI 参数 `parameters_flutter_module` / `parameters_sdk_module` 存在时会切换为 Git source；本地环境不要意外继承这些变量。

## `ProjectConfig.plist`

`g0-ios/AddxAi/AppConfig/ProjectConfig.plist` 是当前品牌和环境的重要输入。至少确认 `tenant_id`、`bundle_display_name`、`build_env`、`is_use_voip` 四个非敏感字段。不要把文件中的服务密钥复制到对话或文档。

`pod install` 会读取 `is_use_voip`；为 false 时，现有 Podfile 会删除 `AddxAi/AppDelegate+Injection.swift` 中的 VOIP 相关行。因此依赖安装不是纯下载操作：运行前后必须比较 `git status --short` 和该文件 diff，不能把变化误当成业务代码或替用户回滚。

## 构建入口

- Workspace：`AddxAi.xcworkspace`
- 主 scheme / target：`AddxAi`
- 扩展 targets：`AddxPushContent`、`AddxPushService`

Pods 未完成时不要改用 `.xcodeproj` 绕过 workspace。先运行：

```bash
xcodebuild -list -workspace AddxAi.xcworkspace
xcodebuild -workspace AddxAi.xcworkspace -scheme AddxAi -showBuildSettings
```

分三层验证：Simulator build/test 不依赖证书；iPhone/iPad device 要求 Team、带私钥的 identity、注册设备和三个 target 的 development profiles；Archive/distribution 才进入 Fastlane 和 Ad Hoc/App Store 签名配置。不要用发布 lane 代替首次环境验证。

Host Podfile 的 deployment target 与 SDK podspec 自己的最低版本可以不同，不要为“统一数字”擅自修改 SDK podspec。

## Fastlane 与敏感配置

Fastlane 会读取 `AddxAi/AppConfig/fastlane_sign.plist`，其中包含 Apple Account、Team、Bundle IDs、profile 名称和密码等敏感数据。该文件在新工作区可能缺失：

- 先咨询 iOS 开发同学，再从[团队证书与签名文档](https://a4x-paas.feishu.cn/wiki/BS8QwCVxCibFerkg5V0cn5tknyc)下载当前适用的证书和签名材料；无权限时请开发同学协助。
- 不在聊天中粘贴、不打印、不提交 Git；普通依赖扫描故意不读取此文件。
- 运行签名 lane 前，先按开发同学指导导入证书、私钥和三个 target 的 development profiles。
- `reloadProfile` / `addDevice` 会刷新 profile 或注册设备。只有用户明确要求且权限、设备信息确认后才执行。
- 历史 lane 中可能含旧设备记录，不要复制或复用其中的 UDID。

若 profile 报 `doesn't include signing certificate`，联系 iOS 开发同学确认并重新下载匹配的证书与 profiles；不要强制选择一张 profile 未包含的本机证书。

## 首次预检

```bash
~/.codex/skills/ios-dev-setup/scripts/preflight_ios_env.sh /path/to/IosProjects
```

预检只输出仓库/环境状态和 `ProjectConfig.plist` 的安全字段，不读取或打印签名 secret，也不修改已有工作区。

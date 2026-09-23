# g0 iOS 常见问题

## 目录

- [Ruby 与 Bundler](#ruby-与-bundler)
- [CocoaPods 插件](#cocoapods-插件)
- [Specs 与网络](#specs-与网络)
- [GitLab 权限](#gitlab-权限)
- [Flutter add-to-app](#flutter-add-to-app)
- [签名与设备](#签名与设备)
- [Xcode 安装与选择](#xcode-安装与选择)
- [模拟器与 Xcode](#模拟器与-xcode)
- [磁盘与工作区变更](#磁盘与工作区变更)

## Ruby 与 Bundler

### Bundler 版本无法运行

症状：系统 Ruby 过旧、`Gemfile.lock` 要求的 Bundler 无法安装，或 `.bundle/config` 指向旧版本。

处理：

1. 读取 `ruby -v`, `which ruby`, `Gemfile.lock` 和 `.bundle/config`。
2. 使用 Homebrew Ruby，并把其 `bin` 与 `Gem.bindir` 放在 PATH 前面。
3. 安装 lockfile 指定 Bundler；显式执行对应版本。
4. 不要顺手更新 `Gemfile.lock` 来掩盖运行时不匹配。

## CocoaPods 插件

### `undefined method manage_flutter_module`

原因：`cocoapods-smart-pod` 没被当前 `pod` 进程加载。常见场景是插件装在全局 RubyGems，但命令使用 `bundle exec pod` 的隔离环境。

检查：

```bash
which ruby; which gem; which pod
gem env home
pod plugins installed
gem list '^cocoapods-smart-pod$' -a
```

让 `pod`、smart-pod、hooks 进入同一 Ruby/GEM_HOME 后重试。不要删除 `manage_flutter_module` 或改成硬编码 Flutter framework。

### smart-pod 报缺少 `cocoapods-hooks`

`Gemfile` 可能从私有 GitLab获取 hooks，而 smart-pod 是本地 `.gem`。Bundler 下载成功不等于全局 `pod` 能看到 hooks。将 hooks 安装到运行 `pod` 的同一个 gem 环境，或把两个插件都纳入同一 bundle，再用 `pod plugins installed` 验证。

### CocoaPods 版本不一致

症状：提示 lockfile 由另一 CocoaPods 版本生成，或安装后产生巨大 `Podfile.lock` diff。

比较 `Gemfile` 与 `Podfile.lock` 末尾的 `COCOAPODS`。先报告仓库自身的冲突；需要切版本时用 `pod _<version>_ install`，不要未经确认更新锁文件。

## Specs 与网络

### 私有 spec 源无法 clone

源：`ssh://git@192.168.31.7:7999/swclien/modulespecs.git`

- timeout / no route：连接公司网络或 VPN。
- host key verification failed：核对主机后添加 port 7999 的 host key。
- permission denied：修复 SSH key 或申请仓库/Group 权限。

### 公共 Specs 镜像长时间卡住

Podfile 可能使用清华完整 Specs Git 镜像。首次 clone 体积大；先检查 repo 目录是否增长，不要无限等待无进展进程。

切换 CDN 会改 Podfile，并可能遇到 GitHub raw 429。仅在用户同意下临时切源；记录并恢复改动。不要同时留下半成品 Git repo 与 CDN 配置。429 是网络限流，换 CocoaPods 小版本通常无效。

## GitLab 权限

### 依赖安装时逐个出现 repository not found

不要继续反复执行 `pod install` 或 `pub get`。在已选 release 上运行 `scripts/scan_gitlab_access.rb --check <workspace>`，一次列出主仓、submodule、Pod/Gem 和 Flutter lockfile 传递依赖。只为失败项申请仓库或上级 Group 权限，成功项不重复申请。

HTTPS origin 可读不代表 SSH git 依赖可读。g0 manifests 大量使用 `git@gitlab.addx.ai:...`，必须验证 SSH key 与 SSH 仓库权限。

## Flutter add-to-app

### Flutter 模块找不到

确认 `g0-flutter-module` 与 `g0-ios` 同级，且本地没有设置会让 Podfile切到 Git source 模式的 CI 环境变量。

### 三仓 worktree 布局或分支错误

症状包括 g0-ios 仍指向主 Flutter/SDK、`git status` 显示 detached HEAD，或某仓修改在当前构建中不生效。

使用 `scripts/create-ios-worktrees.sh` 重新建立 `<group>/g0-ios`、`<group>/g0-flutter-module`、`<group>/smartdevicecoresdk-ios`。逐仓运行 `git symbolic-ref --short HEAD`，并用 `pwd -P` 核对 g0-ios 的两个兄弟路径。不要复制 `.dart_tool`、`.ios`、`.flutter-plugins-dependencies`、Pods 或 DerivedData。

### worktree submodule 为空或重新下载

IJK/A4xLiveSDK 体积较大。先在主仓初始化 submodule 并确认目标 base 所需 commit 存在，再用 `create-ios-worktrees.sh` 通过 `--reference` 复用主仓 `.git/modules` 对象库。不要在 worktree 中直接运行裸 `git submodule update --init --recursive`；Podfile 会执行递归更新，必须在 `pod install` 前把 worktree submodule 准备好。

### Flutter 版本或缓存不一致

读取 `.fvmrc` 后执行：

```bash
fvm install
fvm flutter clean
fvm flutter pub get
```

确保调用 smart-pod 的 PATH 中 `flutter` 指向该 FVM 版本；某些插件内部调用裸 `flutter`，只执行 `fvm flutter ...` 不足以保证 Pod 阶段版本正确。

## 签名与设备

### 没有 Apple Development identity

运行 `security find-identity -v -p codesigning`。若没有有效 `Apple Development:` identity，提示使用者咨询 iOS 开发同学，并从[团队证书与签名文档](https://a4x-paas.feishu.cn/wiki/BS8QwCVxCibFerkg5V0cn5tknyc)下载、导入当前适用的证书和签名材料。只有证书文件但没有对应 private key 仍不可签名。模拟器可以继续，真机必须停止。

### Profile 不包含当前设备

示例：`Provisioning profile ... doesn't include the currently selected device`。

先判断 destination：

- 只开发 iOS App：切到 iPhone 模拟器或已注册 iPhone，不要误选“本机 Mac”。
- Apple Silicon Mac 运行 iOS App：在 Apple Developer 注册该 Mac UDID，重新生成并安装包含它的 profile。
- 真机：注册 iPhone UDID，确认 Team、Bundle ID、开发证书和 profile 一致。

项目允许时可启用 Automatically manage signing。若项目不支持 Mac，移除 Mac (Designed for iPad) / Catalyst destination，避免 Xcode 自动选错目标。不要通过禁用签名来解决真机安装。

### Profile 不包含当前签名证书

症状：`Provisioning profile ... doesn't include signing certificate ...`。这与“没有安装 profile”或“设备 UDID 未注册”不同。运行 `security find-identity -v -p codesigning` 核对当前 identity，然后联系 iOS 开发同学确认并重新下载匹配的共享证书、私钥和三个 target 的 development profiles。不要在 Build Settings 中强行指定一张 profile 未包含的本机证书。

## Xcode 安装与选择

### 当前 macOS 无法安装最新版 Xcode

不回退到旧版选择流程。提示用户升级 macOS 后重新执行 `scripts/install_xcode.sh --runtime`；不要从非官方来源安装修改版 Xcode。

### `xcodebuild` 指向 Command Line Tools

症状：`xcode-select -p` 返回 `/Library/Developer/CommandLineTools`，或 `xcrun` 找不到 iPhoneOS SDK。

处理：确认完整 Xcode 已位于 `/Applications`，再运行：

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
xcodebuild -version
xcrun --sdk iphoneos --show-sdk-path
```

### 多个 Xcode 选中了旧版本

重新运行 `xcodes install --latest --select`，再检查 `xcode-select -p`、`DEVELOPER_DIR` 和进程实际路径。Xcode GUI 中也确认当前打开的是最新版。

### License 或首次组件未完成

症状包括 license 未接受、缺少 `simctl`、首次构建要求安装组件。让用户阅读并接受 license，再运行 `sudo xcodebuild -runFirstLaunch`。不要自动替用户接受许可协议。

## 模拟器与 Xcode

### `simctl` 无法连接 CoreSimulator

先确认 Xcode 已启动并完成首次组件安装，运行 `xcodebuild -runFirstLaunch`（需要相应权限），再检查 `xcode-select -p`。在受限沙箱中 CoreSimulator 服务可能不可访问；区分沙箱限制与项目构建错误。

### `Unable to find a destination` 或没有可用 iPhone

运行 `xcrun simctl list runtimes` 和 `xcrun simctl list devices available`。若没有 iOS runtime，在 Xcode Settings → Components 安装；若 runtime 存在但没有设备，从 Xcode 的 Devices and Simulators 创建兼容设备。构建命令应使用本机实际存在的名称或 UDID，不硬编码 `iPhone 15`。

### 真机系统版本高于 Xcode device support

这不是 provisioning profile 问题。根据 Apple 兼容表升级到支持该设备 OS 的 Xcode，且先确认当前 macOS 能运行该 Xcode；否则改用受支持设备/模拟器。

### workspace 无法列出 scheme

若 `AddxAi.xcworkspace` 不完整或 `xcodebuild -list` 失败，先确认 `Pods/` 和 `pod install` 是否完整。不要改用 `.xcodeproj` 绕过 Pods。

## 磁盘与工作区变更

### 安装或编译出现无关错误

`No space left on device` 可能表现为 Homebrew lock、Pods clone、DerivedData 或编译缓存错误。先运行 `df -h /` 与 `du` 定位；优先清理可再生成的下载/构建缓存，删除大型跨项目缓存前先征得用户同意。

### `pod install` 后出现用户未预期改动

Podfile 会递归初始化 submodule、设置 hooks，插件也可能更新 lockfile/工程文件。它还会根据 `ProjectConfig.plist` 的 `is_use_voip` 直接删除 `AppDelegate+Injection.swift` 中的 VOIP 行。对比运行前后的 `git status --short` 和 diff，先判断配置是否正确，保留用户既有改动；不要把所有变化一并回滚或提交。

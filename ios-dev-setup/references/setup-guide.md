# g0 iOS 环境搭建

## 目录

- [首次运行决策](#首次运行决策)
- [安装 Xcode](#安装-xcode)
- [检查 GitLab 权限](#检查-gitlab-权限)
- [仓库与分支](#仓库与分支)
- [版本和环境发现](#版本和环境发现)
- [安装依赖](#安装依赖)
- [编译与运行](#编译与运行)
- [worktree 并行开发](#worktree-并行开发)

## 首次运行决策

先确认三项：

1. App：`vico` / `kb` / `nature`
2. 版本：指定版本或最新 release
3. 目标：iPhone 模拟器 / iPhone 真机 / Apple Silicon Mac

release 前缀沿用 g0 家族约定：VicoHome=`VH`、Kiwibit=`KB`、VicoNature=`VN`。通过 `glab` 或 `git branch -r` 分别查询三个仓库；不要从 Android 分支名推断 iOS，也不要假设三仓完全同名。SDK 可能由 Host 的本地 `:path` 引入，此时它当前 checkout 的分支就是实际参与编译的代码。

```bash
PREFIX=<VH|KB|VN>
glab api "projects/SWCLIEN%2Fg0-ios/repository/branches?search=release/${PREFIX}_" --paginate \
  | jq -r '.[].name' | rg "^release/${PREFIX}_" | sort -V | tail -1
glab api "projects/SWCLIEN%2Fg0-flutter-module/repository/branches?search=release/${PREFIX}_" --paginate \
  | jq -r '.[].name' | rg "^release/${PREFIX}_" | sort -V | tail -1
glab api "projects/SWCLIEN%2Fsmartdevicecoresdk-ios/repository/branches?search=release/${PREFIX}_" --paginate \
  | jq -r '.[].name' | rg "^release/${PREFIX}_" | sort -V | tail -1
```

## 安装 Xcode

### 1. 直接安装最新版

不读取 `.xcode-version`、CI/Jenkins pin、`LastUpgradeCheck` 或兼容表来选择版本。直接运行 skill 内安装脚本，安装 Apple 当前最新版 Xcode：

```bash
~/.codex/skills/ios-dev-setup/scripts/install_xcode.sh --runtime
```

脚本安装 `xcodes` CLI，并执行 `xcodes install --latest --select`，从 Apple Developer 下载最新版，安装到 `/Applications`、立即选择最新版、执行 first-launch 组件安装并验证 SDK/runtime。它不会读取或打印 Apple 密码；Apple Account、2FA、macOS sudo 密码和 license 由用户在交互提示中处理。不要把 `XCODES_PASSWORD` 或 session cookie 写入命令、日志或 skill。

只从 Apple 官方分发源取得 Xcode。若最新版无法在当前 macOS 安装，安装失败后再提示升级 macOS；不要回退到版本选择流程。

安装前只检查剩余磁盘，不做 Xcode 版本判断。Xcode、解压副本、Simulator runtimes 与 DerivedData 都占用大量空间；根据下载页显示的实际大小预留空间，不在 skill 中硬编码容量。

### 2. 验证 Developer 目录与首次启动

完整 Xcode 自带构建 iOS 所需工具；`xcode-select --install` 安装的 standalone Command Line Tools 不能替代完整 Xcode。安装脚本会选择最新安装版本，再验证：

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
xcode-select --print-path
xcodebuild -version
```

首次安装后打开一次 Xcode，让用户阅读并接受 license；随后安装系统组件：

```bash
sudo xcodebuild -runFirstLaunch
```

不要代表用户静默执行 `xcodebuild -license accept`。license 尚未接受时，让用户在 Xcode 或 `sudo xcodebuild -license` 中完成。

### 3. 安装 iOS 平台与 Simulator runtime

在 Xcode → Settings → Components 安装项目需要的 iOS 平台/Simulator runtime。也可使用 Apple 支持的命令下载当前 Xcode 可用的 iOS 平台：

```bash
xcodebuild -downloadPlatform iOS
```

runtime 是否可用取决于最新版 Xcode；按 Components 中实际列表安装，不降级 Xcode。

验证安装闭环：

```bash
xcodebuild -version
xcode-select -p
xcrun --sdk iphoneos --show-sdk-version
xcrun simctl list runtimes
xcrun simctl list devices available
```

只有以上命令都指向最新版 Xcode、存在 iOS SDK 和至少一个可用模拟器后，才继续安装 Flutter 与 Pods。

## 检查 GitLab 权限

先 checkout/确认目标 release 的依赖声明，再运行：

```bash
~/.codex/skills/ios-dev-setup/scripts/scan_gitlab_access.rb --check /path/to/IosProjects
```

脚本读取三个主仓 origin、`.gitmodules`、Podfile/Podfile.lock、Gemfile/Gemfile.lock、fastlane Pluginfile、pubspec.yaml/pubspec.lock，去重后逐项执行无交互的 `git ls-remote`。任何仓库失败都先申请对应仓库或 Group 权限；不要等到 `pod install` / `pub get` 中途再逐个发现。

完整清单、当前分支已知依赖和失败分类见 [access-and-signing.md](access-and-signing.md)。

## 仓库与分支

默认布局：

```text
IosProjects/
├── g0-ios/
├── g0-flutter-module/
└── smartdevicecoresdk-ios/
```

`g0-ios/Podfile` 默认以 `../g0-flutter-module` 和 `../smartdevicecoresdk-ios` 引用本地模块。Podfile 在 CI 环境变量存在时可能切为 Git source 模式；本地开发不要无意设置 `parameters_flutter_module` 或 `parameters_sdk_module`。

新环境按三个已确认的 base 分支同级克隆，并立即验证分支不是 detached：

```bash
git clone --single-branch --branch "$IOS_BASE" git@gitlab.addx.ai:SWCLIEN/g0-ios.git
git clone --single-branch --branch "$FLUTTER_BASE" git@gitlab.addx.ai:SWCLIEN/g0-flutter-module.git
git clone --single-branch --branch "$SDK_BASE" git@gitlab.addx.ai:SWCLIEN/smartdevicecoresdk-ios.git
test "$(git -C g0-ios symbolic-ref --short HEAD)" = "$IOS_BASE"
test "$(git -C g0-flutter-module symbolic-ref --short HEAD)" = "$FLUTTER_BASE"
test "$(git -C smartdevicecoresdk-ios symbolic-ref --short HEAD)" = "$SDK_BASE"
git -C g0-ios submodule update --init --depth=1 MediaCodec/MediaCodec/ijk
git -C smartdevicecoresdk-ios submodule update --init --depth=1 SmartDeviceCoreSDK/Source/SmartLiveSDK/SmartWebRTC/A4xLiveSDK
```

已克隆仓库不要只执行 `pull`。三仓分别 `git fetch --prune origin`，再 `git switch "$BASE"`；本地分支不存在时用 `git switch --track -c "$BASE" "origin/$BASE"`。最后逐仓运行 `git symbolic-ref --short HEAD` 和 `git status --short`，detached HEAD 或已有改动都先处理再装依赖。

## 版本和环境发现

先执行统一的只读预检；它不会读取 Fastlane secret，也不会修改仓库：

```bash
~/.codex/skills/ios-dev-setup/scripts/preflight_ios_env.sh /path/to/IosProjects
```

先读源文件，再安装工具：

```bash
xcodebuild -version
xcode-select -p
xcrun --sdk iphoneos --show-sdk-version
xcrun simctl list devices available
cat g0-flutter-module/.fvmrc
tail -n 12 g0-ios/Gemfile.lock
rg 'cocoapods' g0-ios/Gemfile
tail -n 4 g0-ios/Podfile.lock
rg '^platform :ios' g0-ios/Podfile
df -h /
```

规则：

- 用 `.fvmrc` 的 Flutter 版本，不从旧对话或另一平台复制版本。
- `xcode-select -p` 必须指向完整 Xcode 的 `Contents/Developer`，而不是仅 `/Library/Developer/CommandLineTools`。
- 用 `Gemfile.lock` 的 `BUNDLED WITH` 选择 Bundler。
- 比较 `Gemfile` 的 CocoaPods pin 与 `Podfile.lock` 的生成版本。若冲突，报告冲突；优先避免重写 lockfile，除非用户确认升级/降级。
- 系统 Ruby 过旧时安装 Homebrew Ruby，并确保 `ruby`, `gem`, `bundle`, `pod` 使用同一前缀。
- 首次 Pods 安装前保留足够磁盘；完整依赖和编译需要数 GB 空间。

## 安装依赖

### 1. Flutter 模块

```bash
cd g0-flutter-module
fvm install
fvm flutter clean   # 仅在首次、切版本或出现 stale 产物时
fvm flutter pub get
```

私有 Git/pub 依赖失败时先修复 GitLab SSH、Nexus 权限或网络，不要把依赖改成本地 vendor 来绕过权限。

### 2. Ruby 与 Bundler

将 Homebrew Ruby 放在 PATH 前面，gem bin 路径从 `gem environment user_gemhome` / `gem env home` 动态获得，不硬编码 Ruby ABI 目录。

```bash
cd ../g0-ios
RUBY_PREFIX=$(brew --prefix ruby)
export PATH="$RUBY_PREFIX/bin:$(ruby -e 'print Gem.bindir'):$PATH"
ruby -v
bundle -v
bundle install
```

若 lockfile 要求指定 Bundler，先安装并显式执行：

```bash
BUNDLER_VERSION=$(awk '/BUNDLED WITH/{getline; gsub(/^ +/, ""); print}' Gemfile.lock)
gem install bundler -v "$BUNDLER_VERSION"
bundle "_$BUNDLER_VERSION" install
```

### 3. g0 CocoaPods 插件

Podfile 依赖：

- `cocoapods-smart-pod`：定义 `manage_flutter_module` / `smart_pod`
- `cocoapods-hooks`：Pod hook

执行仓库脚本后验证，不假设安装成功：

```bash
bash install_pod_plugins.sh
pod plugins installed
gem list '^cocoapods-smart-pod$' -a
gem list '^cocoapods-hooks$' -a
```

关键是运行 `pod` 的 Ruby 能同时加载两个插件。若 `bundle exec pod` 隔离了全局 smart-pod，改用与插件相同 Ruby 下的 `pod`，或把插件纳入同一 bundle；不要编辑 Podfile 删除插件调用。

### 4. 私有 specs 和 Pods

先验证私有源：

```bash
git ls-remote ssh://git@192.168.31.7:7999/swclien/modulespecs.git HEAD
```

首次连接如提示 host authenticity，核对内网主机后将 `192.168.31.7:7999` host key 加入 `~/.ssh/known_hosts`，再重试。随后使用已确认版本的 CocoaPods：

```bash
SKIP_POD_PLUGIN_INSTALL=1 pod install
```

Podfile 会运行 recursive submodule 初始化并设置 `.githooks`。运行前后查看 `git status --short`，识别 `Podfile.lock`、submodule 和工程文件的变化。

它还会读取 `ProjectConfig.plist` 的 `is_use_voip`，现有实现可能直接改写 `AddxAi/AppDelegate+Injection.swift`。先确认品牌/环境/VOIP 配置，并在安装后单独审查该文件 diff；详情见 [project-config-and-build.md](project-config-and-build.md)。

## 编译与运行

Pods 成功后只打开 workspace：

```bash
open AddxAi.xcworkspace
xcodebuild -list -workspace AddxAi.xcworkspace
```

默认主 scheme 为 `AddxAi`。先用模拟器验证编译，destination 名称按本机实际可用设备替换：

```bash
xcrun simctl list devices available
xcodebuild \
  -workspace AddxAi.xcworkspace \
  -scheme AddxAi \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=<available iPhone>' \
  build
```

模拟器编译通过不代表真机签名已配置。若目标是真机或 Apple Silicon Mac，继续前必须提示使用者咨询 iOS 开发同学，并从[团队证书与签名文档](https://a4x-paas.feishu.cn/wiki/BS8QwCVxCibFerkg5V0cn5tknyc)下载、导入开发同学确认的证书和签名材料，再按 [access-and-signing.md](access-and-signing.md) 完成签名门禁；缺少材料时不要用禁用签名、改 Bundle ID 或删除 capabilities 绕过。

签名/Archive 还涉及主 App 与两个 Push Extension，以及本地敏感 `fastlane_sign.plist`；按 [project-config-and-build.md](project-config-and-build.md) 分阶段处理，不为首次模拟器构建运行会修改远端状态的 Fastlane lane。

## worktree 并行开发

多需求并行时三仓必须在同一 group 目录内成组创建，不使用 detached worktree：

```text
<worktree-base>/<feature>/g0-ios
<worktree-base>/<feature>/g0-flutter-module
<worktree-base>/<feature>/smartdevicecoresdk-ios
```

先在三个主仓执行 `git fetch --prune origin`，并保证主仓的两个 submodule 已初始化、包含目标 base 所需 commit。配置 worktree 根目录后运行：

```bash
WT_CONFIG_DIR=$HOME/.config/dev-config
WT_CONFIG=$WT_CONFIG_DIR/g0-worktree.yaml
# 配置缺失时先询问用户；默认值为 ~/work-tree
WT_BASE=$(awk -F': *' '/^worktree_base:/ {print $2}' "$WT_CONFIG" 2>/dev/null | sed "s|~|$HOME|")
[ -n "$WT_BASE" ] || WT_BASE=$HOME/work-tree

FEATURE=<需求名>
NEW_BRANCH="feat/$FEATURE"
GROUP_DIR="$WT_BASE/$FEATURE"

bash <skill-dir>/scripts/create-ios-worktrees.sh \
  <g0-ios主仓> <g0-flutter-module主仓> <smartdevicecoresdk-ios主仓> \
  "$IOS_BASE" "$FLUTTER_BASE" "$SDK_BASE" "$NEW_BRANCH" "$GROUP_DIR"
```

脚本会：

- 从三仓各自 base 创建同名本地特性分支，并验证全部非 detached。
- 用主仓 `.git/modules` 作为 `--reference` 初始化 IJK/A4xLiveSDK，主仓缺对象时在创建前阻塞，避免重新下载大仓库。
- 仅在主仓存在时复制 gitignored 的 `fastlane_sign.plist`，权限设为 `600`，且不输出内容。
- 验证 g0-ios 默认兄弟路径确实指向同组 Flutter 和 SDK；默认不安装依赖，避免绕过权限门禁。

创建后先运行 `scan_gitlab_access.rb --check "$GROUP_DIR"`。权限通过后在新 Flutter worktree 执行 `fvm flutter pub get`；不要复制 `Pods`、`.dart_tool`、`.ios`、`.flutter-plugins-dependencies`、`build` 或 `DerivedData`。确需让脚本一并准备 Flutter 时才显式设置 `G0_PREPARE_FLUTTER=1`，且应已提前在目标 manifests 上完成权限检查。

脚本不是事务性的；中途失败时先用 `git worktree list` 核对已创建的精确路径和分支，不自动删除或覆盖。

完成本地联调后，检查 Podfile/pubspec 中临时 `:path` 依赖是否应恢复为 Git ref，再提交。

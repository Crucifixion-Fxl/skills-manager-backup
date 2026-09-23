---
name: ios-dev-setup
description: Use when directly installing the latest Xcode, setting up, building, or running the g0-ios host app with g0-flutter-module; checking required GitLab repository and dependency permissions; obtaining or validating an Apple Development certificate and provisioning access; selecting VicoHome/Kiwibit/VicoNature release branches and Xcode schemes; preparing simulator runtimes, Ruby, Bundler, CocoaPods, private specs, and g0 CocoaPods plugins; creating parallel g0 iOS worktrees; or diagnosing g0-specific Pod, Flutter add-to-app, simulator, code-signing, provisioning-profile, and device-install errors. Triggers on "安装 Xcode"、"搭建 iOS 环境"、"iOS 首次运行"、"g0-ios 跑起来"、"GitLab 权限"、"开发证书"、"pod install 失败"、"manage_flutter_module"、"iOS 签名"、"Provisioning profile"、"g0 iOS worktree".
---

# iOS Dev Setup

搭建并排查 `g0-ios`（集成 `g0-flutter-module`）开发环境。通用 Homebrew、Xcode 和 CocoaPods 知识由 Codex 自行处理；本 skill 只规定 g0 的版本来源、仓库布局、私有依赖、插件链和签名陷阱。

## 版本来源

| 组件 | 规则 | 来源 |
|---|---|---|
| Xcode | 直接安装并选择 Apple 当前最新版，不读取项目 pin、不做版本判断 | `xcodes install --latest --select` |
| iOS deployment target | 读取 Podfile 和各 build configuration;发现不一致时保留项目现状并报告 | `Podfile`, `project.pbxproj` |
| Flutter | 使用 FVM 安装项目声明版本;切版本后清理旧产物 | `g0-flutter-module/.fvmrc` |
| Ruby / Bundler | 使用新 Ruby 运行锁定的 Bundler;系统 Ruby 过旧时使用 Homebrew Ruby | `Gemfile.lock` 的 `BUNDLED WITH` |
| CocoaPods | 比较 `Gemfile` pin 与 `Podfile.lock` 的 `COCOAPODS`;不静默改 lockfile | `Gemfile`, `Podfile.lock` |

## 核心规则

1. **先确认 app 与分支** — 询问 `vico` / `kb` / `nature`;分别在 `g0-ios`、`g0-flutter-module`、`smartdevicecoresdk-ios` 查询并确认目标分支，不假设三仓分支名或版本号相同。
2. **直接安装最新版 Xcode** — 不检查项目 pin 或兼容表，直接运行 `scripts/install_xcode.sh --runtime`，完成最新版下载、安装、选择和验证；只让用户接管 Apple 登录/2FA、sudo 和 license。
3. **完整 Xcode 才能构建 iOS** — standalone Command Line Tools 不能替代 Xcode；安装后选择 Developer 目录、完成 first launch，并安装所需 iOS Simulator runtime。
4. **先过 GitLab 权限门禁** — 对已选 release 运行 `scripts/scan_gitlab_access.rb --check <workspace>`；主仓、submodule、Pod/Gem 和 Flutter git 依赖全部可读后再装依赖。
5. **仓库保持同级** — `g0-ios`, `g0-flutter-module`, `smartdevicecoresdk-ios` 默认是兄弟目录；Podfile 的本地 `:path` 依赖依赖此布局。
6. **Flutter 版本以 `.fvmrc` 为准** — 不复用历史对话里的版本号；执行 `fvm flutter pub get`，切版本后执行 `fvm flutter clean`。
7. **Pods 插件必须与执行 `pod` 的 Ruby 环境一致** — `cocoapods-smart-pod` 提供 `manage_flutter_module`;`cocoapods-hooks` 也必须对同一个 GEM_HOME 可见。
8. **不要为绕过插件错误修改 Podfile** — `undefined method manage_flutter_module` 是插件未加载，不是 Podfile API 错误。
9. **私有 specs 需要公司网络和 SSH 信任** — 从当前 Podfile 动态读取私有 specs 地址；先验证连接、host key 与权限，不在核心 skill 或脚本中硬编码内部主机。
10. **真机前咨询开发同学获取签名材料** — 模拟器无需证书；真机或 Apple Silicon Mac 签名前，提示使用者咨询 iOS 开发同学，并从团队飞书文档下载证书和签名材料后按指导导入。
11. **打开 workspace，不打开 project** — Pods 完成后使用 `AddxAi.xcworkspace`，scheme 通常为 `AddxAi`。
12. **保护用户改动** — Podfile 会初始化 submodule、设置 hooks，并可能改源文件；运行前后都检查 `git status --short`，不要覆盖已有改动。
13. **先识别项目配置** — 只读取 `ProjectConfig.plist` 的安全字段确认品牌、环境和 VOIP；`pod install` 可能据此修改 `AppDelegate+Injection.swift`。
14. **签名覆盖三个 target** — `AddxAi`、`AddxPushContent`、`AddxPushService` 都要使用开发同学确认的 Team、证书与 profile；不要自行创建或替换团队签名材料。
15. **拉取后必须切分支并验证** — 三仓分别 `fetch --prune` 后 `switch` 到已确认的 base；`git symbolic-ref --short HEAD` 不得为空。
16. **iOS worktree 必须三仓成组** — 固定为 `<group>/g0-ios`、`<group>/g0-flutter-module`、`<group>/smartdevicecoresdk-ios`；复用主仓 submodule 对象，权限扫描通过后在新 Flutter worktree 重建产物，禁止复制生成目录或重新下载大 submodule。

## 执行流程

| 阶段 | 要点 | 详情 |
|---|---|---|
| 首次运行 | 先问 app/版本/模拟器或真机，再查三仓目标分支并执行只读预检 | [setup-guide.md](references/setup-guide.md) |
| Xcode 安装 | 直接安装并选择最新版；完成 Developer 目录、first launch、Simulator runtime | 同上 |
| 权限预检 | 动态扫描主仓/submodule/Pod/Gem/Flutter GitLab 依赖并逐项验证 | [access-and-signing.md](references/access-and-signing.md) |
| 环境发现 | 验证 Xcode/SDK/模拟器，再检查 FVM、Ruby/Bundler、Pods 与 scheme | 同上 |
| 依赖安装 | Flutter pub → Ruby gems → g0 Pod 插件 → private specs → Pods | 同上 |
| 编译运行 | 使用 workspace + `AddxAi` scheme;先模拟器编译，再按需真机签名 | 同上 |
| 项目配置 | 确认品牌/环境/VOIP，区分模拟器、真机和 Archive，保护 Fastlane secret | [project-config-and-build.md](references/project-config-and-build.md) |
| 证书门禁 | 真机前咨询 iOS 开发同学，从团队飞书文档下载并导入证书和签名材料，再验证 identity、UDID 和 profile | [access-and-signing.md](references/access-and-signing.md) |
| worktree 开发 | 用配套脚本建立三仓同名分支、复用 submodule 并验证兄弟路径；权限通过后重生 Flutter 产物 | 同上 |
| 故障排查 | Ruby PATH、插件可见性、Specs/CDN、签名、CoreSimulator、磁盘 | [troubleshooting.md](references/troubleshooting.md) |

## References

- **[setup-guide.md](references/setup-guide.md)** — Xcode 安装、分支选择、版本发现、仓库布局、依赖安装、workspace 构建与 worktree
- **[access-and-signing.md](references/access-and-signing.md)** — GitLab 权限动态检查、当前依赖清单、开发证书与真机签名门禁
- **[troubleshooting.md](references/troubleshooting.md)** — CocoaPods 插件、私有源、公共 Specs、签名、模拟器和磁盘问题
- **[project-config-and-build.md](references/project-config-and-build.md)** — 三仓关系、ProjectConfig 副作用、三个签名 target 与 Fastlane 签名边界

## Scripts

- `scripts/install_xcode.sh` — 直接安装并选择最新版 Xcode；`--runtime` 同时安装 iOS runtime，`--dry-run` 用于审查命令
- `scripts/scan_gitlab_access.rb` — 扫描依赖清单；加 `--check` 后用只读 `git ls-remote` 验证全部 GitLab 权限
- `scripts/preflight_ios_env.sh` — 只读检查三仓、Xcode、版本 pin、项目安全配置、workspace 和开发 identity
- `scripts/create-ios-worktrees.sh` — 从三仓各自 base 创建非 detached 的同名特性分支，安全复制签名配置、复用 submodule 并验证本地 `:path` 布局
- `tests/smoke_test.rb` — 无网络、无安装副作用地验证四个脚本，并覆盖三仓 worktree 成功路径、Submodule 对象门禁及签名配置 `0600` 复制

## Examples

### Bad — 混用 Ruby 环境并绕过 Podfile

```bash
bundle exec pod install  # smart-pod 不在 bundle 中，却未检查插件可见性
# 然后删除 manage_flutter_module 调用以“修复”报错
```

### Good — 先对齐版本和插件运行时

```bash
cat ../g0-flutter-module/.fvmrc
tail -n 8 Gemfile.lock
tail -n 4 Podfile.lock
pod plugins installed
# 确认 pod、smart-pod、hooks 属于同一 Ruby/GEM_HOME 后再 pod install
```

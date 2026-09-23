---
name: android-dev-setup
description: Use when setting up g0-android + g0-flutter-module build environment, choosing and switching to vico/kb/nature release branches, creating paired non-detached g0 worktrees, linking Android/FlutterBoost to the matching Flutter worktree, selecting g0 flavors/build variants, or hitting g0-specific build/GitLab dependency permission errors. Triggers on "搭建 Android 环境"、"首次运行"、"首次编译"、"切换 release 分支"、"g0 flavor"、"g0 worktree"、"g0 多端并行"、"FlutterBoost 路径".
---

# Android Dev Setup

g0-android(集成 g0-flutter-module)开发环境搭建与构建排查。通用工具安装/使用 AI 已知,本 skill 只写 g0 特有的版本来源与坑。

## 版本来源

| 组件 | 规则 | 来源 |
|---|---|---|
| Android Studio | 安装;用于 Gradle Sync、SDK Manager、设备运行与调试 | 安装当前稳定版 |
| JDK | 由 AGP 决定:AGP 8.x → JDK 17,AGP 7.x → JDK 11 | `gradle/libs.versions.toml` 的 `agp` 字段 |
| Flutter | g0 policy pin **3.24.5**;`.fvmrc` 可能更高,不可作为 SSOT | g0 团队策略 |
| NDK | 从项目配置收集,多模块可能需多个 NDK,全部装 | `gradle.properties` / `*.gradle` 的 `ndkVersion` |
| Gradle | 使用 wrapper 指定版本 | `gradle/wrapper/gradle-wrapper.properties` 的 `distributionUrl` |

## 核心规则

1. **安装 Android Studio** — 同时配置 SDK Manager 和 Gradle JDK
2. **JDK 由 AGP 决定** — 先读 `agp`;AGP 8.x 用 JDK 17,AGP 7.x 用 JDK 11
3. **Flutter 用 FVM 管理** — 使用 g0 policy pin 3.24.5;切版本后必须 `fvm flutter clean`
4. **首次运行先选 app** — 内部别名 `vico`/`kb`/`nature` 分别对应 `release/VH_4.x.x`、`release/KB_2.x.x`、`release/VN_1.x.x`
5. **不要设 `ndk.dir`** — `local.properties` 只写 `sdk.dir`,AGP 自动选 NDK
6. **`g0-android` 与 `g0-flutter-module` 同级** — `FLUTTER_PATH` 默认 `../g0-flutter-module`;否则 `-PFLUTTER_PATH=` 指定
7. **首次编译关 offline** — `gradle.properties` 常设 `org.gradle.offline=true`,首编须 `false` 联网 resolve
8. **worktree 禁止 clone submodule** — ijk/A4xLiveSDK 只用主仓 alternates 引用,严禁 `submodule update --init` 从远端拉
9. **拉取后必须切分支并验证** — 两仓分别 `switch` 到已确认的 release/特性分支;`git symbolic-ref --short HEAD` 不得为空
10. **Android/Flutter worktree 必须成对** — 固定为 `<pair>/g0-android` + `<pair>/g0-flutter-module`;在该 Flutter worktree 重跑 `fvm flutter pub get`,禁止复制主仓 `.android`
11. **Gradle Sync 前检查 Git 权限** — 用 `scripts/check-git-access.sh` 验证两仓 remote、Android submodule 和 Flutter Git 依赖;任一失败就先申请权限

## 执行流程

| 阶段 | 要点 | 详情 |
|---|---|---|
| 首次运行 | 先问用户要装 `vico` / `kb` / `nature`;用 `glab` 找对应最新 release 分支 | [setup-guide.md](references/setup-guide.md) |
| 版本发现 | 从项目源文件读 AGP/NDK/SDK;Flutter 按 g0 policy pin | [setup-guide.md](references/setup-guide.md) |
| 环境安装 | Android Studio → JDK → FVM+Flutter → Android SDK+NDK(只装缺失的) | 同上 |
| 克隆仓库 | `g0-android` + `g0-flutter-module` 同级;内部依赖按需 worktree | 同上 |
| Git 权限预检 | Gradle Sync 前扫描并 `git ls-remote` 所有主仓/submodule/Flutter Git 依赖 | 同上 |
| worktree 开发 | 用配套脚本建立非 detached 的成对 worktree,验证 Flutter 真实路径;重 submodule 用 `--reference` 复用 | 同上 |
| 编译/安装 | `project/customer_res.gradle` 动态生成 flavor;用 `-PtargetFlavor=<appId>` 运行 `assemble/install` | 同上 |
| 构建排查 | submodule/build-cache/磁盘等 g0 特有坑 | [troubleshooting.md](references/troubleshooting.md) |

## References

- **[setup-guide.md](references/setup-guide.md)** — app/release 分支选择、版本读取、环境搭建、flavor 编译/安装、worktree 开发流
- **[troubleshooting.md](references/troubleshooting.md)** — g0 构建排查(worktree submodule / build-cache / git archive)
- **`scripts/create-paired-worktrees.sh`** — 创建已切特性分支的 Android/Flutter 成对 worktree,重生 Flutter 工程并校验链接路径
- **`scripts/check-git-access.sh`** — Gradle Sync 前批量验证主仓、submodule 和 Flutter Git 依赖的访问权限
- **`scripts/tests/run-tests.sh`** — 本地回归验证权限检查、URL 解析/脱敏及 worktree 创建/回滚

## Examples

### ❌ Bad — 硬编码版本

```bash
# 硬编码 JDK 17,没读 AGP 确认 → AGP 7.x 需要 JDK 11
# Flutter 3.27.4 → 盲信 .fvmrc(g0 实际钉 3.24.5)
# local.properties 设了 ndk.dir → 报 [CXX1104]
```

### ✅ Good — 正确区分版本来源

```bash
# 读 AGP 版本 → 8.x 定 JDK 17, 7.x 定 JDK 11
# Flutter 按 g0 policy pin 3.24.5,不盲信 .fvmrc
# local.properties 只设 sdk.dir,不设 ndk.dir
```

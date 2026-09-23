# 环境搭建(g0 特有部分)

通用安装步骤(brew/sdkmanager/fvm pub get 等)AI 已知,本文档只列 g0 特有版本来源、文件路径和常见坑。

## 目录

- [首次运行: app / release 分支](#首次运行-app--release-分支)
- [版本读取](#版本读取)
- [环境安装](#环境安装)
- [克隆仓库](#克隆仓库)
- [Git 仓库权限预检](#git-仓库权限预检gradle-sync-前)
- [worktree 并行开发(推荐)](#worktree-并行开发推荐)
- [编译 / 安装 / 运行（Flavor）](#编译--安装--运行flavor)

## 首次运行: app / release 分支

初次运行先问用户要装哪个 app。内部常用别名:

| 别名 | App | Gradle `appId` / task flavor | release 分支 |
|---|---|---|---|
| `vico` | VicoHome | `vicohome` / `Vicohome` | `release/VH_4.x.x` |
| `kb` | Kiwibit | `kiwibit` / `Kiwibit` | `release/KB_2.x.x` |
| `nature` | VicoNature | `viconature` / `Viconature` | `release/VN_1.x.x` |

未指定版本时,用 `glab` 分别查 `g0-android` / `g0-flutter-module` 最新 release 分支;两仓逐个确认,不要假设一定同名:

```bash
APP=<vico|kb|nature>
case "$APP" in
  vico) PREFIX=VH; APP_ID=vicohome; TASK_FLAVOR=Vicohome ;;
  kb) PREFIX=KB; APP_ID=kiwibit; TASK_FLAVOR=Kiwibit ;;
  nature) PREFIX=VN; APP_ID=viconature; TASK_FLAVOR=Viconature ;;
  *) echo "APP must be vico, kb, or nature"; exit 1 ;;
esac

glab api "projects/SWCLIEN%2Fg0-android/repository/branches?search=release/${PREFIX}_" --paginate | jq -r '.[].name' | grep "^release/${PREFIX}_" | sort -V | tail -1
glab api "projects/SWCLIEN%2Fg0-flutter-module/repository/branches?search=release/${PREFIX}_" --paginate | jq -r '.[].name' | grep "^release/${PREFIX}_" | sort -V | tail -1

ANDROID_BASE=<上面确认的 g0-android 分支>
FLUTTER_BASE=<上面确认的 g0-flutter-module 分支>
```

## 版本读取

AGP/Gradle/NDK/SDK 从项目源文件读取;Flutter 按 g0 policy pin,不要把 `.fvmrc` 当 SSOT:

```bash
# JDK(AGP 定): AGP 8.x → 17, AGP 7.x → 11
grep '^agp' g0-android/gradle/libs.versions.toml

# Gradle
grep 'distributionUrl' g0-android/gradle/wrapper/gradle-wrapper.properties

# NDK(可能有多个,全部装)
grep 'android.ndkVersion' g0-android/gradle.properties
grep -r 'ndkVersion' g0-android/ --include="*.gradle" | grep -v build/

# Flutter: g0 policy pin 3.24.5,不盲信 .fvmrc

# compileSdk/targetSdk/minSdk
grep -A10 "versions = \[" g0-android/project/project_depslib_version.gradle
```

## 环境安装

安装当前稳定版 **Android Studio**,并配置其 SDK Manager。首次启动后:

- 在 SDK Manager 安装项目需要的 Android SDK Platform、Build-Tools、Platform-Tools、Command-line Tools 和所有收集到的 NDK
- 在 Settings → Build, Execution, Deployment → Build Tools → Gradle 中,将 Gradle JDK 设为 AGP 对应版本(AGP 8.x 用 JDK 17,AGP 7.x 用 JDK 11)
- 确认 `local.properties` 的 `sdk.dir` 指向 Android Studio 使用的 SDK 目录;不要写 `ndk.dir`

核心工具链:Android Studio → JDK(由 AGP 决定) → FVM + Flutter 3.24.5 → Android SDK + 所有收集到的 NDK。

> 切 Flutter 版本后必须 `fvm flutter clean`,否则 stale 增量产物报 `StandardFileSystem only supports file:*`。

环境变量写入用户当前 shell profile:JAVA_HOME + FVM default/bin + ANDROID_HOME + platform-tools。安装后至少验证 Android Studio 能打开 `g0-android`完成 Gradle Sync,`adb version` 可执行,SDK Manager 能看到已安装的 SDK/NDK。

## 克隆仓库

```bash
git clone --single-branch --branch "$ANDROID_BASE" git@gitlab.addx.ai:SWCLIEN/g0-android.git
git clone --single-branch --branch "$FLUTTER_BASE" git@gitlab.addx.ai:SWCLIEN/g0-flutter-module.git
test "$(git -C g0-android symbolic-ref --short HEAD)" = "$ANDROID_BASE"
test "$(git -C g0-flutter-module symbolic-ref --short HEAD)" = "$FLUTTER_BASE"
bash <skill-dir>/scripts/check-git-access.sh g0-android g0-flutter-module
git -C g0-android submodule update --init --recursive --depth=1 ThirdPartLib/ijk
```

必须同级(FLUTTER_PATH 默认 `../g0-flutter-module`)。`ThirdPartLib/ijk` 子模块需 AUD Group 权限,克隆失败是阻塞性问题。不要用全量 `--recurse-submodules` 拉所有子模块。

已克隆的仓库不要只 `pull`;两仓分别执行 `git fetch --prune origin`,再分别 `git switch "$ANDROID_BASE"` / `git switch "$FLUTTER_BASE"`;本地分支不存在时用 `git switch --track -c <base> "origin/<base>"` 创建。最后用 `git symbolic-ref --short HEAD` 验证两仓均已切分支,结果不得为空。

## Git 仓库权限预检(Gradle Sync 前)

在 submodule 初始化、`fvm flutter pub get` 和 Android Studio Gradle Sync 前执行:

```bash
bash <skill-dir>/scripts/check-git-access.sh <g0-android 路径> <g0-flutter-module 路径>
```

脚本会扫描并去重两个主仓的 `origin`、`g0-android/.gitmodules` 和 Flutter `pubspec.yaml` / `pubspec.lock` 中的 Git URL,将 `.gitmodules` 的 `./`、`../` 相对 URL 按 Android `origin` 解析后,对每个仓库执行无交互 `git ls-remote <url> HEAD`。日志中的 URL 会隐藏用户名、密码、token 以及 query/fragment,禁止输出原始凭据。

- 全部显示 `OK` 才继续 Gradle Sync
- 任一显示 `FAIL` 就停止,根据 URL 申请对应 Repository/Group 权限并确认 SSH key
- 不要通过替换依赖、修改 submodule URL 或 vendor 源码绕过权限

## worktree 并行开发(推荐)

多需求并行时用 worktree 隔离。Android 与 Flutter 必须在同一 pair 目录下成对创建,不要使用 `git worktree add --detach` 或 `g0-android-<version>` 这种无配对布局:

```bash
# 配置 worktree 根目录(首次缺失时先询问用户;默认 ~/work-tree):
WT_CONFIG_DIR=$HOME/.config/dev-config
WT_CONFIG=$WT_CONFIG_DIR/g0-worktree.yaml
# 用户确认创建后再执行:
# mkdir -p "$WT_CONFIG_DIR" && printf 'worktree_base: ~/work-tree\n' > "$WT_CONFIG"
WT_BASE=$(awk -F': *' '/^worktree_base:/ {print $2}' "$WT_CONFIG" 2>/dev/null | sed "s|~|$HOME|")
[ -n "$WT_BASE" ] || WT_BASE=$HOME/work-tree

FEAT=<需求名>
PAIR_DIR="$WT_BASE/$FEAT"
NEW_BRANCH="feat/$FEAT"

git -C <g0-android 主仓> fetch --prune origin
git -C <g0-flutter-module 主仓> fetch --prune origin
bash <skill-dir>/scripts/create-paired-worktrees.sh \
  <g0-android 主仓> <g0-flutter-module 主仓> \
  "$ANDROID_BASE" "$FLUTTER_BASE" "$NEW_BRANCH" "$PAIR_DIR"
```

脚本会创建同名本地分支并验证非 detached,复制 Android `local.properties`,在对应 Flutter worktree 运行 `fvm flutter pub get` 重生 `.android` / `.flutter-plugins-dependencies`,再校验 Android 默认 `../g0-flutter-module` 的 canonical path。任一步失败时会自动回滚本次创建的 worktree、特性分支和空 pair 目录;自动清理失败时输出人工清理命令。仅在明确要手动准备 Flutter 时才设 `G0_SKIP_FLUTTER_PREPARE=1`。

**worktree 注意事项**:
- 不得从主 Flutter 仓复制 `.android`、`.dart_tool` 或 `.flutter-plugins-dependencies`;其中包含绝对路径,会让 Android/FlutterBoost 继续指向主仓或旧 worktree
- Gradle 配置输出必须显示 `Flutter module linked from <当前 pair 的 Flutter 路径>`;否则停止构建并修正布局
- 🚨 **worktree 内禁止 clone submodule**:ijk/A4xLiveSDK 必须走主仓 alternates 引用,严禁从远端拉→详见 [troubleshooting.md](troubleshooting.md#worktree-重-submoduleijk--a4xlivesdk-复用)
- 各仓同版本分支名常不统一,建前逐仓 `git branch -a --list '*<版本>*'` 确认 `ANDROID_BASE` / `FLUTTER_BASE`

> 内部依赖(`cms_payment_plugin` 等)需本地调试时,用 worktree 同级放置,Flutter path 依赖自动生效。不需要时不拉。

## 编译 / 安装 / 运行（Flavor）

`g0-android/local.properties` 只设 `sdk.dir`(不设 `ndk.dir`)。Flutter 模块先 `fvm flutter pub get`(加 `fvm dart run build_runner build --delete-conflicting-outputs`)。若 pub get 拉 GitLab git 依赖时报仓库不存在/无权限,先开通对应仓库或 Group 权限再重试。

g0 的 flavor 是动态生成的:`project/customer_res.gradle` 先读 `-PtargetFlavor`,否则从 `assemble|install|bundle|generate + <Flavor> + Debug|Release` 的 task 名反推 `appId`,最后才退回 `addxfavor`;`app/build.gradle` 只创建当前 `"$appId"` 这一个 `productFlavors`。不要把不带 `-PtargetFlavor` 的 `:app:tasks --all` 当作完整 flavor 清单。

先确认 `APP_ID` / `TASK_FLAVOR` / `BUILD_TYPE`,再直接运行对应 task:

```bash
cd g0-android
BUILD_TYPE=Debug
# 可选:只查看当前动态 appId 对应的任务
./gradlew -PtargetFlavor="$APP_ID" :app:tasks --all | grep -E "(assemble|install)${TASK_FLAVOR}.*${BUILD_TYPE}"
# 首次/新依赖须先关 offline;这是本地临时改动,不要提交,验证后按团队习惯恢复
# 用户同意后再执行:perl -0pi -e 's/^org\.gradle\.offline=true$/org.gradle.offline=false/m' gradle.properties
# 出包
./gradlew -PtargetFlavor="$APP_ID" ":app:assemble${TASK_FLAVOR}${BUILD_TYPE}" --no-daemon
# 安装到已连接设备
./gradlew -PtargetFlavor="$APP_ID" ":app:install${TASK_FLAVOR}${BUILD_TYPE}" --no-daemon
```

> pub get 报 `Couldn't resolve` → 再跑一次(lock 落后)。
> 切版本后先 `fvm flutter clean` + `./gradlew clean`。
> appId/buildEnv 默认值见 `project/customer_res.gradle`;接口环境切换见 `project/project_properties.json` 对应 appId 的 `apiHost`。

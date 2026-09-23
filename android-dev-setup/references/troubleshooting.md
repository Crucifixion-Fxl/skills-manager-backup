# 常见问题与构建排查

## 目录

- [环境 / 配置问题](#环境--配置问题)
- [构建排查（g0 家族特有）](#构建排查g0-家族特有)

## 环境 / 配置问题

### Flutter 模块找不到

**错：** `Warning - Flutter module not found at ...`
**解：** 确认 `g0-flutter-module` 与 `g0-android` 同级，或 `-PFLUTTER_PATH=` 指定。

### worktree 使用了错的 Flutter / FlutterBoost 代码

**症状:** Android 改了当前 Flutter worktree 但不生效;Gradle 输出的 `Flutter module linked from ...` 指向主仓、旧 worktree 或 worktree 根目录。

**原因:** Android 默认只解析同级 `../g0-flutter-module`;而 Flutter 生成的 `.android`、`.dart_tool`、`.flutter-plugins-dependencies` 含本机绝对路径,复制主仓产物会保留旧指向。

**解:** 重建成 `<pair>/g0-android` + `<pair>/g0-flutter-module` 布局,在该 Flutter worktree 执行 `fvm flutter clean && fvm flutter pub get`,再从 Android worktree 运行 Gradle 并核对 `Flutter module linked from` 的 canonical path。不要复制 Flutter 主仓的生成目录。CLI 临时构建可显式传绝对路径 `-PFLUTTER_PATH=<flutter-worktree>`;长期开发仍使用成对布局,避免 Android Studio Sync 指错。

### worktree 处于 detached HEAD

**症状:** `git status --branch` 显示 `HEAD (no branch)`。

**解:** 新 worktree 统一用 `scripts/create-paired-worktrees.sh` 创建。已有 worktree 在确认无未保存改动后执行 `git switch -c <feature-branch>`;然后用 `git symbolic-ref --short HEAD` 验证结果不为空。

### Git Submodule 为空

**症状：** `ThirdPartLib/ijk/` 空，编译报 `Could not resolve project :lib_ijk-java`
**解：**
- 主仓首次初始化:申请 AUD Group 权限后 `cd g0-android && git submodule update --init --recursive --depth=1 ThirdPartLib/ijk`
- worktree 空目录:不要跑裸 `submodule update`;按下方 [worktree 重 submodule](#worktree-重-submoduleijk--a4xlivesdk-复用) 复用主仓对象

### Gradle Sync 报 Git 仓库无权限

**错:** `Permission denied (publickey)` / `Repository not found` / `Could not read from remote repository`

**解:** 先运行 `bash <skill-dir>/scripts/check-git-access.sh <g0-android> <g0-flutter-module>` 定位失败 URL。确认 SSH key 已加入 GitLab 并申请对应 Repository/Group 权限;预检全部通过后再执行 submodule、`pub get` 和 Gradle Sync。

### NDK 版本冲突

**错：** `[CXX1104] NDK version disagrees with android.ndkVersion`
**解：** 删除 `local.properties` 中的 `ndk.dir`，从错误信息提取所需版本并 `sdkmanager --install "ndk;<版本>"`

### Nexus Maven 依赖下载失败

**错：** `Could not resolve com.smartdevice.sdk:...`
**解：** 联系管理员获取凭证，在 `~/.gradle/gradle.properties` 配置 `nexusUsername` / `nexusPassword`

### Flutter pub get 拉 GitLab 依赖失败

**错：** `Repository not found` / `Permission denied (publickey)` / `Could not read from remote repository`
**解：** `pubspec.yaml` / `pubspec.lock` 里的 git 依赖可能指向内部 GitLab 仓库;先开通对应仓库或 Group 权限,确认 SSH key 可用后再 `fvm flutter pub get`。不要替换依赖或把源码 vendor 进来绕过权限。

### JDK 版本不对

**错：** `Unsupported class file major version`
**解：** 命令行改 `JAVA_HOME`；Android Studio 在 Settings → Gradle 改 Gradle JDK

### build_runner 生成失败

**症状：** `*.g.dart` 不存在
**解：** `cd g0-flutter-module && fvm flutter pub get && fvm dart run build_runner build --delete-conflicting-outputs`

---

## 构建排查（g0 家族特有）

通用 gradle/flutter 问题 AI 已知，以下只列 g0 特定的坑。

### gradle offline 模式

工作态常设 `org.gradle.offline=true`，首次/新依赖时须先 `false` 联网 resolve，再改回 `true`。

### Flutter pub get 报 `Couldn't resolve`

lock 落后于 pubspec → **再跑一次 `fvm flutter pub get`** 强制 re-resolve。

### 切 Flutter 版本后报 `StandardFileSystem only supports file:*`

dart SDK 不一致 → `fvm flutter clean` + `./gradlew clean`。

### build-cache 掩盖双端不一致

**症状：** 平时能出包，clean 后报 `Unresolved reference`。
**原因：** build-cache 跨分支命中旧产物。
**排查：** `git log --all --diff-filter=A -- <文件>` 找引入 commit，`git branch -r --contains <commit>` 看含哪些分支。
**解：** 同步 flutter 对应分支最新。

### worktree 重 submodule（ijk / A4xLiveSDK）复用

> **🚨 worktree 内严禁 `git submodule update --init` 从远端 clone submodule。** ijk/A4xLiveSDK 含大 blob，远端拉极慢且按 SHA 浅拉会被拒。必须走主仓 alternates 引用。

主仓已有完整对象（~1-2GB），复用零下载。

**优先一步法（`--reference`）：**

```bash
MAIN=<g0-android 主仓路径>; WT=<worktree 路径>; SUB=ThirdPartLib/ijk

test -d "$MAIN/.git/modules/$SUB/objects"
git -C "$WT" submodule update --init --depth=1 --reference "$MAIN/.git/modules/$SUB" "$SUB"
```

**三步法（仅在 worktree submodule gitdir 已存在但对象缺失时使用）：**

```bash
MAIN=<g0-android 主仓路径>; WT=<worktree 路径>; SUB=ThirdPartLib/ijk

WT_GITDIR=$(git -C "$WT" rev-parse --git-path modules/$SUB)
test -d "$MAIN/.git/modules/$SUB/objects"
test -d "$WT_GITDIR" || { echo "先用 --reference 一步法初始化 $SUB"; exit 1; }
test -d "$WT/$SUB" || { echo "worktree 缺 $SUB 工作目录"; exit 1; }

# 主仓缺目标 commit 时,只浅拉目标分支到共享库
git -C "$MAIN/$SUB" fetch --depth=1 origin <branch>

mkdir -p "$WT_GITDIR/objects/info"
printf '%s\n' "$MAIN/.git/modules/$SUB/objects" > "$WT_GITDIR/objects/info/alternates"

TARGET=$(git -C "$WT" rev-parse HEAD:"$SUB")
git -C "$MAIN/$SUB" cat-file -e "$TARGET^{commit}"
git -C "$WT/$SUB" checkout "$TARGET"
```

> 新增 submodule（主仓没有的，如 `ThirdPartLib/engagement`）不能 `--reference`，直接 `git submodule update --init --depth=1 <submodule>`。worktree 内禁止裸 `git submodule update --init --recursive`。

### build 自身 `git submodule update` 把 submodule 搅成空目录

worktree 内 `settings.gradle` 走 `file://` 克隆被安全策略拦（`transport 'file' not allowed`），失败时留空目录。task 非致命，只要文件在就能编。重填用 `git archive`（别 clone、别 cp -r）：
```bash
M=<主仓>; wt=<worktree>
for sm in ThirdPartLib/ijk ThirdPartLib/A4xLiveSDK; do
  pin=$(git -C "$wt" ls-tree HEAD -- "$sm" | awk '{print $3}')
  git -C "$M/$sm" cat-file -t "$pin" && git -C "$M/$sm" archive "$pin" | tar -x -C "$wt/$sm"
done
```

### 磁盘空间

完整编译峰值 ~8–10G，`No space left on device` 伪装成 transforms/cache 失败。

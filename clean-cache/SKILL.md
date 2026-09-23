---
name: clean-cache
description: 批量扫描工作区的 Flutter/Android/iOS/Node.js 项目，统计缓存占用并分级清理释放磁盘空间。当用户说"清理缓存"、"磁盘空间不足"、"clean cache"、"释放空间"时触发。
---

# 批量项目缓存清理 (Clean Cache)

## Description

开发多个 Flutter/Android/iOS 项目时，构建产物和依赖缓存会快速膨胀。本 Skill 批量扫描工作区，按安全等级分级清理，在释放空间的同时**保留全局依赖缓存**，避免下次构建重新下载。

## Rules

### Rule 0 — 安全分级（核心原则）

清理分三级，**默认只执行 L1**，L2/L3 需用户明确确认：

| 级别 | 删什么 | 重建代价 | 预计释放 |
|------|--------|---------|---------|
| **L1 安全清理** | 构建产物（build/、DerivedData、.dart_tool/、bazel-*）+ 开发工具过期缓存（Homebrew 旧版本包、Bazel 全局缓存） | 重新编译，无网络开销 | 大 |
| **L2 依赖清理** | 项目级依赖（Pods/、node_modules/、.gradle/caches/） | 需 `pod install` / `npm install` / gradle sync，但从**全局缓存**恢复，较快 | 中 |
| **L3 深度清理** | 全局缓存（~/.gradle/caches/、CocoaPods 仓库索引、Pub 全局缓存） | 所有项目首次构建都要重新下载，**很慢** | 小 |

**关键区分**：L2 删的是项目内的 `Pods/`、`node_modules/`，但**全局缓存还在**（`~/.cocoapods/repos/`、npm cache、`~/.gradle/caches/`），所以重新安装时大部分包从本地缓存恢复，不需要重新下载。

### Rule 1 — Step 1: 扫描工作区

接收用户指定的工作区根目录（默认 `~/Desktop` 或当前目录的上级），扫描所有可清理的缓存目录：

```bash
# 扫描脚本 — 找出所有缓存目录及大小
WORKSPACE="${1:-.}"

echo "=== L1 构建产物（安全删除，重编译即可）==="
# Flutter build 产物
find "$WORKSPACE" -name ".dart_tool" -type d -maxdepth 6 2>/dev/null | while read d; do du -sh "$d"; done
# Android build 产物
find "$WORKSPACE" -path "*/app/build" -type d -maxdepth 6 2>/dev/null | while read d; do du -sh "$d"; done
find "$WORKSPACE" -name "build" -path "*android*" -type d -maxdepth 6 2>/dev/null | while read d; do du -sh "$d"; done
# iOS DerivedData（全局）
du -sh ~/Library/Developer/Xcode/DerivedData/ 2>/dev/null
# Bazel 产物
find "$WORKSPACE" -name "bazel-*" -type l -maxdepth 4 2>/dev/null | while read d; do echo "$d (symlink)"; done
# Homebrew 旧版本包缓存
du -sh ~/Library/Caches/Homebrew/ 2>/dev/null
# Bazel 全局缓存
du -sh ~/Library/Caches/bazel/ 2>/dev/null
du -sh ~/Library/Caches/bazelisk/ 2>/dev/null

echo ""
echo "=== L2 项目级依赖（删后需 install，但从全局缓存恢复）==="
# iOS Pods
find "$WORKSPACE" -name "Pods" -type d -maxdepth 5 2>/dev/null | while read d; do du -sh "$d"; done
# Node modules
find "$WORKSPACE" -name "node_modules" -type d -maxdepth 5 -prune 2>/dev/null | while read d; do du -sh "$d"; done
# Android .gradle（项目级）
find "$WORKSPACE" -name ".gradle" -type d -maxdepth 4 2>/dev/null | while read d; do du -sh "$d"; done
# Flutter .flutter-plugins
find "$WORKSPACE" -name ".flutter-plugins" -type f -maxdepth 5 2>/dev/null
find "$WORKSPACE" -name ".flutter-plugins-dependencies" -type f -maxdepth 5 2>/dev/null

echo ""
echo "=== L3 全局缓存（删后所有项目都要重新下载，慎重）==="
du -sh ~/.gradle/caches/ 2>/dev/null
du -sh ~/.cocoapods/repos/ 2>/dev/null
du -sh ~/.pub-cache/ 2>/dev/null
du -sh ~/Library/Caches/CocoaPods/ 2>/dev/null
npm cache ls 2>/dev/null | wc -l | xargs -I{} echo "npm cache: {} entries"
```

输出格式 — 按项目分组的汇总表：

```
## 缓存扫描报告

| 项目 | 类型 | L1 构建产物 | L2 项目依赖 | 合计 |
|------|------|-----------|-----------|------|
| g0-ios | iOS | 0M | 288M (Pods) | 288M |
| g0-android | Android | 450M (build) | 156M (.gradle) | 606M |
| g0-flutter-module | Flutter | 36M (.dart_tool) | — | 36M |
| ... | | | | |

### 全局缓存
| 缓存 | 大小 |
|------|------|
| DerivedData | 3.2G |
| ~/.gradle/caches | 1.1G |
| ~/.cocoapods/repos | 800M |

**L1 可释放: X.X GB** | L2 可释放: X.X GB | L3 可释放: X.X GB
**总计: X.X GB**
```

### Rule 2 — Step 2: 用户确认清理级别

展示扫描报告后，询问用户：

```
推荐 L1（安全清理），预计释放 X.X GB，下次构建只需重新编译，无需重新下载。
是否也执行 L2？L2 会删除 Pods/node_modules/.gradle，但全局缓存还在，
重新 install 时大部分从本地恢复（通常 < 2 分钟）。

选择: L1 / L1+L2 / L1+L2+L3（不推荐）
```

**禁止未经确认执行 L2 或 L3。**

### Rule 3 — Step 3: 执行清理

根据用户选择执行对应级别。**每个 rm 前打印路径和大小，让用户看到在删什么。**

#### L1 清理命令

```bash
# Flutter 构建产物
find "$WORKSPACE" -name ".dart_tool" -type d -maxdepth 6 -exec rm -rf {} + 2>/dev/null

# Android build 目录（只删 app/build 和模块 build，不删源码目录）
find "$WORKSPACE" -path "*/app/build" -type d -maxdepth 6 -exec rm -rf {} + 2>/dev/null

# iOS DerivedData（所有项目共享，一次性清）
rm -rf ~/Library/Developer/Xcode/DerivedData/*

# Bazel 输出（只删符号链接指向的内容）
# bazel clean 需要在项目目录执行
find "$WORKSPACE" -name "WORKSPACE" -o -name "MODULE.bazel" -maxdepth 4 2>/dev/null | \
  xargs -I{} dirname {} | sort -u | while read proj; do
    echo "Cleaning bazel in $proj"
    (cd "$proj" && bazel clean 2>/dev/null)
  done

# Homebrew 旧版本包（只删过期下载，不影响已安装软件）
brew cleanup 2>/dev/null

# Bazel 全局缓存（编译中间产物，删后重新编译即可）
rm -rf ~/Library/Caches/bazel/ ~/Library/Caches/bazelisk/
```

#### L2 清理命令

```bash
# iOS Pods（保留 Podfile.lock，下次 pod install 会恢复一致版本）
find "$WORKSPACE" -name "Pods" -type d -maxdepth 5 -exec rm -rf {} + 2>/dev/null

# Node modules（保留 package-lock.json）
find "$WORKSPACE" -name "node_modules" -type d -maxdepth 5 -prune -exec rm -rf {} + 2>/dev/null

# Android .gradle 项目缓存
find "$WORKSPACE" -name ".gradle" -type d -maxdepth 4 -exec rm -rf {} + 2>/dev/null

# Flutter 生成文件
find "$WORKSPACE" -name ".flutter-plugins" -type f -maxdepth 5 -delete 2>/dev/null
find "$WORKSPACE" -name ".flutter-plugins-dependencies" -type f -maxdepth 5 -delete 2>/dev/null
```

#### L3 清理命令（需二次确认）

```bash
echo "⚠️  L3 会清除全局缓存，所有项目下次构建都需要重新下载依赖"
echo "确认输入 YES 继续"

# Gradle 全局缓存
rm -rf ~/.gradle/caches/

# CocoaPods 缓存
pod cache clean --all
rm -rf ~/Library/Caches/CocoaPods/

# Pub 全局缓存
flutter pub cache clean

# npm 缓存
npm cache clean --force
```

### Rule 4 — Step 4: 验证并报告

```bash
# 清理后重新扫描，对比释放的空间
echo "=== 清理完成 ==="
echo "释放空间: X.X GB"
echo ""
echo "下次构建恢复指南:"
echo "  Flutter: flutter pub get"
echo "  iOS:     cd <project> && pod install"
echo "  Android: 打开项目，Gradle 自动 sync"
echo "  Node.js: npm install"
```

### Rule 5 — 不要删的东西（红线）

| 绝对不删 | 原因 |
|---------|------|
| `Podfile.lock` / `package-lock.json` / `pubspec.lock` | 锁文件保证版本一致性，删了可能引入依赖版本变化 |
| `.gradle/wrapper/` | Gradle wrapper jar，删了 gradle 命令本身跑不了 |
| `~/.pub-cache/` (L1/L2 时) | Flutter 全局包缓存，删了所有项目都要重下 |
| `~/.gradle/caches/` (L1/L2 时) | Gradle 全局依赖缓存 |
| `~/.cocoapods/repos/` (L1/L2 时) | CocoaPods spec 仓库索引 |
| 任何 `src/`、源码文件 | 只删构建产物和缓存，绝不碰源码 |
| `.git/` | git 仓库数据 |

### Rule 6 — 常用单项目快速清理

如果用户只想清理单个项目而不是批量扫描：

```bash
# Flutter 项目
cd <project> && flutter clean && flutter pub get

# iOS 项目
cd <project>/ios && rm -rf Pods/ build/ && pod install

# Android 项目
cd <project>/android && ./gradlew clean

# 全清 DerivedData（影响所有 Xcode 项目）
rm -rf ~/Library/Developer/Xcode/DerivedData/*
```

## Examples

### Bad

```
用户: "清理一下缓存"
AI: 直接执行 rm -rf ~/.gradle/caches/ ~/.cocoapods/repos/ ~/.pub-cache/
→ 删了全局缓存，所有项目下次构建都要花 30 分钟重新下载
```

```
用户: "磁盘空间不足"
AI: 只清理了当前目录一个项目的 build/
→ 其他 10 个项目的缓存还在，释放空间微乎其微
```

### Good

```
用户: "清理一下缓存"
AI: 扫描整个工作区 → 报告 8 个项目共 4.2GB 缓存 →
    推荐 L1 释放 2.8GB（纯构建产物）→ 用户确认 →
    执行清理 → 报告实际释放 2.8GB，下次编译自动恢复
```

```
用户: "磁盘快满了，尽量多清一些"
AI: 扫描报告 L1=2.8GB, L2=1.4GB → 建议 L1+L2 →
    说明 L2 恢复方式（pod install / npm install，从全局缓存恢复约 1-2 分钟）→
    用户确认 L1+L2 → 执行 → 释放 4.2GB
```

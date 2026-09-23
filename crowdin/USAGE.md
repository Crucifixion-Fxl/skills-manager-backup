# Crowdin 拉翻译 — 团队使用指南

把"`cd crowdin && ./auto_l10n.sh config/xxx.json`" 简化成两种用法,都带网络/token/项目失效的连通性预检和"假成功"防护,跑完自动 `git diff --stat` 让你 review 变更。

## 用法 1:用 AI 自然语言(推荐)

如果你在用 Claude Code、Cursor 或其他接入了 skill 的 AI agent,**直接说人话**:

| 你说 | AI 自动做 |
|---|---|
| 帮我拉一下 KB 安卓翻译 | `pull.sh android kb_android_dev` |
| VH iOS 翻译有变化吗 | 跑 full + git diff 给你看 |
| 喂鸟器翻译同步下 | NatureHood → `pull.sh flutter f_flutter_dev` |
| 把 KB 全端翻译都更新了 | 依次跑 android / flutter / ios 三端 |
| 拉 VH flutter,但不要混 KB | 识别"不混 KB" → 走 `v_flutter_dev` 而不是 `vh_*` |
| 看下 vn ios 翻译有没有更新 | 跑 full + 给你看 git diff(不强制部署) |
| 拉 KB 翻译 | 端没说清,会反问你"哪个端?Android / Flutter / iOS" |

AI 跑完会汇报变更范围,不满意按它给的 `git checkout` 命令一键还原。**全程不用记任何命令名**。

前提:skill 已经装在 AI agent 的 skill 目录(本文档的 `~/A4x/AI/skills/skills/crowdin/`)。

## 用法 2:命令行(给 CI / 资深用户 / 没有 AI agent 的场景)

```bash
~/A4x/AI/skills/skills/crowdin/pull.sh <项目> <config 名>
```

例:
```bash
# KB Android dev 翻译
pull.sh android kb_android_dev

# VN Flutter dev 翻译(拉 VN + 合并 KB 归一化)
pull.sh flutter vn_flutter_dev

# VH iOS dev 翻译,只要 VH 不混 KB
pull.sh ios v_ios_dev
```

默认操作是 `full`(下载 → 合并 → 转换 → 部署 → 代码生成),跟你手工 `cd crowdin && ./auto_l10n.sh config/xxx.json` 完全一致。

跑完自动展示:
```
── git diff --stat lib/l10n ──
 17 files changed, 1877 insertions(+), 245 deletions(-)
  to revert: cd ~/A4x/App/g0-flutter-module && git checkout -- lib/l10n
```

不满意按 `to revert` 那行命令一键还原。

## Config 命名规范

`<app>_<platform>_<branch>.json`

| 前缀 | 含义 |
|---|---|
| `kb_*` | KiwiBit App |
| `f_*` | NatureHood (F App) |
| `v_*` | VH App,**只拉 VH,不合并 KB** |
| `vh_*` | VH App,**拉 VH 并合并 KB 归一化** |
| `vn_*` | VicoNature App,**拉 VN 并合并 KB** |

`platform`:`android` / `flutter` / `ios`
`branch`:`dev`(日常)/ `main`(发版前)

不确定有哪些 config:
```bash
pull.sh ls flutter   # 列 flutter 所有可用 config
pull.sh ls           # 列三个端的全部 config
```

## 一次性配置(每个新人)

### 1. 同步工具到三个项目根

`~/A4x/App/tools/l10n/crowdin/` 是 SoT(token、config、common/、core/、auto_l10n.sh 都以 tools/ 为准),需要先把它同步到三个项目根:

```bash
SRC=~/A4x/App/tools/l10n/crowdin
for r in g0-android g0-flutter-module g0-ios; do
  D=~/A4x/App/$r/crowdin
  [ -d "$D" ] || continue
  cp "$SRC/.env" "$D/.env"
  rsync -a --delete "$SRC/common/" "$D/common/"
  rsync -a --delete "$SRC/core/"   "$D/core/"
  cp "$SRC/auto_l10n.sh" "$D/auto_l10n.sh" && chmod +x "$D/auto_l10n.sh"
  for cfg in "$SRC"/config/*.json; do
    cp "$cfg" "$D/config/$(basename "$cfg")"
  done
done
```

> tools/ 后续如果有更新,重跑这段即可同步。

### 2. 装依赖

```bash
# Crowdin CLI
npm install -g @crowdin/cli

# Python 库
pip3 install python-dotenv

# jq(macOS)
brew install jq
```

### 3. 配 CROWDIN_TOKEN(每人自己一份)

去 Crowdin → Account Settings → API → Personal Access Tokens → 新建,勾选 `project`、`project.source.string`、`project.translation` 权限,复制 token。

```bash
# tools/.env 里替换成自己的 token(只用改 tools/,然后跑上面的同步脚本传给三个项目根)
echo 'CROWDIN_TOKEN=glpat_或你自己的_token' > ~/A4x/App/tools/l10n/crowdin/.env

# 重跑步骤 1 把新 token 同步过去
```

### 4. 检查 /etc/hosts 没有过期的 crowdin 劫持(老员工容易踩)

```bash
grep -n crowdin /etc/hosts
# 如果看到 174.129.148.70 api.crowdin.com 之类的硬编码,删掉:
# sudo sed -i '' '/api\.crowdin\.com/d' /etc/hosts
# sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
```

### 5. 验证

```bash
~/A4x/AI/skills/skills/crowdin/pull.sh flutter kb_flutter_dev probe
# 期望:✓ connectivity ok
```

## 错误码对照表

| 退出码 | 含义 | 怎么办 |
|---|---|---|
| `0` | 成功(`full` 已部署,git diff 已展示) | 在 IDE 看 diff,满意 commit;不满意按 to revert 命令还原 |
| `2` | 网络/SSL 被拦 | 检查 `/etc/hosts` 有没有过期的 crowdin 硬编码;否则切手机热点试 |
| `3` | token 失效 | tools/.env 里 token 过期,重新生成 + 重跑同步脚本 |
| `4` | project/bundle 已失效 | tools/config/*.json 里的 project_id 老了,问维护者要新值 |
| `5` | 假成功被扫描器拦下 | wrapper 输出会列出原因,大概率上面 2/3/4 之一,看具体提示 |
| `1` | 参数/前置条件错误 | 看 wrapper 输出,通常是 config 名拼错或没装依赖 |

## 子命令

| op | 行为 |
|---|---|
| `full`(默认) | download → merge → convert → deploy → 触发代码生成 |
| `deploy` | 同上去掉代码生成 |
| `diff` | 仅看变更,不部署(注意:auto_l10n.sh 的 diff 子命令会跳过 merge,可能误报"全部删除",建议直接跑 full + git diff 兜底) |
| `download` | 只下载 bundle 到 `crowdin/l10n_resources/` |
| `check` | 质量检查 |
| `probe` | 仅验证网络 + token + project,**最适合诊断** |

## 常见问题

### Q: `Code generation failed (subprocess PATH cannot find 'flutter')`

不影响翻译资源本身。手动跑代码生成:
```bash
cd ~/A4x/App/g0-flutter-module
flutter pub run intl_utils:generate
```

### Q: Wrapper 拦下 exit 5 说 "17 files reported as deleted"

这通常意味着 download 阶段实际失败了(网络/token 问题),但 `auto_l10n.sh` 没识别。先跑 `pull.sh <proj> <cfg> probe` 确认连通性,再看 wrapper 输出里的具体 ❌ 行。

### Q: 我能不能像以前一样手工跑?

可以。两种方式等价(前提是按上面"一次性配置"做完):
```bash
# 方式 A:wrapper(推荐)
pull.sh flutter kb_flutter_dev

# 方式 B:手工(原来的命令)
cd ~/A4x/App/g0-flutter-module/crowdin
./auto_l10n.sh config/kb_flutter_dev.json
```

A 比 B 多了:连通性预检、强制 non-interactive、假成功扫描、跑完自动 git diff --stat、to revert 提示。

### Q: 如何升级 wrapper 本身?

`pull.sh` 在 `~/A4x/AI/skills/skills/crowdin/`,跟着 skills 仓库走。`git pull` 即可。

## 红线

- ❌ **不要**修改任何 `crowdin/.env`(token 必须由你自己写入)
- ❌ **不要**绕过 wrapper 直接调 `auto_l10n.sh`(会失去 non-interactive 保护和假成功扫描)
- ❌ **不要**把自己的 token commit 进 git(`.env` 在 .gitignore 里,确认提交前 `git status` 看一遍)
- ✅ **要**用 `git diff` review 翻译变更后再 commit
- ✅ **要**遇到错误码 2/3/4/5 时认真看 wrapper 输出,不要硬重试

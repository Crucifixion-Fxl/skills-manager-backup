---
name: crowdin
description: Crowdin 字符串录入 + 多语言翻译拉取助手。两条主流——【录入流】当用户提到"录入 Crowdin 文案"、"往 Crowdin 写字符串"、"上传翻译"、"加几条 i18n"、"新增多语言文案"、"帮我翻译并写到 Crowdin"时触发,走 REST API 创建分支 + String + 翻译 + 导出 CSV + 可选合并 dev;【拉取流】当用户提到"拉/更新/同步翻译"、"拉文案"、"更新 i18n"、"sync l10n"、"看 crowdin 有没有更新"、"翻译到本地"、"部署翻译到项目"、"Crowdin diff",或提到具体产品+端的翻译需求("KB/VH/VN/喂鸟器/安防/NatureHood/VicoHome/VicoNature/KiwiBit + Android/iOS/Flutter")时触发,通过 ~/A4x/AI/skills/skills/crowdin/pull.sh 包装 g0-{android,flutter-module,ios}/crowdin/auto_l10n.sh 完成 download → 合并 → 格式转换 → 部署 → 代码生成,跑完自动 git diff 让用户 review。
---

## Description

覆盖 Crowdin 平台两类日常操作:

1. **录入流** — 把客户端需要翻译的文案写入 Crowdin string-based 项目,自动翻译成所有目标语言。来源可以是飞书文档、.md、Excel 或用户口述。
2. **拉取流** — 从 Crowdin 把已翻译完成的 bundle 拉回到本地项目,合并 OEM/ODM 资源,转换占位符格式,部署到项目 l10n 目录,可选触发代码生成。

适用产品线:安防摄像头 App/Web (VicoHome / KiwiBit / VicoNature)、智能喂鸟器 (NatureHood)、高尔夫私人教练。

## 路径与工具约定

**录入流**仅依赖 Crowdin REST API,无本地工具。Token 来自环境变量 `$CROWDIN_TOKEN`。

**Python HTTP 请求约定（录入流）**:本机 Python 3 存在 SSL 证书验证失败问题,`urllib` / `requests` 发 HTTPS 请求会抛 `SSLCertVerificationError`。所有录入流的 API 调用必须通过 `subprocess` 调用 `curl -k` 实现,模板如下:

```python
import subprocess, json, os

TOKEN = os.environ["CROWDIN_TOKEN"]

def api(method, path, body=None):
    args = ["curl", "-s", "-k", "-X", method,
            f"https://api.crowdin.com/api/v2{path}",
            "-H", f"Authorization: Bearer {TOKEN}",
            "-H", "Content-Type: application/json"]
    if body:
        args += ["-d", json.dumps(body)]
    result = subprocess.run(args, capture_output=True, text=True)
    return json.loads(result.stdout)
```

批量写入（如逐条录入 String + 翻译）时,把上述 `api()` 函数和所有数据放入一个 Python 脚本,用 `Bash` 工具的 `run_in_background=true` 执行,避免长时间阻塞。

**拉取流**通过 skill 自带的 wrapper `pull.sh`(同目录)调用 `~/A4x/App/g0-{android,flutter-module,ios}/crowdin/auto_l10n.sh`。Token 来自每个项目 `crowdin/.env` 里的 `CROWDIN_TOKEN`。

Wrapper 帮 skill 处理掉 auto_l10n.sh 的三个坑:① diff 是交互式的,会问"是否替换";② 下游 CLI 失败时仍报"成功";③ 空目录跟项目对比,误判"全部删除",一键 y 清空 l10n。**写规则时不要重复造这些保护逻辑,直接用 wrapper。**

Config 文件名规范:`<app>_<platform>_<branch>.json`
- `app`:`kb` (KiwiBit) / `vh` (VicoHome) / `vn` (VicoNature) / `v` (Vico 通用) / `f` (NatureHood / 喂鸟器)
- `platform`:`android` / `flutter` / `ios`
- `branch`:`dev` (日常迭代) / `main` (发版前)

## 获取 Token

未设置时停止并告知:Crowdin 右上角头像 → Account Settings → API → Personal Access Tokens → 新建,勾选 `project`、`project.source.string`、`project.translation` 权限。
- 录入流:`export CROWDIN_TOKEN=...`
- 拉取流:写入对应项目的 `~/A4x/App/g0-{android,flutter-module,ios}/crowdin/.env` 文件(`cp .env.example .env` 后填入)

## API 文档

录入流执行前通过 WebFetch 读取:`https://support.crowdin.com/src/assets/api/crowdin/string-based.yml`

---

## Rules

本 skill 有两条主流。Rule 1-6 是【录入流】,通过 REST API 把文案写到 Crowdin;Rule 7-9 是【拉取流】,通过本地 wrapper 把已翻译 bundle 拉回项目。两组 Rules 完全独立,触发哪一组由 description 里的关键词决定。

### 录入流 (Rule 1-6)

#### Rule 1 — 收集两件事再开始

**首先整理内容**:无论来源是飞书文档、.md、Excel 还是直接描述,先将内容整理成三列表格展示给用户确认——即使 token 尚未就绪也要做这一步,让用户知道内容已被正确解析:

| Identifier | String(英文原文) | Context(使用场景) |
|---|---|---|

**然后验证 token**:内容展示后检查 `$CROWDIN_TOKEN` 是否已设置。未设置则停止,给出获取路径(Account Settings → API → Personal Access Tokens,勾选 `project`、`project.source.string`、`project.translation`,执行 `export CROWDIN_TOKEN=...`),告知用户 token 就绪后可继续。

Token 就绪后,**项目选择**:调用 `GET /projects` 列出所有项目,展示项目名、ID、目标语言数,由用户选择。选定后记录 `projectId` 和完整 `targetLanguages` 列表。

#### Rule 2 — 生成分支名

两件事确认后,自动生成分支名并请用户确认(可修改):

```
feature_<简短描述>_<用户名>_<YYYYMMDD>
```

简短描述从 context 或用户说明中提炼,2-4 个英文单词,下划线连接。确认后调用 `POST /projects/{projectId}/branches`,记录返回的 `branchId`。

#### Rule 3 — 逐条录入:String → 翻译 → 下一条

每条 String 必须按顺序:① 创建 String → ② 立刻翻译写入所有语言 → ③ 再处理下一条。

禁止批量创建完再统一翻译——逐条处理可从断点继续,不影响已完成条目。

**创建 String 的关键约束**:调用 `POST /projects/{projectId}/strings` 时,**`isHidden` 必须显式传 `false`**。该项目存在默认隐藏行为,不传则 String 在编辑器中不可见,翻译无法进行。

**翻译系统提示词**(每条每种语言都用此提示词翻译后再写入):

```
你是一名专业本地化翻译,正在翻译 AI 智能摄像头产品的 UI 文案。

产品场景:安防摄像头(移动侦测/录像/报警)、NatureHood 喂鸟器(鸟类识别/Timeline)、高尔夫教练(姿势分析)。

原则:① 保持简洁;② 参考 context 理解场景;③ 按钮/标签用动词原形;④ Premium、HomeScreen 等专有名词不翻译;⑤ {num}、{username} 等花括号占位符原样保留,禁止翻译。

目标语言:{targetLanguage}
```

翻译完成后调用 `POST /projects/{projectId}/translations` 写入。

#### Rule 4 — 进度汇报

每完成一条(含全部语言),输出:`✓ [N/总数] identifier → X 个语言写入完成`

全部完成后输出汇总(项目、分支、String 数、翻译条目数、失败数)。

#### Rule 5 — 导出校对 CSV

用 Python 生成校对文件,列顺序:`identifier, en, <各目标语言>`,`utf-8-sig` 编码(Excel 打开时 CJK/阿拉伯文不乱码)。Python 不可用时改用 Node.js(`fs.writeFileSync` 写 BOM + CSV 内容)。

文件名:`crowdin_<branch_name>_<YYYYMMDD>.csv`,保存到 `~/Downloads/`(不存在则保存到当前目录)。

#### Rule 6 — 询问是否合并到 dev

CSV 告知用户后立即询问:**「是否将 `<branch_name>` 合并到 dev?(默认:否)」**

用户明确确认后执行:
1. 先调用 `GET /projects/{projectId}/branches?name=dev` 查找 dev 分支的 `branchId`
2. 调用 `POST /projects/{projectId}/branches/{devBranchId}/merges`,传入 `sourceBranchId`(feature 分支)和 `deleteAfterMerge: false`
3. 轮询合并状态直到 `finished` 或 `failed`,输出结果

> **红线:禁止删除任何分支。** `deleteAfterMerge` 必须始终为 `false`,不得调用 `DELETE /branches/{id}`。

---

### 拉取流 (Rule 7-9)

#### Rule 7 — 解析"项目 + Config + 操作"

**0. 先做上下文感知(reduce 用户该说的话)**

在解析用户口语之前,先看以下信号自动推断默认值,然后**把推断结果复述给用户确认**(给用户纠正机会):

| 信号 | 推断 |
|---|---|
| `pwd` 在 `~/A4x/App/g0-android` 下 | platform=android |
| `pwd` 在 `~/A4x/App/g0-flutter-module` 下 | platform=flutter |
| `pwd` 在 `~/A4x/App/g0-ios` 下 | platform=ios |
| 路径含 `kiwibit` / `kb` flavor 段 | app=kb |
| 路径含 `viconature` / `vn` flavor 段 | app=vn |
| 路径含 `vicohome` / `vh` flavor 段 | app=vh |
| 用户最近 `git status` / `git diff` 集中在某 flavor 目录 | 同上 app 推断 |
| `pwd` 在三个项目根之外 | 完全靠口语,不做 platform 推断 |

**用户口语优先**:如果上下文推断 `kb`,但用户明确说了"VH 翻译",以 VH 为准(并提一句"注意你当前在 KB flavor 目录,要切到 VH 项目根再跑吗?")。

**1. 项目** → `android` / `flutter` / `ios`
   - 上下文推断已给出默认 → 用之,但可被口语覆盖
   - 用户说 `flutter` / `dart` / `arb` → `flutter`
   - 用户说 `android` / `xml` / `kotlin` → `android`
   - 用户说 `ios` / `swift` / `strings` / `lproj` → `ios`
   - 上下文 + 口语都没线索 → 用 AskUserQuestion 让用户选

2. **Config 名**(无 `.json` 后缀):`<app>_<platform>_<branch>`
   - `app` 解析(支持中英文、产品全称、口语别名):
     | 前缀 | 命中关键词 |
     |---|---|
     | `kb_*` | KiwiBit / KB / 鸟笔(口语) |
     | `f_*` | NatureHood / F App / 喂鸟器 / 鸟食 |
     | `v_*` | VH App + 用户说"**只拉 VH**"/"**不混 KB**"/"**不合并 KB**" |
     | `vh_*` | VicoHome / VH App(默认含合并 KB,这是 VH App 最常见的) |
     | `vn_*` | VicoNature / VN |
   - **批量场景**:用户说"全端"/"三端"/"all"/"KB 整个"→ 同 app 跑三遍 `pull.sh android <app>_android_dev` + `_flutter_*` + `_ios_*`,每跑完一个汇报进度
   - **歧义消解**:
     - "拉 VH 翻译"(没说混不混 KB)→ 默认 `vh_*`(用户日常 VH App 就走合并)
     - "拉 KB 翻译"(没说端)→ AskUserQuestion 让用户选 android/flutter/ios
     - 完全不确定 → `pull.sh ls` 列全部候选给用户挑
   - `branch`:默认 `dev`;用户提到"主分支"/"发版"/"main"才用 `main`

3. **操作**(默认 `full`,跟用户日常 `cd crowdin && ./auto_l10n.sh config/...` 一致):
   | 用户说 | op |
   |---|---|
   | "更新翻译"/"拉翻译"/"应用翻译"/"sync"/"拉一下"/"更新文案"/"同步" | `full`(默认) |
   | "完整跑一遍"/"发版前更新" | `full` |
   | "部署但不要代码生成" | `deploy` |
   | "看变化"/"对比"/"有没有新翻译"/"diff" | `diff` |
   | "只下载到 temp" | `download` |
   | "质量检查"/"check 一下" | `check` |
   | "只看 token+网络通不通" | `probe` |

三件事齐了向用户复述:**「即将运行 `pull.sh <project> <config>`(默认 full),跑完用 git diff --stat 给你看变更,确认?」**

**短路场景(无需复述)**:用户已经在前面消息里明确确认要拉哪个 app+platform+op,且无歧义,可以直接跑不再问。但批量(三端/多 app)第一次跑前必须复述要跑的清单。

#### Rule 8 — 调用 pull.sh,信任它的退出码

skill 同目录 `pull.sh` 已封装好连通性预检、强制 non-interactive、假成功扫描、git diff 兜底。**直接调,不要绕开它去手搓 `auto_l10n.sh` 命令**。

```bash
~/A4x/AI/skills/skills/crowdin/pull.sh <android|flutter|ios> <config-name> [op]
```

退出码语义:
- `0` 成功(`full`/`deploy` 已部署,git diff 已展示;`diff` 仅查看完成)
- `2` 网络/SSL 被劫持 → 让用户切网络或检查 `/etc/hosts`
- `3` token 失效或无 project 访问权 → 让用户更新 `crowdin/.env`
- `4` config 里的 project/bundle 已失效或未识别错误 → 让用户对齐 `tools/` 的 config
- `5` "假成功"被扫描器拦下 → 把 wrapper 输出给用户排查
- `1` 其他参数/前置条件错误

非 0 立即停止,把 stderr 原样给用户。不要二次包装错误信息。

#### Rule 9 — yolo + git diff 兜底(贴合用户日常工作流)

用户日常工作流就是直接 `cd crowdin && ./auto_l10n.sh config/<file>.json`(默认 full,直接部署),靠 `git diff` 在 IDE 里 review,不满意 `git checkout` 还原。**skill 复刻这个工作流,不强加 diff-first 预览**:

1. 直接跑 `pull.sh <project> <config>`(默认 full)
2. wrapper 跑完后自动展示 `git diff --stat <l10n_dir>` 让用户看变更范围
3. wrapper 给出 `to revert: cd ... && git checkout -- <l10n_dir>` 提示
4. 用户在 IDE 里看具体 diff,决定 commit 还是 revert

只有用户明确说"先看下变化不要部署"才用 `pull.sh ... diff`(只读模式,但因为 auto_l10n.sh 的 diff 子命令会跳过 merge,wrapper 的启发式扫描可能误报"全部删除",此时建议直接跑 full + git diff 兜底)。

`full` 之后注意事项:
- **Flutter**:wrapper 已检测代码生成是否成功(常见:subprocess PATH 找不到 `flutter` → soft warning,翻译已部署)。如有 warning,提醒用户在项目根手动跑 `flutter pub run intl_utils:generate`
- **Android**:提醒 IDE sync,确认 `R.string.*` 引用没断
- **iOS**:提醒 Xcode 看 `Localizable.strings` 是否被 target 拾取

> **红线**:不修改 `crowdin/.env`(token 必须由用户自己写入);不绕过 `pull.sh` 直接调 `auto_l10n.sh`(失去 non-interactive 保护和假成功扫描)。

---

## Examples

### Bad

**录入流**:
```
AI:批量创建完 22 个 String,再批量写翻译
→ 中途失败后无法判断哪些已翻译、哪些没有
```

```
AI:创建 String 时未传 isHidden: false
→ 所有 String 在编辑器中不可见,翻译无法进行
```

```
AI:用 Python urllib / requests 直接发 HTTPS 请求
→ SSLCertVerificationError,本机证书链不完整,请求全部失败
→ 必须改用 subprocess + curl -k(见「路径与工具约定」模板)
```

**拉取流**:
```
AI:绕过 pull.sh 直接调 ./auto_l10n.sh config/kb_flutter_dev.json
→ 失去 non-interactive 保护(脚本在 full 流程中也会问"是否继续?[y/N]");
  失去假成功扫描;失去 git diff 兜底提示
```

```
AI:用户没指定平台说"拉 VN 翻译",AI 猜了 flutter 直接执行
→ 选错项目,拉的是另一端的 bundle
```

```
AI:绕过 pull.sh 直接 cd 到项目 crowdin/ 跑 ./auto_l10n.sh diff
→ 失去 --non-interactive 保护,触发交互式 prompt;失去假成功扫描,
  网络挂掉时空目录对比误判"全部删除",用户误按 y 清空 l10n
```

### Good

**录入流**:
```
用户:我要录入 Crowdin 文案,来源是这个飞书文档 <链接>

AI:
1. 验证 CROWDIN_TOKEN ✓
2. 读取文档 → 整理 22 条表格 → 用户确认
3. 列出项目 → 用户选 app_kb → 记录 16 个目标语言
4. 生成分支名 feature_timeline_i18n_jacky_20260420 → 用户确认
5. 逐条录入:创建 String (isHidden:false) → 翻译 16 语言 → 下一条
   ✓ [1/22] ... ✓ [22/22] ...
6. 完成汇总:22 条 / 352 条翻译 / 0 失败
7. 生成 crowdin_feature_timeline_i18n_jacky_20260420.csv → ~/Downloads/
8. 询问是否合并 dev → 用户确认 → 查 dev branchId → 合并 → 轮询 → ✓ finished
```

**拉取流(自然语言场景)**:
```
用户:帮我拉一下 KB 安卓翻译

AI:
1. 解析 → android + kb_android_dev + full
2. "运行 pull.sh android kb_android_dev,跑完 git diff 给你看变更?" → 用户 y
3. pull.sh android kb_android_dev → exit 0
4. wrapper 自动输出 git diff --stat + to revert 提示
5. "翻译已部署,17 个文件改动,在 IDE 看具体 diff;不满意按上面 revert 命令"
```

```
用户:VH iOS 翻译有变化吗

AI:
1. 解析 → ios + vh_ios_dev + full(yolo 模式 + git diff 兜底,不再用 diff 子命令)
2. "运行 pull.sh ios vh_ios_dev(默认 full),跑完看 git diff?如果只想看不动可以选 diff" → 用户决策
```

```
用户:喂鸟器翻译同步一下

AI:
1. 解析 → 喂鸟器 = NatureHood = f 前缀,但 NatureHood 只有 flutter 端
2. → flutter + f_flutter_dev + full
3. 复述确认 → 跑
```

```
用户:把 KB 全端翻译都更新了

AI:
1. 解析 → kb × 三端(android+flutter+ios)+ full
2. 复述清单:"将依次跑 pull.sh android kb_android_dev → flutter kb_flutter_dev → ios kb_ios_dev,跑完每个都给你 git diff 看,确认?"
3. 用户 y → 串行跑三个,每个跑完汇报变更范围
```

```
用户:拉 VH flutter,但只要 VH 不混 KB

AI:
1. "不混 KB" 关键词 → v_ 前缀(不是 vh_)
2. → flutter + v_flutter_dev + full
3. 复述确认 → 跑
```

```
用户:【pwd 在 ~/A4x/App/g0-android】拉 KB 翻译

AI:
1. 上下文推断:在 g0-android → platform=android
2. 口语:app=kb,branch 默认 dev,op 默认 full
3. → pull.sh android kb_android_dev
4. 复述:"在 g0-android,要拉 kb_android_dev,跑完 git diff 给你看?" → 用户 y
```

```
用户:【pwd 在 ~/A4x/App/g0-flutter-module/lib/kb_flavor】更新翻译

AI:
1. 上下文:在 g0-flutter-module → platform=flutter
2. 路径含 kb_flavor → app=kb
3. → pull.sh flutter kb_flutter_dev
4. 复述:"基于当前目录推断 kb_flutter_dev,确认?" → 用户 y
```

```
用户:【pwd 在 ~/A4x/App/g0-ios】拉 vh iOS 翻译

AI:
1. 上下文 platform=ios,口语也是 ios → 一致
2. 口语 app=vh,默认走 vh_*(合并 KB)
3. → pull.sh ios vh_ios_dev
4. 跑
```

```
用户:【pwd 在 ~/Documents】拉 KB 安卓翻译

AI:
1. 上下文 pwd 不在三个项目根 → 不做平台推断,完全靠口语
2. 口语:android + kb_
3. → pull.sh android kb_android_dev
```

```
用户:【pwd 在 ~/A4x/App/g0-android/ResModule/src/kiwibit】拉 vh 翻译

AI:
1. 上下文:platform=android, app=kb(路径含 kiwibit)
2. 口语:app=vh ← 与上下文冲突!
3. 用户口语优先,但提示:"注意你在 KB flavor 子目录,要切到 VH 项目根再跑吗?"
4. 用户确认 vh 后 → pull.sh android vh_android_dev
```

```
用户:VH flutter 拉一下,但只要 VH 不要混 KB

AI:
1. 解析 → flutter + v_flutter_dev(v_ 前缀 = 不合并 KB)+ full
2. pull.sh flutter v_flutter_dev → ...
```

```
用户:看下 vn ios 有没有新翻译,不要部署

AI: pull.sh ios vn_ios_dev diff → 展示给用户。注意:auto_l10n.sh 的 diff 子命令
会跳过 merge,如果 wrapper 报"17 个文件全删除"这种启发式拦截,告知用户"diff
模式有局限,建议直接跑 full + git diff 兜底"
```

```
用户:拉 KB android 翻译  (网络异常时)

AI:
1. pull.sh android kb_android_dev → exit 2,SSL 被劫持
2. 检查 /etc/hosts 里 crowdin 残留 → 告诉用户怎么清,或建议切网络
```

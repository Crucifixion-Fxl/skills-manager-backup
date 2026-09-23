# mylibrary — 详细搭建指南

从零搭一个「类 `EM/library`」的知识库的逐步操作、文件清单、写作约定细则与排错。`EM/library` 是活样板，下面所有「抄」都指它的 `templates/` 与根目录。

## 一、文件清单：从 EM/library 抄什么

先把 `EM/library` 克隆到本地作为来源：

> `EM/library` 是私有仓库。克隆前需要项目 `Reporter` 及以上或等效的 Group 继承权限；没有权限时先请项目 Owner / Maintainer 授权。

```bash
git clone git@gitlab.addx.ai:EM/library.git /tmp/em-library
```

| 文件（EM/library 内路径） | 作用 | 必抄？ |
|---|---|---|
| `templates/build.py` | 构建脚本：HTML→md 派生、生成 `index.html` 总目录、死链检查（只告警不阻塞）、`--public` 产 Pages 产物 | ✅ 必抄 |
| `templates/knowledge.html` | 知识文档默认骨架（写新文档时复制改名） | ✅ 必抄 |
| `templates/library.css` | 全库共享样式（目录/锚点/TL;DR 盒/折叠） | ✅ 必抄 |
| `templates/library.js` | 全库共享脚本（目录、锚点、自动补 `target=_blank`）。⚠️ **评论模块 + Local ADB 桥客户端都打包在内**——整抄会带进来，普通文档库见下方说明按需删 | ✅ 必抄（注意裁剪） |
| `templates/hooks/` | pre-commit 钩子（提交时自动重建派生区） | ✅ 必抄 |
| `.gitlab-ci.yml` | CI：MR/分支查派生同步、`main` 发 Pages | ✅ 必抄（按需改镜像/tag，见五） |
| `.gitignore` | 至少含 `/public/`、`/.local/`、`/templates/__pycache__/` | ✅ 必抄 |
| `templates/oauth-callback.html` | 划词评论的 OAuth 回调页 | ⬜ 仅开在线评论时 |
| `templates/local_comment_server.py` + `start_local_comment_server.{sh,bat,command}` | 本地 `file://` 评论服务（`.sh` / Windows `.bat` / macOS `.command` 三件套，双击即起）。⚠️ 启动脚本会**连带在 `127.0.0.1:8765` 拉起 ADB 桥** | ⬜ 仅本地批注时 |
| `templates/local_adb_bridge.py` | 文档内 ▶ 按钮对本机设备执行命令 | ⬜ 仅硬件调试库 |

**不抄、要自己来的两类**：
- `md/`、`index.html`：**别抄**，`build.py` 自动生成（机器派生区）。
- `AGENTS.md` + `CLAUDE.md`：**别照抄样板库的项目专属治理**（含 GS001 规则）——这是「给 AI 用的规则 Markdown」，用「2.3」的骨架在你库里**新建**。`EM/library` 的 `README.md` 可当写作约定参考，按需精简成自己的。

> **`library.js` 整抄的代价**：它把目录/锚点/新标签页（你要的）和**划词评论模块 + Local ADB 桥客户端**（多数普通文档库不要）打包在一起。**普通（非硬件）文档库**：保留目录/锚点/`target=_blank` 能力即可正常出站，初始化时删掉评论与 ADB 桥相关模块；想要在线评论再按「六、可选增强」单独开。别因为它标了「✅ 必抄」就连带把硬件调试机制也搬进一个纯文档库。

## 二、逐步搭建（命令行端到端，AI 可直接执行）

`<you>` = 你的 GitLab 用户名；全程不用点网页。

```bash
# 0. 建远程空仓（internal，仅登录同事可见）—— 用 glab，免网页操作
glab repo create <you>/library --defaultBranch main --description "我的技术知识库" --internal

# 1. 取脚手架源（需 EM/library Reporter+ 或等效权限）+ 本地建仓 + 抄核心文件
git clone git@gitlab.addx.ai:EM/library.git /tmp/em-library
mkdir -p ~/git/library/templates && cd ~/git/library && git init
cp /tmp/em-library/templates/{build.py,knowledge.html,library.css,library.js} templates/
cp -r /tmp/em-library/templates/hooks templates/
cp /tmp/em-library/.gitlab-ci.yml /tmp/em-library/.gitignore .

# 2. 装 pre-commit 钩子（需 python3）
git config core.hooksPath templates/hooks

# 3. 把 index 分类改成你的主题（见下「2.1」，不改全部文档会落 overview 兜底）

# 4. 写第一篇（复制模板 → 按「2.2 三个必改点」改）
mkdir -p myproject
cp templates/knowledge.html myproject/快速上手.html

# 5. 本地构建：生成 md/ 派生区 + index.html 总目录 + 死链告警（不阻塞）
python3 templates/build.py

# 6. 提交并发布
git remote add origin git@gitlab.addx.ai:<you>/library.git
git add -A && git commit -m "feat: 初始化知识库"   # pre-commit 钩子自动重建派生区并带进提交
git branch -M main && git push -u origin main      # 推 main → CI 发 Pages

# 7. 访问 https://pages.addx.ai/<you>/library
```

> **Windows/WSL**：仓库在 `/mnt/c/...` 时，`git commit` 必须在 WSL 内执行，否则 pre-commit 钩子的 python3 起不来。
> **Pages 发布前置**：新项目要开 shared runner、用带 python3 的合规镜像，详见「五」与 `gitlab-pages-html`。

### 2.1 把 index 分类改成你的主题（关键，否则全落 overview）

`index.html` 总目录由 `build.py` 自动生成，按**关键词**把每篇归到一个板块。规则在 `build.py` 顶部：

- `INDEX_CATEGORIES`（顶部约第 34 行）：`[(分类id, 中文名, 描述), …]`，定义有哪些板块、它们的中文名与**展示顺序**；
- `INDEX_CATEGORY_RULES`（其下约第 50 行）：`[(分类id, (关键词…)), …]`，按文档**路径/标题**匹配关键词把每篇归到一个板块。

（行号随 `EM/library` 演进会漂，认**常量名**找即可。）

样板库的关键词是 GS001/CQ529 专属（配网/IMU/OTA/bspbox…）。**改成你自己的主题关键词**；没命中的文档落 `overview` 兜底，`build.py` 会在 stderr 提示「未命中分类关键词，归入 overview 兜底」。最省事：初期全留 `overview` 也能用，文档多了再按主题补分类。

### 2.2 用 knowledge.html 写文档（复制后 3 个必改点）

`knowledge.html` 的 `<head>` 里有一大段**填写说明注释**（含可视化组件用法），照它填即可。复制到文档位置后**这 3 处必须改，否则页面坏**：

1. **css/js 相对路径**：模板默认 `href="library.css"` / `src="library.js"`（仅供同目录预览）。按文档深度改成指向 `templates/`：`<仓>/<产品>/x.html` → `../../templates/library.css`，**每深一层多一个 `../`**。不改 = 整页没样式。
2. **面包屑 + index 链接**：`<nav class="crumb"><a href="../../index.html">📚 library</a> / …` 改成你的路径与正确深度。
3. **占位与示例链接**：正文遍布 `替换我`、示例「相关文档」指向样板库专属页（如 `配网.html`/`CQ529 WiFi`）——**全部换成你的真实内容/链接，删掉用不上的示例组件**，否则一堆死链。

### 2.3 给 AI 用的仓库规则：AGENTS.md（+ CLAUDE.md）

库的「AI 友好」靠**两个给 AI 用的 Markdown**：① `build.py` 自动派生的 `md/` 镜像——AI **读你的知识内容**；② 仓库根的 `AGENTS.md`——AI **在你库里干活时遵守的规则**（怎么写、什么不能改）。`md/` 自动来；`AGENTS.md` 要你建（**别照抄样板库的项目专属规则**，它含 GS001 治理）。在仓库根放下面这份骨架：

```markdown
# AGENTS.md — <你的库> agent 指南

本仓是「类 library」知识库：HTML 单源，`build.py` 自动派生 `md/` 镜像 + `index.html`。

## 铁律
- **只改源 HTML**；`md/` 与 `index.html` 是机器派生区，**永不手改**（改了下次 build 覆盖、CI 判不同步）。
- 提交即构建：已装 pre-commit 钩子，`git commit` 自动重建派生区；也可手跑 `python3 templates/build.py`。
- 事实以源码/实测为准，设计文档只当线索；拿不准标「存疑」别臆测。

## 文档怎么写
- 一篇一主题：正文按主题/方案/测试方法组织 + 一个 `# 附录`；进度/MR/证据/出处/版本进附录并被正文超链引用。
- 命名：文档名 4~8 字；同级标题风格统一、≤4 字为佳；版本号/专有名词放正文不进名/标题。
- 新建文档复制 `templates/knowledge.html`，按其 head 注释填；复制后改 css/js 相对路径、面包屑、替换占位与示例链接。
- 互链用相对路径、保持双向（A 引 B，则 B 的「相关文档」补 A）。
- index 分类由 `build.py` 的 `INDEX_CATEGORY_RULES` 关键词决定，新主题去那里补关键词。

## 重点文档保护（可选）
- 把少数定稿权威文档列为「重点文档」，未明确点名不改、只修有证据的明显错误。清单按本库实际填。
```

再放一行 `CLAUDE.md`（让 Claude 与 Codex/Cursor 等读同一套规则）：

```markdown
@AGENTS.md
```

> 这俩文件也让任何 AI（包括跑本 skill 的）一进你的库就知道「只改 HTML、md 是派生的、怎么写文档」，是「确保 AI 能正确维护库」的关键。

### 2.4 搭完验收（Definition of Done）

照上面跑完，满足这几条才算搭成了一个能用的库：

- [ ] `python3 templates/build.py` 退出 0，输出「派生 N 篇 md，索引收录 N 篇」，无你没料到的死链告警。
- [ ] `index.html` 已生成，且你的文档进了**你定的分类**（不是全堆在 overview）。
- [ ] 浏览器开你的文档 HTML：**有样式**（css/js 路径对）、目录/锚点正常、面包屑能回 index。
- [ ] `md/<同名>.md` 已派生（**给 AI 读取的镜像层**）。
- [ ] 仓库根有 `AGENTS.md`（+ `CLAUDE.md` @import）——**给 AI 的规则文件**，别忘了建。
- [ ] push `main` 后 CI 的 pages job 绿、`GET /api/v4/projects/:id/pages` 的 `deployments[0]` 刷新（别拿本机 curl 判，见「五」）。
- [ ] `https://pages.addx.ai/<you>/library` 登录后能打开、能搜索。

## 三、文档结构模板

每篇 = 若干主题章节 + 一个 `# 附录`：

```markdown
# 主题章节一
<技术方案 / 测试方法>

# 主题章节二
<技术方案 / 测试方法>

# 附录
<进度状态 / 相关文档 / 源码索引 / 原始数据 / 外部资料 / 版本来源——须被正文超链引用>
```

- 正文章节标题用**主题名**（如「命令速查」「职责边界」），不按「谁写的」分区。
- 非技术方案/测试方法的一切（进度、MR、证据、出处、版本）放 `# 附录`，并从正文链接过去，不散落正文。

## 四、写作约定细则

- **命名**：文档名 4~8 字（4 字达意更佳）；同级标题风格统一、可加括号补充（如「烧录流程（三线相同）」）；版本号、专有名词（WiFi/OTA 等）放正文，不进文件名/标题。
- **归类**：同类主题尽量并一篇，单篇过长再按子主题拆（如「升级」拆「升级选区」「升级通道」）；同类文档 **≥4 篇** 时建子文件夹归拢。
- **互链**：相对路径互引，含 `#中文锚点` 深链；保持**双向**——A 引 B，B 的「附录/相关文档」补 A。
- **HTML vs Markdown**：定稿知识用 HTML；过程文档留 Markdown，稳定后转。`md/` 与 `index.html` 是机器派生区，勿手改。

### 正反例（对照上面的约定）

❌ **Bad**：

```text
myproject/
  GS001配网功能v3-最终版.html   ← 文件名带版本号/专有名词，且未来还会有"v4最终版"
  └─ 正文里混着：配网协议讲解 + 这周的进度 + 三个 MR 链接 + 充电 IC 选型 + TODO
md/配网.md                      ← 手动改了这个派生文件（下次 build.py 直接覆盖，CI 报不同步）
```
问题：一文多主题、进度/MR 堆在正文、文件名带版本、手改派生区。

✅ **Good**：

```text
myproject/
  配网.html        ← 4 字达意；只讲配网这一个主题
  充电IC.html      ← 充电 IC 单独成篇
# 配网.html 正文只写协议与测试方法；进度/MR/证据放 `# 附录` 并被正文链接引用
# 只编辑 HTML，commit 时 pre-commit 钩子自动重建 md/ 与 index.html
```

## 五、构建与发布细节

- **pre-commit 钩子**：每次 `git commit` 自动 `build.py` 重建 `md/` + `index.html` 并 `git add` 进本次提交。新克隆一次性安装：`git config core.hooksPath templates/hooks`。
- **CI（照抄 EM/library 的 `.gitlab-ci.yml`）**：
  - 非默认分支 / MR：跑 `build.py` 后 `git diff` 校验 `md/`、`index.html` 与源同步，不同步则失败 → 本地重跑脚本提交。
  - `main`：`build.py --public public` 生成产物，`artifacts.paths: [public]` 发 Pages。
- **发布从 `main`（与 gitlab-pages-html 的 demo 走 docs 分支不同）**：library 是**纯文档仓**，`main` 本身就是文档，所以发 `main`；那是给「一次性 HTML demo 与生产代码隔离」的场景。
- **镜像/runner 差异**：`EM/library` 用 `image: python:3.12` + `tags:[kubernetes]` 跑通（`build.py` 需要 python3）。换到你的新项目若出现 job 一直 pending 或 `runner_system_failure`，按 `gitlab-pages-html` 处理：① 开 `shared_runners_enabled`；② 换 `tags:[sg-amd64]`；③ 换合规 harbor 镜像——但 harbor `base/` 无 python 轻量镜像，需走 base-images 同步或选一个带 python3 的可用镜像。先照抄样板库配置试跑，不通再据此调。
- **确认部署成功**：看 GitLab 服务端证据（pages job `success` + `GET /api/v4/projects/:id/pages` 的 `deployments[0].created_at`），**别用本机 curl Pages URL 的 HTTP 码判断**（若 Pages 设为 `private`，未登录请求一律先 `302`，与内容是否部署无关）。详见 `gitlab-pages-html`。
- **图形/可视化**：md 镜像对纯文字/表格/代码无损，但图形与 `data-md-skip` 块会降级；`build.py` 会在这类文档的 md 顶部盖一行降级提示并指回源 HTML，需要精确内容时回源 HTML 读。
- **模板演进**：改 `library.css`/`library.js` 自动作用全库；改模板 HTML 结构不自动回灌旧文档，在模板头「模板更新记录」追一行标【需回刷】，旧文档下次被编辑时顺手回刷，不批量翻新。

## 六、可选增强

### 6.1 划词评论 → GitLab issue
- 在线读者选中文字即可评论，自动同步成团队可见的 GitLab issue notes。
- 需要：`library.js` 的评论模块 + `templates/oauth-callback.html`。**裁剪/精简公开产物时务必保留 `oauth-callback.html` 公开**，否则在线登录会跳回 404。
- 本地 `file://` 预览的评论：起 `bash templates/start_local_comment_server.sh`（Windows 双击 `.bat`、macOS 双击 `.command` 同效；同时拉起评论服务 `127.0.0.1:8766` 与 ADB bridge `127.0.0.1:8765`），数据存 `.local/libcmt.json`（gitignore）。

### 6.2 Local ADB Bridge（仅硬件调试库）
- 文档命令旁的 ▶ 按钮对本机连着的板子/设备执行命令。普通文档库不要开。
- 脚本 `templates/local_adb_bridge.py`，随评论服务一起起。

### 6.3 文档分级保护（治理约定）
- 把少数「定稿权威」文档列为**重点文档**，约定：未在对话中明确点名 → 不改（含通用命令、批量处理、格式美化、信息补充都跳过）；只有「有证据的明显错误」（失效锚点/死链、与代码或实测矛盾、自相矛盾）才可直接修，且只修错误本身。
- 防 AI/协作者顺手「优化」把定稿改坏。是约定不是代码，写进你库的 `AGENTS.md` 即可。**清单按你自己的文档定，别照抄样板库的 GS001 清单。**

## 七、排错速查

| 现象 | 原因 / 处理 |
|---|---|
| commit 后 `md/`、`index.html` 没更新 | 钩子没装：`git config core.hooksPath templates/hooks`；或 Windows 下在 Windows 端提交了（改在 WSL 内提交） |
| CI `check` 失败说派生区不同步 | 本地 `python3 templates/build.py` 后把变更一并 `git add` 提交 |
| Pages job 一直 pending | runner 问题，见五 / `gitlab-pages-html`：开 shared runner + 换 tag |
| Pages job `runner_system_failure` | dockerhub 扁平镜像被 Kyverno 拦，换合规 harbor 镜像（需带 python3） |
| Pages 打不开/超时 | Pages 只在公司网可达；且确认 `pages_access_level` 与对方是否登录/在内网，见 `gitlab-pages-html` |
| 在线评论登录跳 404 | `oauth-callback.html` 被裁掉了，恢复其公开 |
| md 里图/可视化丢失 | 正常降级，回源 HTML 读（顶部有降级提示） |

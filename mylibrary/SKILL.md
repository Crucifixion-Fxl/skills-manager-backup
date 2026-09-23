---
name: mylibrary
description: 搭建「类 EM/library」的个人/团队技术知识库：HTML 单源 + 自动派生 Markdown 供 AI 检索 + pre-commit 构建 + GitLab Pages 发布 + 划词评论。当用户想把零散技术笔记/调试结论沉淀成同事可见、可搜索的文档库，或提到「个人知识库/文档库/知识沉淀/文档站/类似 library」时使用。发布到 Pages 的底层机制见 gitlab-pages-html。
---

# mylibrary — 搭一个「类 [library](https://pages.addx.ai/em/library/)」的知识库

把零散的技术笔记、调试结论、踩坑记录，沉淀成一个**同事可见、可搜索、可划词评论**的在线文档库——参照 `EM/library` 的现成做法，半天就能跑起来。

- **本 skill 给什么**：方法论 + 从零搭建骨架。
- **脚手架不重复造**：可复用文件直接抄 `EM/library/templates/`，本仓不再留一份。
- **要细节去哪**：详细命令 / 文件清单 / 写作约定细则 / 排错 → [setup-guide](references/setup-guide.md)；划词评论与 ADB 桥 → [comments-and-adb](references/comments-and-adb.md)。

## 描述

一个 GitLab 仓库 = 你的知识库：你**只手写 HTML 知识页**，零依赖的 `build.py` 自动派生出给 AI 检索的 Markdown 镜像 + 总目录 + 死链检查，推到 `main` 后 CI 自动发到 GitLab Pages，得到一个 `pages.addx.ai/<你>/library` 的在线链接；被授予 `Guest` 及以上项目权限的同事登录后可查看。

```text
  你手写 HTML          build.py                          git push main
  <产品>/<主题>.html  ─────────▶  md/ 镜像（喂 AI 检索）   ─────────▶  CI
        │                         index.html（总目录）      （pre-commit       │
        │                         死链/锚点检查              已先跑过 build）   ▼
        └── git commit ──────────────────────────────────────────▶  GitLab Pages
            （钩子自动跑 build.py 并把派生区带进提交）        https://pages.addx.ai/<你>/library
```

### AI 友好的核心：两个 Markdown

| 给 AI 的 Markdown | 干嘛用 | 谁来生成 |
|---|---|---|
| 派生的 **`md/` 镜像** | AI **读知识内容**——纯文本可检索（HTML 的图形 / 交互 AI 读不动） | `build.py` 自动派生 |
| 根目录 **`AGENTS.md`**（+ 一行 `CLAUDE.md`） | AI **守库规则**——只改 HTML、md 是派生的、怎么写 | 你建（别照抄样板库的项目专属规则） |

> 有了这俩，任何 AI 一进库就会**正确维护**：读得到内容、也知道规矩。

### 何时用 / 跟邻近 skill 的分工

> mylibrary 是「搭知识库」这件事的总入口。下面这些更窄的活有更专、更直接的 skill——但它们的产出**照样能沉淀进你的库**，不是互斥，是子集。

| 你的主要目标 | 更直接的 skill | 说明（产出都能进库） |
|---|---|---|
| 把个人 / 团队技术知识做成可分享、可搜索的文档库，「像 library 那样」组织 | **本 skill** | 这就是总入口 |
| 发一个一次性 HTML demo / 原型 | `gitlab-pages-html` | 更直接；沉淀价值高的也可收进库 |
| 发 / 归档周报（固定 author / week 结构） | `weekly-report-publish` | 专走周报流水线，归档进库 |
| 给**单个项目**写架构 / ADR / 技术设计 | `architect` / `story-craftsman` | 项目内文档，也可链进库 |

## 核心理念

> 4 条，照着做就不会跑偏。结构 / 命名 / 归类都是手段，与「读得高效、不出错」冲突时一律让位。

| # | 理念 | 落地 |
|---|---|---|
| 1 | **可读性、准确性第一** | 简洁＝读得高效，不是写得少；一切结构服从阅读 |
| 2 | **HTML 是唯一手写权威** | 定稿知识写 HTML（TL;DR 盒 / 折叠 / 锚点 / 互链）；还在更新的过程文档先留 Markdown，稳定后转；`md/` 与 `index.html` 是机器派生区，**整树勿手改** |
| 3 | **一篇一主题** | 正文只写技术方案 / 测试方法 + 一个 `# 附录`；进度 / 状态 / MR / 证据 / 源码索引 / 原始数据 / 出处 / 版本来源全进 `# 附录`，且须被正文超链引用，不堆无关内容 |
| 4 | **来源靠 git，不靠徽章** | 谁写 / 谁核定看 git 提交（`Co-Authored-By` 区分 AI 改 vs 人手敲）；不在文档里用徽章或分区标作者 |

## 参考实现：EM/library（直接照抄）

不要从零造轮子。`EM/library` 是一个持续维护、约百篇文档的活样板：

| | |
|---|---|
| **仓库** | `git@gitlab.addx.ai:EM/library.git`（仓库可见性 `private`；克隆需 `Reporter` 及以上或等效继承权限） |
| **在线** | <https://pages.addx.ai/em/library>（Pages `public`，公司网络内可直接阅读） |
| **机制权威** | 它的 `README.md`（书写 / 构建约定）+ `AGENTS.md`（文档分级 / 评论处理） |
| **脚手架** | `templates/`：`build.py` · `knowledge.html` · `library.css` · `library.js` · `hooks/` |

> ⚠️ 它的 `AGENTS.md` 还含样板库专属治理（GS001 重点文档清单、platform_a4x 附件归档路径）——**复用结构，别照搬这些内容**，治理规则按你自己的库重写。下文的 `gitlab.addx.ai` / `pages.addx.ai` / runner / 镜像都是 **addx 公司这套 GitLab/Pages 实例**的值，换别的 GitLab 自行替换。

**搭你自己的库 = 新建一个 `<你>/library` 仓库 + 从 `templates/` 抄核心脚手架 + 写你自己的内容。** 抄哪几个文件、怎么改，见 [setup-guide](references/setup-guide.md)。

> 👀 **先去点一圈活样板**：<https://pages.addx.ai/em/library/>（公司网络内可直接阅读）。可浏览、可搜索；OAuth 回调配置正确且读者拥有项目权限时，还可登录后**划词评论**（评论自动变成团队可见的 GitLab issue）——看完再回来照着搭。

## 从零搭建

> 目标级骨架 + 最易漏的关键点。可直接执行的命令行 runbook（含 `glab` 建仓、验收清单）在 [setup-guide 第二节](references/setup-guide.md)。

```bash
glab repo create <你>/library --internal              # 1. 建远程空仓（免点网页）
# 2. clone EM/library（需 Reporter+ 或等效权限），抄 templates/{build.py,knowledge.html,library.css,library.js,hooks/}
#    + 根目录 .gitlab-ci.yml、.gitignore 到新仓
git config core.hooksPath templates/hooks              # 3. 装 pre-commit 钩子（需 python3；Win 在 WSL 内提交）
cp templates/knowledge.html <产品或主题域>/<主题>.html   # 4. 写第一篇
# 5. 建 AGENTS.md（库内约定）+ 一行 CLAUDE.md(@AGENTS.md)，别照抄样板库的项目专属治理
python3 templates/build.py                             # 6. 本地构建（派生 md/ + 生成 index.html + 死链告警，不阻塞）
git commit && git push origin main                     # 7. 钩子重建派生区 → CI 发 Pages → 开 pages.addx.ai/<你>/library
```

**四个复现关键点**（漏了库就坏；详见 setup-guide 2.1 / 2.2 / 2.3 / 2.4）：

| 必做 | 不做的后果 |
|---|---|
| 改 `build.py` 的 `INDEX_CATEGORY_RULES` 成你自己的主题关键词 | 所有文档落 `overview` 兜底 |
| `knowledge.html` 复制后改 3 处：`library.css`/`library.js` 相对路径、面包屑、替换所有 `替换我` 占位与样板库示例链接 | 没样式 + 一堆死链 |
| 建 `AGENTS.md`（+ `CLAUDE.md`）给 AI 的库内规则 | AI 进库不知「md 是派生的、只改 HTML」，易改坏 |
| 照「搭完验收清单」自检：build 退 0 / 有样式 / 进了你定的分类 / `md/` 已派生 / `AGENTS.md` 已建 / Pages 能开 | 不知道搭没搭成 |

## 构建、发布与维护

### 构建与发布

- **提交即构建**：pre-commit 钩子在每次 `git commit` 自动重建 `md/` 与 `index.html` 并带进提交，无须手跑；也可随时 `python3 templates/build.py` 即时刷新（含死链检查）。
- **发布**：推到 `main` 后 CI 跑 `build.py --public public` 生成 Pages 产物并发布。分支 / MR 上 CI 会校验「派生区与源是否同步」，不同步则失败——本地重跑脚本把变更一并提交即可。

> ⚠️ **Pages 机制全交给 `gitlab-pages-html`**（runner、镜像、`pages_access_level`、「只在公司网可达」、如何确认部署成功），本 skill 不重复。但有意不同：`EM/library` 是**纯文档仓**，发 `main`（不像 `gitlab-pages-html` 的一次性 demo 走 `docs` 分支），且 `build.py` 要 python3 镜像（非该 skill 默认的 maven 镜像）——用 `python:3.12` + `tags:[kubernetes]` 跑通。新项目若 job 卡 pending 或被 Kyverno 拦，按它的「三处必改」处理（开 shared runner / 换 tag / 换合规且带 python3 的 harbor 镜像）。

### 维护

- **md 保真与回源**：md 镜像对纯文字 / 表格 / 代码无损，但图形 / 可视化与 `data-md-skip` 块会降级；`build.py` 会在这类文档的 md 顶部盖降级提示并指回源 HTML，要精确内容时回源读。
- **模板演进**：改 `library.css`/`library.js` 自动作用全库；改模板 HTML 结构不自动回灌旧文档，在模板头「模板更新记录」追一行标【需回刷】，旧文档下次被编辑时顺手回刷，不批量翻新。

## 文档写作约定

| 维度 | 约定 |
|---|---|
| 文件 / 标题命名 | 文档名 4~8 字（4 字达意更佳）；同级标题风格统一、可加括号补充；版本号、专有名词放正文，不进名 / 标题 |
| 主题归类 | 同类尽量并入一篇，过长再按子主题拆；同类 **≥4 篇** 时建子文件夹归拢 |
| 互链 | 文档间用**相对路径**互引（含 `#中文锚点` 深链），保持**双向**：A 引 B，则 B 的「附录 / 相关文档」也补 A |
| 链接行为 | 非页内锚点链接默认开新标签页，由 `library.js` 统一补 `target=_blank`，不用逐个手写 |
| 附件 | 文档库只放正文知识页 + 阅读所需图片；日志 / 固件 / 工具包等大附件放别处，正文只放稳定外链 |

## 入口页（让同事真用起来）

库能不能落地，常卡在「同事不知道怎么用、为什么值得用」。给库写一个面向读者的**入口 / 使用指南页**（参照 `EM/library` 的 `ai/使用指南.html`），比一进来就甩一长串文档列表更能拉动使用：

- **开头用「亮点卡」讲清凭什么值得用**——划词评论、命令执行、HTML 比 Word/Markdown 更适合人读又好喂 AI、知识合一处……可复用 `library.css` 里的 `.spotlight`（首屏大卡 + 顶部彩条）和 `.feat-grid`/`.feat-card`（好处卡）组件，不用自己写样式。
- **再讲怎么访问 / 怎么读 / 怎么评论**：访问入口、阅读增强（目录 / 锚点 / 深色 / 搜索）、划词评论与命令执行的用法。
- 它本身就是一篇 HTML 知识页，照常 build、进总目录、也能被划词评论——不是额外机制。

## 可选增强（按需开，别默认全上）

> 完整搭法、API、坑见 [comments-and-adb](references/comments-and-adb.md)。先按库的类型决定开哪几个：

| 增强 | 纯文档库（多数人） | 硬件 / 嵌入式调试库 |
|---|:---:|:---:|
| 划词评论 → GitLab issue | 想要团队 review 就开 | 按需 |
| 本机命令执行（ADB 桥） | ❌ 不开 | ✅ 开（改命令白名单） |
| 文档分级保护 | 按需（治理约定） | 按需 |

### 划词评论 → GitLab issue

读者在线选中正文即可评论，自动落成团队可见的 GitLab issue note，**无需后端**。读者还能**回复 / 编辑 / 删除、@提及同事（搜全 GitLab、支持中文名、todo+邮件通知）、`Ctrl+V` 贴图、标解决 / 锁定、改版自动重锚**——全在 `library.js` 里，照抄脚手架即白得。

- **机制**：每篇文档一个「载体 issue」（`title==文档相对路径` find-or-create），划词评论＝该 issue 的 note（锚点写在 HTML 注释头，issue 页渲染成 blockquote；改版定位不到降级为普通评论）；登录走 GitLab OAuth PKCE，回调页 `templates/oauth-callback.html`。
- **★ 坑**：`oauth-callback.html` **必须随 Pages 一起公开**（OAuth `redirect_uri` 落点，删了 = 在线登录跳 404，样板库曾踩过）；无标签管理权限的低权限项目成员创建 issue 时，GitLab 可能**静默剥掉 label**，所以认 `title` 不认 label。
- **本地预览**：`file://` 的评论走本机服务 `templates/local_comment_server.py`（`127.0.0.1:8766`，存 `.local/libcmt.json` 已 gitignore；REST + `list/delete` CLI；含「损坏拒写防清库」护栏；服务挂了页面自动退回 localStorage）。

### 本机命令执行（Local ADB Bridge）— 仅硬件调试库

文档里命令旁加 ▶ 按钮，点了直接对本机连着的板子跑 `adb shell <cmd>` / `adb <subcommand>`。脚本 `templates/local_adb_bridge.py`（`127.0.0.1:8765`），**命令白名单**只接受文档记录的排查命令。

- ⚠️ 白名单里的 `bspbox/dev_mcu_test/impdbg`、`/app/bin/...` 是 GS001 特定的，换产品要改。
- ⚠️ `library.js` 把评论模块和 ADB 桥客户端**打包在内**，`start_local_comment_server.sh` 还会**连带在 `8765` 拉起 ADB 桥**——普通（非硬件）库整抄会带进这些，按需删掉对应模块。

### 文档分级保护

把少数「定稿权威」文档列为重点文档，约定「未明确点名不改、只修有证据的明显错误」，防 AI 顺手「优化」改坏。是治理约定，按需采用，**别照抄样板库的具体清单**。

## 规则与红线（速查）

> 🔴 = 硬线，越线库会坏 / 出事；⚠️ = 提交 / 放开前先确认。多数详情在上文，这里一眼扫。

| | 红线 | 一句话 + 出处 |
|:---:|---|---|
| 🔴 | **派生区永不手改** | `md/`、`index.html` 是机器派生，只改源 HTML（核心理念 ②） |
| 🔴 | **一篇一主题** | 正文只放方案 / 测试，余进 `# 附录`（核心理念 ③） |
| 🔴 | **大文件不进库** | 密钥 / 日志 / 固件 / 大附件不进库（`.gitignore` 已挡 `/public/`、`/.local/`、`/templates/__pycache__/`） |
| ⚠️ | **访问权限** | 仓库与 Pages 分开设置：新库的 Pages 默认保持 `private`，按 `gitlab-pages-html` 给读者授予 `Guest`；样板库 Pages `public` 是经确认的例外，不照抄 |
| ⚠️ | **文件命名** | 4~8 字、不带版本号 / 专有名词（文档写作约定） |

## 示例

> 一篇文档该长什么样——反面 vs 正面。完整版（含更多细节）见 [setup-guide「四、写作约定细则 · 正反例」](references/setup-guide.md)。

### ❌ Bad

```text
myproject/GS001配网功能v3-最终版.html   ← 文件名带版本号 / 专有名词，未来还有「v4最终版」
  └─ 正文混着：配网协议 + 本周进度 + 三个 MR 链接 + 充电 IC 选型 + TODO
md/配网.md                              ← 手改了派生文件（下次 build.py 直接覆盖，CI 报不同步）
```

问题：一文多主题、进度 / MR 堆正文、文件名带版本、手改派生区。

### ✅ Good

```text
myproject/配网.html       ← 4 字达意，只讲配网这一个主题
        /充电IC.html      ← 充电 IC 单独成篇
# 配网.html 正文只写协议与测试方法；进度 / MR / 证据放 `# 附录` 并被正文链接引用
# 只编辑 HTML，commit 时 pre-commit 钩子自动重建 md/ 与 index.html
```

## References

| 资源 | 看什么 |
|---|---|
| `EM/library` 的 `README.md` / `AGENTS.md` | 书写 / 构建约定权威 + 文档分级 / 评论处理 |
| [setup-guide.md](references/setup-guide.md) | 详细搭建步骤 / 文件清单 / 写作约定细则 / 排错 |
| [comments-and-adb.md](references/comments-and-adb.md) | 划词评论 / 本地评论服务 / ADB 桥的完整搭法与坑 |
| `gitlab-pages-html` | Pages 发布机制与 addx 必踩点 |
| `weekly-report-publish` · `product-tech-research` · `software-development-guide` | 姊妹先例（HTML 归档 + Pages）· 调研报告产出可沉淀进库 · 团队/公司级知识库可作其文档入口 |

# mylibrary — 划词评论 与 Local ADB Bridge

EM/library 在「只读文档站」之上加了两套交互能力：**读者划词评论**（在线同步成团队可见的 GitLab issue；本地预览走本机服务）和 **Local ADB Bridge**（文档里的命令点一下就打到本机连着的板子）。两套都是**可选增强**——纯文档库不开也完整可用；下面讲清各自怎么搭、有哪些坑。脚手架文件都在 `EM/library/templates/`。

---

## 一、在线划词评论 → GitLab issue

读者在 Pages 上选中正文一段文字即可评论，评论自动落成**团队可见的 GitLab issue note**，无需任何后端。逻辑全在 `templates/library.js` 的评论模块里。

### 读者能做什么（照抄 library.js 即白得，无需额外开发）

整套交互都已在 `library.js` 里实现，复用脚手架就一并拿到：

- **整页 / 划词两种评论**：抽屉底输入框写整页评论；正文选段浮出「评论此段」写带引用的划词评论，发表后原文黄色高亮、点高亮↔点引用双向跳转。
- **回复 / 编辑 / 删除**：每条评论可回复成楼中楼；自己发的可改可删。
- **@提及同事**：评论框打 `@` 即弹推荐（本文档参与者 + 活跃同事），可**搜全 GitLab**（支持中文名），`↑↓`/`Enter` 选填；被 @ 的人收 GitLab 通知（todo + 邮件），`@用户名` 渲染成主页链接。仅在线评论通知，本地批注不通知。
- **贴图**：评论框 `Ctrl+V` 粘贴截图即作附件——在线传到 GitLab project uploads、本地内联进 `.local`，抽屉里直接显示。
- **解决 / 锁定**：每条根评论可标「解决 / 重开」「锁定 / 解锁」（用隐藏的 append-only 状态 note 实现；已获得项目访问且可创建 note 的登录读者可操作，无需 `Developer` 写权限），已解决淡化、锁定禁回复，徽章一眼看状态。
- **自动重锚**：文档重新生成 / 改动后，划词评论按「引文 + 前后约 24 字上下文」**三级容错**自动重新锚定（精确 → 归一化空白 → 引文前缀），容忍重排和小改，不轻易掉成孤儿引用。

### 机制（怎么把评论存进 GitLab）

- **一篇文档 = 一个「载体 issue」**：评论模块按 `title == 文档相对路径` 精确匹配 find-or-create 一个 issue（`GET /issues?search=<docPath>` 找、没有就 `POST /issues` 建，description 里写「本 issue 承载文档页内评论，请勿改标题」）。
- **划词评论 = 该 issue 的 note**：选区的锚点（引用原文 + 前后约 24 字上下文）以 **HTML 注释头**写进 note body 开头——issue 页里注释不可见、引文渲染成 blockquote，两端都体面。页面加载后按文本内容重新定位高亮；**文档改版定位不到时降级成普通评论**展示，不丢。
- **唯一键是 title，不是 label**：无标签管理权限的低权限项目成员创建 issue 时，GitLab 可能**静默剥掉 labels**，按 label 过滤会查不到；label 只当 best-effort 装饰。

### 登录（GitLab OAuth PKCE，前端纯客户端）

- 读者点登录 → 跳 `GitLab /oauth/authorize`（`response_type=code` + `code_challenge`，PKCE）→ 授权后回到 `templates/oauth-callback.html`：它用 `sessionStorage` 里存的 `code_verifier` 去 `POST /oauth/token` 换 access token，存好后跳回原文档页。**回调页不含任何密钥**，`client_id` 等参数全来自发起页 sessionStorage。
- 需要在 gitlab.addx.ai 建一个 **OAuth Application**，`redirect_uri` 指向你 Pages 上的 `oauth-callback.html`，把 `client_id` 填进 `library.js` 顶部的 `CFG`（连同承载 issue 的项目 `PRJ`、`label`）。
- OAuth Application 的 Redirect URI 属于 GitLab 外部配置；仓库或 Pages 迁移后必须同步更新，并以实际登录回调成功为准，不能只凭页面可打开就判定评论可用。

### ★ 必踩坑

- **`oauth-callback.html` 必须随 Pages 一起公开发布**。它是 OAuth 的 `redirect_uri` 落点——裁剪/精简公开产物时把它删了，在线登录就会**跳回 404**（EM/library 曾踩过：一次"裁剪内部文件"把它删了导致线上评论登录全挂）。`build.py --public` 的产物里务必保留它。
- 评论默认进 **issue notes**，谁能看由该项目可见性决定；别把敏感内容写进评论。

### 处理评论的工作流

- 在线评论 = 改对应载体 issue 的 note。处理完按锚点改源 HTML → 提交 → 关掉/删掉对应 note 结案；删完重拉确认剩余为 0。
- 这套「评论即 issue」让团队 review 文档不用离开浏览器，也方便 AI 批量取评论、改文档、回写。

---

## 二、本地 file:// 评论服务（local_comment_server.py）

本地用 `file://` 预览文档时没有线上 issue，评论存本机一个 JSON 服务，离线也能随手批注，且方便自动化处理（不用碰浏览器 LevelDB）。

- **启动**：`bash templates/start_local_comment_server.sh`——它会**同时拉起评论服务（`127.0.0.1:8766`）和 ADB 桥（`127.0.0.1:8765`）**，各带健康检查、幂等（已在跑就跳过）、后台 detached。也可单独 `python3 templates/local_comment_server.py serve`。
- **存储**：`.local/libcmt.json`（已 gitignore，不入库）；`LIBCMT_DB` 环境变量可改路径。
- **API（仅 localhost + Origin 校验）**：`GET /health`、`GET /all`、`GET /notes?doc=`；`POST /notes`、`POST /import`；`PUT /notes/<id>`、`DELETE /notes/<id>`。
- **CLI**：`python3 templates/local_comment_server.py list`（看所有待处理本地评论）、`... delete <doc> <note_id>`。
- **防清库护栏**：`libcmt.json` 顶层若损坏成非 dict（如 `[]`/`""`），**拒绝写入并报错**（不静默回退空库，否则下一次写盘会永久抹掉全部评论）；写盘用临时文件 + `os.replace` 原子替换 + fsync。
- **降级**：本机服务不可用时，页面自动退回浏览器 `localStorage`，不阻塞批注；服务恢复后迁移旧批注。

---

## 三、Local ADB Bridge（local_adb_bridge.py）——仅硬件调试库

让文档里的命令旁出现 ▶ 按钮，点一下就把命令打到**本机连着的板子/设备**。静态页本身不能起 adb，靠这个本机小服务中转。

- **用途**：硬件/嵌入式知识库里，调试命令写在文档里、读者点 ▶ 直接在本机执行 `adb shell <cmd>` 或 `adb <subcommand>`，省去复制粘贴。
- **端口**：`127.0.0.1:8765`（随上面的启动脚本一起拉起）。
- **安全**：**命令白名单**——只接受文档里记录的排查命令（`ALLOWED_SHELL_HEADS` / `ALLOWED_ADB_SUBCOMMANDS`），别的拒掉；只听 localhost。
- **⚠️ 这是最 GS001/硬件特定的一块**：白名单里的 `bspbox`、`dev_mcu_test`、`impdbg` 和路径 `/app/bin/...` 都是 GS001 的；换产品要改 `ALLOWED_SHELL_HEADS`。还内置 Windows→WSL 路径转换。
- **普通（非硬件）文档库不要开这块。**

---

## 四、要不要开 / 怎么裁

| 你的库 | 评论 | ADB 桥 |
|---|---|---|
| 纯文字/方案知识库（多数人） | 想要团队 review → 开在线评论；否则不开 | 不开 |
| 硬件 / 嵌入式调试库 | 按需 | 开（改白名单成你的命令） |

- `library.js` 把目录/锚点（你要的）和**评论模块 + ADB 桥客户端**打包在一起。普通库整抄后可删掉评论/ADB 相关模块，只留目录/锚点/`target=_blank` 即可正常出站。
- `start_local_comment_server.sh` 会连带起 ADB 桥（`8765`）——只想要本地评论、不要 ADB 的，删掉脚本里启动 ADB 那段。

回到主流程见 [SKILL.md](../SKILL.md) 与 [setup-guide.md](setup-guide.md)。

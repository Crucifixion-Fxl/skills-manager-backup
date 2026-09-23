# 长报告写成飞书文档：谁产出谁发

定时分析、每日走查这类报告动辄几十行，直接发进 Buzz 频道会刷屏（镜像到飞书群后更明显）。规则：**完整内容写成飞书文档，频道里只发摘要和文档链接**。

## 何时使用

- 报告正文超过约 8 行、含表格、或按日／周重复产出（交付进展、数据复盘、pipeline 健康、架构坏味道、走查、客服日报等）。
- 短结论、行动消息、pickup／ack 不走文档，照常发在 Thread 里。

## 谁产出谁发

**哪个 agent 写的报告，就由那个 agent 用自己的 bot 身份建文档、授权群、发摘要。**三步都必须 `--as bot`，并且用该 agent 自己的隔离 lark-cli profile（PersonalAgent 的 app），不借用 owner 的默认配置。

**不得**用 owner 的 `--as user` 代劳：文档会挂在 owner 名下，群里看到的是 owner 发的，责任人和审计都对不上。缺权限时也不得退回 user 身份，见下文「缺 scope」。

## 一条命令：`feishu_doc_publish.py`

```bash
LARKSUITE_CLI_CONFIG_DIR=<agent 的 lark_config_dir> LARKSUITE_CLI_DATA_DIR=<agent 的 lark_data_dir> \
  python3 references/scripts/feishu_doc_publish.py --title "<标题>" --chat-id <chat_id> < report.md
# stdout：{"document_id": "...", "url": "https://….feishu.cn/docx/…"}
```

它替你处理下面几个坑，报告里的图片和表格原样带过去：

- **Buzz 上的图片**（正文里 `![](https://…/media/<sha>…)`）需要 Buzz 鉴权，飞书服务器拉不到，`docs +create` 会卡到 30 秒超时失败。脚本先用 `buzz media get` 下到本地，改成 `![](@./x.jpg)`（lark-cli 只读当前目录内的 `@./` 路径）；**永远不要只贴图片链接**，要上传成文档里的图。
- 正文里的 `<`、`~`、`$` 会被当成 XML 标签、删除线、公式，脚本在代码块之外转义。
- 长文档按「每张图一段」分块：先 `+create`，再逐块 `+update --command append`，失败重试；任何一块没传完就不授权给群、非 0 退出。
- 每次 `lark-cli` / `buzz media get` 调用都有硬超时（`--timeout`，默认 120 秒，超时会连同子进程整组杀掉）并按 `--retries` 重试；定时 Workflow 不会因为某次调用卡死而整轮挂住，最终失败就非 0 退出、stdout 为空、不授权给群。
- 代码围栏认 ``` 和 ~~~ 两种，围栏内的内容原样保留。

## 命令（手工做法，脚本做的就是这几步）

`<chat_id>` 是这个频道绑定的飞书群（见 [feishu-group-sync.md](feishu-group-sync.md)）。正文一律走 stdin，`--content` 不接受绝对路径。

```bash
export LARKSUITE_CLI_CONFIG_DIR=<agent 的 lark_config_dir>
export LARKSUITE_CLI_DATA_DIR=<agent 的 lark_data_dir>

# 1. 建文档（Markdown 经 stdin）；返回里取 document_id 和 url
lark-cli docs +create --as bot --doc-format markdown \
  --title "<频道> 走查日报 <YYYY-MM-DD>" --content - < report.md

# 2. 只读授权给群（--perm 只用 view）
lark-cli drive +member-add --as bot --token <document_id> --type docx \
  --member-type openchat --member-id <chat_id> --perm view --yes

# 3. 在群里发摘要 + 链接
lark-cli im +messages-send --as bot --chat-id <chat_id> \
  --text "<摘要 3～5 行>：<文档 url>"
```

频道已经镜像到飞书群的，Buzz Thread 里的摘要和链接会自动出现在群里，不用另发一条群消息；没有镜像的群才用第 3 步。再在 Buzz 频道同一 Thread 发一条摘要 + 文档链接（走原有的最终报告 helper 和站立受众通知，规则不变）。文档标题带日期，同一天重跑就更新原文档，不要再建一篇。

## 缺 scope：先预检，是给 owner 的阻塞项

新建 agent 应用只有基线 scope，没有文档权限。创建应用时就应把免审 scope 一并开通：`python3 references/scripts/feishu_scope_apply_url.py <app_id>` 出一条申请链接，owner 点一次（清单见 [feishu-agent-app-scopes.txt](feishu-agent-app-scopes.txt)，见 [feishu-group-sync.md](feishu-group-sync.md) 创建应用的第 3 步）。老应用同样用它补。第一次使用前用 `--dry-run` 之外的真实小文档试一次（标题写「[测试]」，事后删），失败时错误里会有 `app_scope_not_applied`，并列出缺的 scope：

| scope | 用于 |
|---|---|
| `docx:document`、`docx:document:create` | 建文档 |
| `docs:permission.member:create` | 把文档授权给群 |
| `docs:document.media:upload`、`drive:file:upload` | 文档里内嵌截图（图片上传） |
| `docs:document.content:read` | 读回文档内容做核对 |
| `im:message:send_as_bot` | 发群消息（发群同步已开通则已有） |

只跑文档流程，上面前三行加这两行共 6 个 scope 就够；`feishu-agent-app-scopes.txt` 的完整清单更大（含多维表格、云盘删除等写权限），是 owner 要求「新建应用时把免审 scope 一并开通」的整包，开之前按该 agent 的实际用途取舍。

**没有 API 可以给应用加 scope**（同 feishu-group-sync 的 LCV-11）。必须由 owner 到开放平台后台为该 app 开通并发版，报错里的 `console_url` 就是申请页。此时把「缺哪个 scope、哪个 app」作为阻塞项报给 owner，等开通后再继续；不得退回 `--as user`，也不得先发长报告到频道凑合。

## 不进文档的内容

文档在群里可读，遵守和频道消息同样的边界：不放 token、密钥、`open_id`、邮箱等身份材料；生产敏感数据只放证据链接。授权只给 `view`，不给 `edit`／`full_access`。

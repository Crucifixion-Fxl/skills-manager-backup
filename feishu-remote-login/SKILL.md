---
name: feishu-remote-login
description: 在没有图形界面的主机上，用无头 Chromium 完成「需要飞书账号登录」的浏览器操作（飞书 SAML/SSO 的内部站点、飞书开放平台、OAuth 授权页），二维码截图后发到 owner 的飞书私聊让他手机扫码，并盯着页面直到登录成功。当任务需要用 owner 的飞书身份登录某个网站、又没有 API 或服务账号可用时使用；也用于扫码后 SSO 报错、二维码过期、发图没有说明文字、登录态怎么保管这类问题。触发词：飞书扫码登录、二维码发飞书、远程登录、无头浏览器登录、SAML 飞书登录、saml_login_session_timeout。
---

# feishu-remote-login

## Description

**解决什么问题**：某个站点只能用飞书账号登录（SAML/OIDC SSO、飞书开放平台、OAuth 授权页），而执行任务的主机没有屏幕，登录页只给「飞书扫码」一种方式。做法是：主机上跑一个带持久 profile 的无头 Chromium，把登录页的二维码截下来、放大、连同一段说明文字发到 owner 的飞书私聊，owner 用手机扫一次码并确认，之后的操作由主机上的浏览器接着做。

**什么时候不用**：

- 有 API、服务账号、access token 可用时，先走那条路（见各平台自己的 skill）。本 skill 是最后手段，因为它用的是**人的完整飞书身份**。
- 只想给同事发消息、读文档：用 `lark-cli`，不用登录网页。
- 站点提供账号密码 / 短信登录：不需要本 skill。

**前置条件**：

| 需要 | 说明 |
|---|---|
| Node.js + `playwright-core` + 一份 Chromium | `npm i playwright-core`，`npx playwright-core install chromium`；运行时 `NODE_PATH` 要能解析到 `playwright-core` |
| `lark-cli` 的 bot 身份 | 能给 owner 发私聊（`--as bot --user-id <open_id>` 才有推送提醒） |
| owner 的 `open_id` | `lark-cli contact +search-user --query <姓名或邮箱> --as user` 查，注意区分同名的跨租户账号 |
| `python3`（标准库即可） | 放大二维码、解析 JSON；主机上通常没有 Pillow / ImageMagick |

**本 skill 提供的脚本**（`<skill_dir>/scripts/`）：

| 脚本 | 作用 |
|---|---|
| `remote_login.js` | 无头浏览器会话：持久 profile、按文件协议接收命令、随时刷新并截二维码、暴露当前 URL 和页面文字 |
| `send_qr.sh` | 让会话出一张新码 → 放大 → **先发文字说明再发图片**；没有说明文字就拒绝发送 |
| `watch_login.sh` | 发码后盯着页面：登录成功就退出，已扫待确认时绝不刷新，一直没人扫就有上限地补发 |
| `qr_enlarge.py` | 只用标准库把小尺寸（约 184 像素）的二维码放大 4 倍并加白边，让手机能从聊天图里扫到 |

`buzz-agent-setup` 里的 `feishu_browser.js` 是同一个文件协议的早期版本，专门服务于飞书应用注册；本 skill 是通用版，多了命令模式下的刷码、页面文字与 URL 暴露、可配置路径。

## Rules

1. **先找不需要人登录的办法。** API、服务账号、access token、已有的授权，能用就不要动 owner 的飞书身份。
2. **登录态 = owner 的完整飞书身份，不是某个站点的凭据。** 浏览器 profile（默认 `~/.cache/feishu-agent-browser`，可用 `REMOTE_LOGIN_PROFILE` 改）里存着它，带着邮件、文档、IM 的权限。只归 owner 本人和 owner 指定的个人 agent 使用，**其它 agent 一律不得读取或使用**，也不得把 profile、cookie、页面快照交出去。
3. **不要指望文件权限或 systemd 隔离它。** 同一 Unix UID 下的进程互相读得到文件，`0700` 不是隔离。用 systemd 的 `InaccessiblePaths=` 之前先实测：主机禁止非特权用户命名空间时它**静默无效**（沙箱里目录依然可见）。真隔离要靠独立 OS 用户 / 容器，做不到就如实告诉 owner「只能靠约定」。 **残余风险（明示）**：会话的控制通道 `cmd.txt` 同样没有调用者认证，同 UID 的任何进程都能驱动已登录的浏览器（`goto` / `fill` / `click`）。所以会话只在需要时起、用完立刻 `quit.txt`；要长期保留登录态，就放到独立的 OS 用户或容器里，而不是靠约定。
4. **登录用完默认删除，owner 明确说保留才保留。** 保留时把位置、只归谁、为什么留写进文档或 memory；不要悄悄留着。
5. **只在 owner 授权的范围内用登录**：具体的站点、具体的操作。到了权限不够的页面（例如站点里只是普通成员，建不了集成）就停下来找有权限的人，不要绕。
6. **登录换来的「人的 token / JWT / cookie」不给 agent**，除非 owner 明确把它作为一次例外，并且同时落实：只读白名单调用脚本、过期策略、审计、写进凭据表。例外要有记录，不能是「顺手放进 env」。
7. **发码只发给 owner 本人的私聊，并且每张图前面必须有文字说明**：写清「用途（登录哪个站点、为哪件事）、扫哪张、多久失效、扫完点什么」。说明里不写 token、cookie。脚本强制的是**必须有说明文字**：没有 `--context` / `--context-file` 就拒绝发送。收件人也由脚本强制：必须设置环境变量 `REMOTE_LOGIN_OWNER_OPEN_ID`（owner 的 open_id），`--to` 必须等于它；没设置或不相等都拒绝（退出码 2），`watch_login.sh` 启动时同样检查。把登录二维码发给同事，他扫码确认就等于用他的身份登录了这个会话。
8. **先握手再发码，只发单张，然后盯着页面。** 二维码约 25–35 秒失效，一刷新旧码立刻作废。让 owner 先说「好」再发；发完由 `watch_login.sh` 读页面判断进度，不要让 owner 回报「我扫了」。连续发很多张只会让人扫到过期的。
9. **页面显示「确认登录」时绝不刷新二维码**——这表示已经扫过、等手机确认，刷新会作废这次登录。页面里一直有静态文字「扫码成功」，不能拿它判断是否已扫。`send_qr.sh` 遇到这种页面直接返回 `awaiting_phone_confirmation`（退出码 6）而不刷新；`watch_login.sh` 按整份 `page.txt` 判断，不是只看前几行；会话本身在刷新前也会再查一次。
10. **会话工作目录必须是自己的 `0700` 目录**（`remote_login.js`、`send_qr.sh`、`watch_login.sh` 都会检查，不满足就拒绝）。`page.txt` / `page.html` / `page.png` 是已登录页面的原样内容，其中可能有 token 或个人信息，会话退出时自动清理，被 SIGKILL 杀掉的会话留下的文件在下次启动时清掉，用完仍要确认已删。写到磁盘的 URL 只有主机和路径，不含 query 和 fragment（OAuth 回调的一次性 `code` 在那里）；`data:`、`blob:` 这类把内容放在 URL 里的，只写协议名。日志和回复里也不要打印这类 URL、token、cookie。
11. **按 PID 精确停进程。** `ps | grep | kill` 或 `pkill -f` 会匹配到自己的命令行（命令里含被匹配的字面量），把当前 shell 杀掉（退出码 144）。拿 PID 后 `kill <pid>`，或把匹配串拆开写。

## 流程

以下 `<skill_dir>` 是本 skill 目录，`<dir>` 是会话工作目录（`mkdir -m 700`）。

**1. 起会话**（后台运行，起始 URL 是站点的 SSO 登录入口）

```bash
mkdir -m 700 "<dir>" && cd "<dir>"
NODE_PATH=<含 playwright-core 的 node_modules> nohup node <skill_dir>/scripts/remote_login.js "<站点登录页 URL>" > run.out 2>&1 &
# 起始页不在 accounts.feishu.cn 时会话直接进入命令模式（出现 ready.txt）
# 默认带 --no-sandbox（禁用非特权用户命名空间的主机上沙箱起不来）；能用沙箱就加 REMOTE_LOGIN_SANDBOX=1
```

**2. 走到飞书扫码页**（命令模式，用文件传命令，写完再改名，避免读到半个文件）

```bash
printf '%s' '{"click":["text=<站点上的 SSO 按钮文字>"],"wait":6000}' > cmd.txt.tmp && mv cmd.txt.tmp cmd.txt
# 结果在 result.txt，页面快照在 page.txt / page.html / page.png，当前 URL 在 url.txt
```

到了 `accounts.feishu.cn` 且页面写着「扫码登录」，就可以发码了。

**3. 握手：告诉 owner 「我马上发一张码，请先打开手机飞书的扫一扫，准备好回我一句」，收到再继续。**

**4. 发第一张码，然后盯着**

```bash
<skill_dir>/scripts/send_qr.sh --workdir "<dir>" --to <owner_open_id> --label "第1张" \
  --context "用途：正在用你的身份登录 <站点>，为 <哪件事>。"
<skill_dir>/scripts/watch_login.sh --workdir "<dir>" --to <owner_open_id> \
  --context "用途：正在用你的身份登录 <站点>，为 <哪件事>。" --max 150 --resend 45 --max-resends 3
```

两个脚本的最后一行都是 JSON，`status` 和退出码如下：

| 脚本 | `status`（退出码） |
|---|---|
| `send_qr.sh` | `sent`（0）· `logged_in` 页面已离开飞书、无需发码（0）· `dry_run`（0）· `awaiting_phone_confirmation` 页面显示「确认登录」，不刷新（6）· `error`：用法错误（2）、会话没出码（3）、发文字失败（4）、发图片失败（5） |
| `watch_login.sh` | `logged_in`（0）· `timeout`（1）· `session_ended`（3，会话已退出、`url.txt` 没了，别干等）· `interrupted`（130）· `error` 用法错误（2） |

**5. 登录成功之后**

- URL 已离开 `accounts.feishu.cn`，但可能落在 SSO 的错误页。**`saml_login_session_timeout`**：SAML 会话从你点站点的 SSO 按钮就开始计时，扫码确认太慢就超时了。飞书会话这时已经建立，回到站点重新点一次 SSO，通常直接通过，不再要码。
- 想在登录后查看某个站点的接口数据，可以直接 `goto` 那个接口的 URL，从 `page.txt` 读 JSON（同源 cookie 已带上）。
- 拿到目标站点的页面后，用 `goto` / `click` / `fill` 继续操作；改动要小步、可回读。

**6. 收尾**

- 做完了：`printf quit > quit.txt` 让会话退出，删除 `<dir>`；owner 没有要求保留就**删除 profile 目录**（规则 4）。
- owner 要求保留：记录 profile 位置和「只归谁用」，并提醒规则 2、3 的残余风险。

## 文件协议

会话分两个阶段：**等登录**（页面还在 `accounts.feishu.cn`，只认这个主机，URL 里含这串字不算）和**命令模式**（有 `ready.txt`，最长 60 分钟）。等登录最多 30 分钟（`REMOTE_LOGIN_LOGIN_TIMEOUT` 秒可改），超时就放弃：退出码 3，不写 `ready.txt`。`about:blank` 和浏览器错误页不算登录成功。

| 文件 | 方向 | 内容 |
|---|---|---|
| `cmd.txt` | 你 → 会话（命令模式） | JSON：`goto`、`fill:[[选择器,值]]`、`click:[选择器]`、`shoot:true`（刷新过期的码并截图）、`wait:毫秒`。读取后会话删除它；坏 JSON 只算这一步失败。先写 `cmd.txt.tmp` 再改名 |
| `shoot.txt` | 你 → 会话（等登录阶段） | 要新码；命令模式下无效，改用 `cmd.txt` 的 `shoot:true` |
| `quit.txt` | 你 → 会话 | 请求退出，**两个阶段都生效**；启动时先清掉旧的 |
| `result.txt` | 会话 → 你 | `OK <主机+路径>` 或 `ERR <原因>`；每条命令开始前先清掉旧的，读不到上一条的结果 |
| `url.txt` / `page.txt` | 会话 → 你 | 当前页面的主机+路径与文字，约每 1.2 秒更新（执行命令或 `wait` 时会停一会儿），两个阶段都有 |
| `state.txt` | 会话 → 你 | `QR <n> <时间戳>`（每出一张码变一次）或 `LOGGED_IN <主机+路径>`（只表示页面已离开 `accounts.feishu.cn`，判断「已登录」以 `url.txt` 为准） |
| `qr.png` | 会话 → 你 | 最新二维码的 canvas 截图（约 184×184，随页面而定）；`send_qr.sh` 放大成 `qr-big.png` 再发 |
| `ready.txt` | 会话 → 你 | 登录成功、进入命令模式 |
| `run.pid` / `run.log` / `error.txt` | 会话 → 你 | 进程号（要强制停止时按 PID 杀）、运行日志、未捕获错误的原因（退出码 1） |

退出码：0 正常退出；1 未捕获的错误；2 工作目录不私有或 profile 不可用；3 等登录超时；129 / 130 / 143 收到 SIGHUP / SIGINT / SIGTERM。会话退出时删除 `url.txt`、`page.*`、`qr.png`、`state.txt`、`ready.txt`、`cmd.txt`、`cmd.txt.tmp`、`shoot.txt`、`result.txt`、`quit.txt`；`run.log`、`run.pid`、`error.txt`、`qr-big.png` 留给你排查，下次启动时清掉。

环境变量：

- `REMOTE_LOGIN_PROFILE`：profile 目录；不存在则以 `0700` 创建，已存在会收紧到 `0700`。
- `CHROMIUM_PATH`：默认取 `~/.cache/ms-playwright/chromium-*` 里最新的（仅 Linux 布局）。
- `REMOTE_LOGIN_SANDBOX=1`：启用 Chromium 沙箱。默认 `--no-sandbox`，因为禁用非特权用户命名空间的主机上沙箱起不来；代价是渲染进程被攻破就等于以当前用户身份执行，所以别用会话去打开不可信页面。
- `REMOTE_LOGIN_LOGIN_TIMEOUT`：等登录的秒数，默认 1800。
- `REMOTE_LOGIN_OWNER_OPEN_ID`：**必填**。`send_qr.sh` / `watch_login.sh` 只允许发给这个 `open_id`，没设置就拒绝（见规则 7）。
- `LARK_CLI`：lark-cli 可执行文件，默认 `lark-cli`。

## 踩坑

| 现象 | 原因 | 处理 |
|---|---|---|
| owner 说「我扫了」，页面还是二维码 | 扫的是已过期的旧码；或发得太密，最新一张已被刷新作废 | 不要连发。握手后发单张，用 `watch_login.sh` 看页面；来不及就「再发」一张新的 |
| 登录成功后落在 `sso-error`，`saml_login_session_timeout` | SAML 会话在点 SSO 按钮时开始计时，扫码确认太久 | 回站点重新点 SSO；飞书会话已建立，不再扫码 |
| 页面文字里有「扫码成功」但没人扫 | 那是页面上一直存在的静态文字 | 只认「确认登录」判断「已扫待确认」；见规则 9 |
| `send_qr.sh` 返回 `awaiting_phone_confirmation`（退出码 6） | 页面显示「确认登录」，owner 已扫码、在手机上确认 | 等，别刷新；确认成功后 `watch_login.sh` 会看到 URL 离开飞书 |
| `send_qr.sh` 报 `logged_in` 但你没看到登录 | 页面 URL 已离开 `accounts.feishu.cn`（可能是 SSO 的错误页） | 看 `page.txt`；`saml_login_session_timeout` 见上一条 |
| 会话刚起就写 `LOGGED_IN`，其实没登录 | `goto` 在 `domcontentloaded` 就返回，应用页在前端再跳到登录页，这一瞬间 URL 还是 `open.feishu.cn` | 现在脚本等 3 秒后复查 URL，还在登录页就继续等码；自己判断登录时也别只看一次 URL，看稳定后的 URL 和页面文字 |
| `touch quit.txt` 后立刻 `rm -rf` 工作目录，怕留下 Chromium | 会话读不到 `quit.txt`，下一次写文件时因目录没了报错 | 现在这种情况脚本也会退出并先关浏览器；收尾仍要自检没有残留的 node / Chromium（按 PID 查，不用 `pkill -f`） |
| 启动报 `a session is already running in this directory` | 这个目录里已有一个还活着的 `remote_login.js`（run.pid 指着它）；再起一个会清掉它的状态文件，所以启动时用原子独占的方式认领目录（同时多个启动也只有一个成功） | 先用 `quit.txt` 停掉旧的，或换一个工作目录。旧进程已死、pid 被别的进程复用或文件内容不对时，新会话会自动接管，不用手删 `run.pid` |
| 二维码发出去扫不了 | 截图只有 184 像素，聊天里被压缩 | 用 `qr_enlarge.py` 放大 4 倍并加白边（`send_qr.sh` 已包含） |
| `lark-cli --image` 报路径不合法 | 只接受相对路径，绝对路径和 `..` 被拒 | 在会话目录里执行，用 `qr-big.png` |
| 解析 `lark-cli` 输出失败 | 发图时会先输出 `uploading image: ...` 再输出 JSON | 从第一个 `{` 开始解析 |
| owner 收不到提醒 | `--as user` 发给自己不会推送 | 用 `--as bot --user-id <open_id>` |
| 命令说「找不到 Chromium」 | 没装或路径不对 | `npx playwright-core install chromium`，或设 `CHROMIUM_PATH` |
| 停进程时把自己的 shell 杀了（退出码 144） | 匹配串出现在自己的命令行里 | 见规则 11 |
| 别的 agent 也能用上 owner 的 JWT | 授权脚本把 token 缓存到了 `~/.troubleshooting-token-*` 这类家目录文件，同一个 skill 会读它 | 换 OAuth token 时用临时 `HOME`，用完删掉，见 `references/oauth-callback.md` |
| 站点里只是普通成员，建不了集成 | 登录只是换了身份，权限没变 | 停下来找有权限的 Owner，不要绕（规则 5） |

## Examples

### Bad

```
AI：登录页要扫码 → 把 184 像素的原图直接发到飞书，没有任何说明；
    然后每 30 秒发一张，一共发了 7 张；owner 回「我扫好了」，页面却还是二维码。
→ 没有说明文字、连发导致扫到过期的码、没有读页面判断进度。
```

```
AI：登录成功后觉得「以后还要用」，悄悄把 profile 留在主机上，也没告诉 owner；
    又把换到的 JWT 放进所有 agent 都读得到的家目录缓存文件。
→ 违反规则 2、4、6。
```

### Good

```
AI：先查有没有服务账号，没有 → 跟 owner 说「我马上发一张码，请先打开扫一扫」→ owner 说好
    → send_qr.sh 发一条说明文字（用途、扫哪张、25 秒）和一张放大的码
    → watch_login.sh 读页面：出现「请在飞书移动端确认登录」就不动，URL 离开 accounts.feishu.cn 就继续
    → 落在 saml_login_session_timeout：回站点重新点 SSO，直接进入
    → 做完事，owner 没说保留，就退出会话并删除 profile。
```

```
AI：拿到的登录态要给某个 agent 用：先问 owner；owner 明确同意后，只给「人的 JWT」，
    经只读白名单脚本调用、24 小时过期、有审计和每日上限，并写进 Canvas 的凭据表；
    浏览器 profile（飞书登录态）仍然不给任何 agent。
```

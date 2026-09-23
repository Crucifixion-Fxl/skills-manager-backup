# 个人 Channel todo 同步 timer 部署（owner `systemd --user`）

本页是 [ADR-0013](../../../../docs/05-adr/0013-run-personal-todo-sync-with-the-owners-pat.md) 的 wiring 契约。每个人一对 `gitlab-todo-sync-<name>.service`／`.timer`，跑在这个人自己的主机、自己的 Unix UID 下。结构沿用 [GitLab → Buzz 同步 timer](README.md)（ADR-0008），差别在凭据：这里用的是**本人的 GitLab PAT**。

## 边界

- `<immutable-release>` 是已评审的 40 位 commit SHA 固定副本（owner 所有、组／其他不可写），必须含 `scripts/gitlab_todo_sync.py` 及其全部依赖：`scripts/gitlab_buzz_sync.py`、`scripts/buzz_responsible_mentions.py`、`references/scripts/nostrkit.py`（少任一个会 `ModuleNotFoundError`）。禁止 `current`、`latest`、branch checkout 或自动更新的 plugin cache。
- 专用 env 文件 `~/.config/buzz/todo/<name>.env`：owner 所有、0600、regular、非 symlink，**独立于任何 Agent 的 env 文件，也不被任何 Agent launcher `source`**。它只放：`BUZZ_RELAY_URL`（relay origin，`https://` 与 `wss://` 都接受，`sync.validate_relay_url` 允许；`ws`／`http` 仅限回环地址）、`BUZZ_PRIVATE_KEY`（`<name>-todo` 发布者的私钥）、`BUZZ_AUTH_TAG`、`GITLAB_TODO_TOKEN`（本人 PAT）、`GITLAB_TODO_CONFIG`（配置文件绝对路径）。值不进入 Git、Canvas、argv、unit 文件或日志。
- launcher 只把这十个名字交给入口：`HOME`、`USER`、`LOGNAME`、`PATH`、`LANG`、`BUZZ_RELAY_URL`、`BUZZ_PRIVATE_KEY`、`BUZZ_AUTH_TAG`、`GITLAB_TODO_TOKEN`、`GITLAB_TODO_CONFIG`。其余**所有合法的 shell 标识符名称**（例如 env 里的 `GITLAB_TOKEN`、模型密钥）一律丢弃。名称含点号或连字符的**非标识符**环境项（如 `a.b`、`x-y`）不会成为 shell 变量，白名单删除看不见它们，bash 会**原样透传**给入口；入口一个都不读，所以无害。父环境或 env 文件里导出的 bash 函数（`BASH_FUNC_*`，`export -f`）也一并丢弃（白名单删除只看变量，所以先 `unset -f`）。
- launcher 对入口路径做严格校验：必须匹配 `^/[A-Za-z0-9_./-]+/scripts/gitlab_todo_sync\.py$`，用固定 Python 的 `os.path.realpath` 验证 canonical 路径（拒绝 `..` 与用户可控符号链接目录）；macOS 只额外允许系统固定的 `/var/...`→`/private/var/...` 等价映射，其余 realpath 差异仍拒绝。入口必须是常规非 symlink 文件，且**三处**——入口文件、它所在的 `scripts/` 目录、release 根目录（`scripts/` 的父目录）——属主都必须是当前用户，并且都不能被组／其他写。解释器只认 `/usr/bin/python3`。
- 配置文件（`gitlab-todo-sync.example.json` 复制而来）建议放 `~/.config/buzz/todo/<name>.json`，`GITLAB_TODO_CONFIG` 写它的绝对路径；`state_dir`（配置里必填的绝对路径）建议 `~/.local/state/buzz-todo/<name>`。两者都是 owner 所有：配置 0600，state 0700。
- 配置里 `todo` 段的可选键 `todo.done_emojis`：字符串列表，缺省 `["✅"]`，示例配置里已写出。列表里任一表情都算完成信号（见下文 `marked_done`），**第一个**还会写进每条待办消息的完成指引行（「完成后点 ✅（表情里搜 check）或在本 Thread 回复 …」；只在第一个表情是 ✅ 时带「（表情里搜 check）」，因为 Buzz Desktop 的表情选择器里要搜 `check` 才找得到 ✅，别的表情不知道搜索名，不带）。每项 ≤ 32 个字符、无空白与控制字符，比较时忽略 U+FE0F（`✔` 与 `✔️` 是同一个），去掉 U+FE0F 后不能重复；违反则整轮拒绝启动。改了它之后，个人 Workflow 唤醒文本里写的 ✅ 也要跟着改（见 `workflows/personal-todo-wake.yaml` 的注释）。
- **同一 UID 下 0600 不隔离本机 Agent**。这是 ADR-0013 明示接受的例外，不是隔离：强制边界是 PAT ≤ 30 天有效期、代码端点白名单、只对本脚本投递过的 todo 回写、单真人 Channel 门禁，以及随时可在 GitLab 撤销。
- PAT：scope `api`，有效期 ≤ 30 天，属主是本人。**自助端点 `POST /user/personal_access_tokens` 只允许 `k8s_proxy`**（要 `api` 会得到 `scopes does not have a valid value`），所以 `api` 令牌只有两条路：本人在 GitLab UI（User settings → Access tokens）签；或**实例管理员**用管理员端点 `POST /users/:id/personal_access_tokens`（JSON body，`scopes` 必须是数组，写法见 [personal-channel.md](../personal-channel.md) 的「三、必须人来做」）。**用户明确授权时**可由 AI 经用户本地已登录的 `glab` 走管理员端点创建（`glab` 默认指向 gitlab.com，要 `--hostname gitlab.addx.ai` 或 `GITLAB_HOST=gitlab.addx.ai`，否则 `401`）：令牌值**只写进 0600 env**，不打印、不进 argv、不贴进聊天或任何 Agent 可见的地方，留一份不含密钥的 receipt。**没有明确授权时**由本人在自己的终端写进 env，用不回显的输入：

```bash
# 在你自己的终端里跑，不要通过 AI 会话
umask 077; mkdir -p ~/.config/buzz/todo
read -rs -p 'GitLab PAT: ' T; echo
printf 'GITLAB_TODO_TOKEN=%s\n' "$T" >> ~/.config/buzz/todo/<name>.env; unset T
chmod 600 ~/.config/buzz/todo/<name>.env
```

## 1. launcher（环境变量白名单）

保存为 `~/.config/buzz/todo/gitlab-todo-sync-launch.sh`，owner 所有、mode 0700。

<!-- template:todo-timer-launcher -->
```bash
#!/bin/bash
# gitlab-todo-sync-launch.sh: owner-fixed whitelist launcher for gitlab_todo_sync.py (ADR-0013).
set -euo pipefail
umask 077
LC_ALL=C  # the regular expression below must be ASCII-only whatever the caller's locale is
SAFE_PATH=/usr/local/bin:/usr/bin:/bin
ENTRY_RE='^/[A-Za-z0-9_./-]+/scripts/gitlab_todo_sync\.py$'

fail() { printf 'gitlab-todo-sync-launch: %s\n' "$1" >&2; exit 2; }
canonical_path() {
  "$PYTHON" -c 'import os,sys
raw=sys.argv[1]
real=os.path.realpath(raw)
raise SystemExit(0 if real == raw or (sys.platform == "darwin" and raw.startswith("/var/") and real == "/private" + raw) else 1)' "$1"
}
# $1 is owned by the current user and neither group- nor other-writable; $2 names it in the error.
private_path() {
  [ "$("$PYTHON" -c 'import os,sys; print(os.stat(sys.argv[1], follow_symlinks=False).st_uid)' "$1")" = "$(id -u)" ] || fail "$2 must be owned by the current user"
  [ $(( 8#$("$PYTHON" -c 'import os,stat,sys; print(format(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode), "o"))' "$1") & 8#022 )) -eq 0 ] || fail "$2 must not be group- or other-writable"
}

[ "$#" -eq 2 ] || fail "usage: /usr/bin/python3 <immutable-release>/scripts/gitlab_todo_sync.py"
[ "$1" = /usr/bin/python3 ] || fail "interpreter must be /usr/bin/python3"
PYTHON="$1"
[[ "$2" =~ $ENTRY_RE ]] || fail "entrypoint must be <immutable-release>/scripts/gitlab_todo_sync.py"
[ ! -L "$2" ] && [ -f "$2" ] || fail "entrypoint must be a regular non-symlink file"
canonical_path "$2" || fail "entrypoint path must be canonical (no .. and no user-controlled symlinked component)"
private_path "$2" "entrypoint"
private_path "${2%/*}" "entrypoint directory"
private_path "${2%/*/*}" "release directory"

ENVFILE="${BUZZ_TODO_ENV_FILE:-}"
[ -n "$ENVFILE" ] || fail "BUZZ_TODO_ENV_FILE is required"
[ ! -L "$ENVFILE" ] || fail "env file must not be a symlink"
[ -f "$ENVFILE" ] || fail "env file must be a regular file"
canonical_path "$ENVFILE" || fail "env file path must be canonical"
[ "$("$PYTHON" -c 'import os,sys; print(os.stat(sys.argv[1], follow_symlinks=False).st_uid)' "$ENVFILE")" = "$(id -u)" ] || fail "env file must be owned by the current user"
[ "$("$PYTHON" -c 'import os,stat,sys; print(format(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode), "o"))' "$ENVFILE")" = 600 ] || fail "env file mode must be 0600"

RUN_HOME="${HOME:?}"
RUN_USER="${USER:-$(id -un)}"
RUN_LOGNAME="${LOGNAME:-$RUN_USER}"
set -a
# shellcheck source=/dev/null
. "$ENVFILE"
set +a
KEEP=" HOME USER LOGNAME PATH LANG BUZZ_RELAY_URL BUZZ_PRIVATE_KEY BUZZ_AUTH_TAG GITLAB_TODO_TOKEN GITLAB_TODO_CONFIG "
for key in BUZZ_RELAY_URL BUZZ_PRIVATE_KEY BUZZ_AUTH_TAG GITLAB_TODO_TOKEN GITLAB_TODO_CONFIG; do
  [ -n "${!key-}" ] || fail "missing $key in env file"
done
# Exported functions (BASH_FUNC_* from the parent, or `export -f` in the env file) are not variables, so the
# removal loop below cannot see them and they would reach the entrypoint. Drop every function except fail,
# and make sure fail itself is not exported.
while IFS= read -r fn; do
  [ "$fn" = fail ] || unset -f "$fn"
done < <(compgen -A function)
export -nf fail
# Whitelist by removal: every exported name that is a valid shell identifier and not in KEEP is dropped.
# Names that are not valid shell identifiers (a.b, x-y) never become shell variables: bash passes them through
# untouched, and the entrypoint reads none of them.
# Values stay in the environment (0400 /proc/<pid>/environ) and never become argv.
for name in $(compgen -e); do
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  case "$KEEP" in *" $name "*) ;; *) unset "$name" ;; esac
done
export HOME="$RUN_HOME" USER="$RUN_USER" LOGNAME="$RUN_LOGNAME" PATH="$SAFE_PATH" LANG=C.UTF-8
cd /
# bash re-exports SHLVL and _ to children; env -u drops them without carrying any value in argv.
exec /usr/bin/env -u SHLVL -u _ "$1" "$2"
```

launcher 出错只写 stderr（进 journal），stdout 为空。入口 `gitlab_todo_sync.py` 自身的 argparse 也拒绝任何参数。

## 2. unit 模板

替换 `<name>`（小写短名）与 `<immutable-release>` 后保存到 `~/.config/systemd/user/`。unit 文件不含 secret，也不使用 `EnvironmentFile=`（那会把 env 里所有键原样传进来）。

<!-- template:todo-timer-service -->
```ini
# gitlab-todo-sync-<name>.service
[Unit]
Description=GitLab todo to personal Buzz Channel for <name> (owner PAT, deterministic, ADR-0013)
After=network-online.target

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=yes
TimeoutStartSec=5min
Environment=BUZZ_TODO_ENV_FILE=%h/.config/buzz/todo/<name>.env
ExecStart=%h/.config/buzz/todo/gitlab-todo-sync-launch.sh /usr/bin/python3 <immutable-release>/scripts/gitlab_todo_sync.py
```

<!-- template:todo-timer-timer -->
```ini
# gitlab-todo-sync-<name>.timer
[Unit]
Description=Pull GitLab todos for <name> every 600 seconds

[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitActiveSec=600
Persistent=true
Unit=gitlab-todo-sync-<name>.service

[Install]
WantedBy=timers.target
```

- `OnUnitActiveSec=600` 以上一轮开始为基准，就是「每 10 分钟」；`Type=oneshot` 的一轮没结束时不会并发第二轮。
- 停机期间新增的 todo 由下一轮补齐：GitLab 上的 pending todo 是事实来源，`todo-state.json` 只负责去重与对账。
- 无人登录的主机先 `loginctl enable-linger "$USER"`。

## 3. 启用、手动运行、观察

```bash
systemctl --user daemon-reload
# 阶段 0：先手动跑一轮，确认 exit 0、个人 Channel 只出现你预期的 todo、journal 无错误
# 阶段 0 还要核对 channels members（见下文「阶段 0 要核对成员列表」）
systemctl --user start gitlab-todo-sync-<name>.service
journalctl --user -u gitlab-todo-sync-<name>.service -n 30 --no-pager
# 阶段 0 通过后再启用定时
systemctl --user enable --now gitlab-todo-sync-<name>.timer
systemctl --user list-timers 'gitlab-todo-sync-*'
```

- 每轮 stdout 是一个 JSON：`{"status":"ok","marked_done":n,"resolved":n,"rejected":n,"truncated":false,"seen":n,"delivered":n,"filtered":n,"invalid":n}`。
  - `marked_done`：本轮对 GitLab 调了 `mark_as_done` 的条数。完成信号有两种并存，都要来自 `done_authors`、晚于投递时间，同一条待办不论收到几个只算一次：① 可信作者回了 `todo:done:<id>`，且**必须是对该待办 Thread 的回复**：标记事件的 `e` tag 指向那条待办的消息或它所在 Thread 的根；发在别处的标记被忽略，待办保持 `ACKED`；② 可信作者对**那条待办消息本身**点了 ✅ reaction（`todo.done_emojis` 里的任一表情，缺省 ✅）：reaction 的 `e` tag 必须等于该待办的消息 id，点在 Thread 根、Thread 里别的消息上不算，`👀` 之类别的表情不算，撤销的不算。每轮只在有等待完成的 `ACKED` 待办时才读这两类事件（`messages get`，文字 `--kinds 9`、reaction `--kinds 7`，同一个时间窗口：读取窗口从最老的待确认投递往前 300 秒开始，与「晚于投递时间（留 300 秒余量）」的判定下界一致，最多回看 30 天）；读 reaction 失败与读文字失败同样处理：不拖停投递，本轮以该错误退出码 1，下一轮重试。
  - `resolved`：本脚本投递过、但已不在 GitLab pending 列表里的条数（你直接在 GitLab 里处理掉了），状态转 `RESOLVED`，此后不再为它们扫描频道。同一个 id 之后又出现在 pending 列表里，会从 `RESOLVED` 退回 `ACKED`（不重发，不计入这里）。
  - `delivered`：本轮新发出的消息条数。
  - `rejected`：Buzz 明确拒收、且**同轮之后有一次发送成功**（证明拒收是逐条的）的条数；见下文「state 里的状态」。系统性拒收（连续 2 条被拒，或被拒后本轮再没有成功的发送）**不计入这里**，而是整轮失败：`Buzz rejected …`，退出码 1，被拒的待办下一轮重试。
  - `truncated`：pending 列表没有拉全时为 `true`，两种原因：超过上限（30 页，每页 100，共 3000 条），或 60 秒 GitLab 预算在翻页中途用完（第一页就没有预算则整轮失败，不算截断）。两者都**不失败**，只处理已拉到的部分，并且本轮**不做 `RESOLVED` 收敛**（列表被截断时，缺一个 id 不代表它已被处理）。出现 `truncated:true` 说明 GitLab 里堆了几千条 pending todo 或 GitLab 很慢，请去清理或查网络。
  - `seen`：本轮拉到的 pending 总数（含已投递的和超过 `max_per_run` 留到下一轮的）；`filtered`：早于 `since` 或不在 `actions` 里的；`invalid`：id 或 `created_at` 无法解析的。
  - 失败带脱敏 `error`（PAT 与 `BUZZ_PRIVATE_KEY` 的值即使出现在错误文本里也会被替换成 `***`），退出码 1。
- `status=locked` 退出码 0：另一进程还持有 state 锁，下一轮续上。
- `mark_as_done` 失败（GitLab 5xx、403、PAT 权限变了）：一条失败不会挡住**后面的标记**（同一轮里其余可信标记照常写），也不会拖停投递；本轮把能标的标完、新待办发完，再以**最后一个错误**退出码 1。失败的那条仍是 `ACKED`，下一轮重试；同一个失败 id 被两位作者各标一次，本轮也只打一次 GitLab。
- **阶段 0 要核对成员列表**：用 owner 绝对路径 CLI 跑 `"$BUZZ_CLI" channels members --channel <CH>`，确认列出的成员只有 owner、发布者与 `done_authors`。若它还列出 relay Workflow 服务公钥之类的系统成员，成员集合门禁会让每一轮整轮失败关闭（零消息），必须在阶段 0 就发现并决定怎么处理（本仓尚未验证 relay 0.2.1 是否会列出）。
- **失败不会在 Channel 里出现**：PAT 过期、被撤销或个人 Channel 多了第二个真人成员，同步都会停下，唯一记录是 user journal。请在 PAT 到期前一周设日历提醒，或定期看 `systemctl --user list-timers` 与 journal。

### state 里的状态

`state_dir/todo-state.json` 的 `todos` 里每条记录有一个状态：

| 状态 | 含义 |
|---|---|
| `PENDING` | 已落盘、正在发送或结果未知；下一轮先在频道里按末行 header 对账（只认发布者发的事件），找到就转 `ACKED`，找不到就丢弃并重发 |
| `ACKED` | 已发出；等可信作者在该待办的 Thread 里回复 `todo:done:<id>`（发在别处的标记被忽略），或对该待办消息本身点 ✅ reaction（点在别处、别的表情被忽略），或等你在 GitLab 里处理（转 `RESOLVED`） |
| `DONE` | 已对 GitLab 调 `mark_as_done`；90 天后删除 |
| `RESOLVED` | 已不在 GitLab pending 列表（你直接在 GitLab 里处理了）；不再重发、不再扫描；90 天后删除。同一 id 又出现在 pending 列表里（例如你在 GitLab 里恢复了它）会退回 `ACKED`，仍不重发，之后它的 `todo:done` 标记照常生效 |
| `REJECTED` | Buzz 明确拒收这一条（回复被拒则先清掉该目标的 Thread 记录改发顶层重试一次，仍被拒），**并且同轮之后有一次发送成功**（有后续成功可证明拒收是逐条的）才记。被拒的记录在此之前只是被删除、不落 `REJECTED`：系统性拒收（连续 2 条被拒，或被拒后本轮再没有成功的发送）因此不会静默丢待办，而是整轮失败（退出码 1），下一轮重试。落成 `REJECTED` 后**终态、不再重试、也不会被 prune**，需要人工看一眼原因（消息是否超限、内容是否被 relay 拒），处理后手动删掉该记录才会重发 |

state 文件损坏、不是合法 JSON 或记录形状不对时，整轮失败关闭并保持文件原样：不会静默重置（重置会把所有 pending todo 重新发一遍）。

## 4. 首次 backfill 与 `since`

`todo.since` 之前创建的 pending todo 永远不发。首次启用先设为「现在」，避免把积压的旧待办一次性灌进 Channel；确实想补发，再把 `since` 往前调，配合 `max_per_run`（默认 20）分几轮送出。

## 5. 轮换、停用与撤销

```bash
# 轮换：先在 GitLab 签发新 PAT，再只替换 env 里的 GITLAB_TODO_TOKEN，然后手动跑一轮确认
systemctl --user start gitlab-todo-sync-<name>.service
# 停用：先停 timer，再确认 service 不在运行；state 保留，重启后从 state 续上
systemctl --user disable --now gitlab-todo-sync-<name>.timer
systemctl --user is-active gitlab-todo-sync-<name>.service
# 撤销：在 GitLab 撤销该 PAT（User settings → Access tokens），并删掉 env 里的 GITLAB_TODO_TOKEN 行
```

重放一条 todo（验收 Workflow 时用）：先停 timer，编辑 `state_dir/todo-state.json`，在 `todos` 里删掉 `"<id>"` 那一项，再手动跑一轮；GitLab 上它仍是 pending，会被重新投递。注意：若这个 Issue／MR 的 Thread 已记录在 `threads` 里，重放的消息是**回复进原 Thread**，不是新的顶层消息；要顶层消息就把对应的 `threads` 项也删掉。这类回复能否触发 `message_posted` Workflow 需要真机确认。

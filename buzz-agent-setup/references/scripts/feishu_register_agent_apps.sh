#!/bin/bash
# 给一个 agent 注册 PersonalAgent 应用：起 `lark-cli config init --new` → 拿 user_code → 驱动已登录的无头浏览器
# 在确认页填应用名并提交 → 等 lark-cli 结束 → 回读 appId 与 app_name 并逐字核对。
#
# 用法：feishu_register_agent_apps.sh <agent 名>
#   agent 名：小写字母开头，其后小写字母 / 数字 / 连字符，最长 32。应用名就用它。
# 环境变量：
#   FEISHU_BROWSER_DIR   必填。feishu_browser.js 运行的目录：必须是自己的 0700 目录，且浏览器会话已登录（有 ready.txt）。
#   LARK_CLI             lark-cli 的路径，默认 lark-cli。
# 依赖：bash、python3（PATH 上找到哪个用哪个，不假设位置；找不到就退出码 2、什么都不做）。
#   LARK_AGENTS_HOME     每个 agent 一套隔离目录 <home>/<name>/{config,data}，默认 ~/.config/lark-agents
#                        （LARKSUITE_CLI_CONFIG_DIR 与 LARKSUITE_CLI_DATA_DIR 都要设：Linux 上 keychain 在 DATA_DIR 里）。
#   URL_WAIT_SECONDS / STEP_TIMEOUT_SECONDS / SUBMIT_WAIT_SECONDS   等 user_code / 浏览器每一步 / 创建完成的上限（40 / 60 / 300）。
# 退出码：0 成功；1 失败（原因在 stderr）；2 用法或环境不对（什么都没做）。
#
# 只有 app_id 合法、且回读的 app_name 与 agent 名逐字相等，才在 stdout 打印唯一的成功行
#   <name> -> app_id=<id> app_name=<name>
# 任何一步失败都非零退出、不打印这一行，并收掉后台的 lark-cli。不回显 API 响应里的其它字段。
#
# 威胁模型：单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程。
# 下面对符号链接的处理（目录、ready.txt、scratch 文件）都是这个范围内的尽力而为：检查之后、使用之前仍有一个窗口，
# 能在这个窗口里换文件的只有同一个用户下的进程，本来就能读写这些目录；jchen 已接受这个残余竞态（skills#117）。
set -uo pipefail
umask 077

fail() { echo "feishu_register_agent_apps: $*" >&2; exit 1; }
usage() { echo "feishu_register_agent_apps: $*" >&2; exit 2; }

A="${1-}"
[[ "$A" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || usage "agent name must be a lowercase letter followed by lowercase letters, digits or hyphens (at most 32)"
BR="${FEISHU_BROWSER_DIR-}"
[ -n "$BR" ] || usage "FEISHU_BROWSER_DIR must name the private directory feishu_browser.js runs in"
{ [ -d "$BR" ] && [ ! -L "$BR" ]; } || usage "FEISHU_BROWSER_DIR is not a directory"
# python3 只在这里解析一次（路径安全校验、拼浏览器命令、读 lark-cli JSON 都用它）。缺就在什么都没做之前退出：
# 别先起 lark-cli、走到第 N 步才发现拼不出命令，更别等 app 已经建好、回读时才因为没有 python3 而失败。
PY="$(command -v python3)" || PY=""
[ -n "$PY" ] || usage "python3 is required (it builds the browser commands and reads lark-cli's JSON) but is not on PATH"
path_uid() { "$PY" -c 'import os,sys; print(os.stat(sys.argv[1], follow_symlinks=False).st_uid)' "$1"; }
path_mode() { "$PY" -c 'import os,stat,sys; print(format(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode), "o"))' "$1"; }
{ [ "$(path_mode "$BR")" = "700" ] && [ "$(path_uid "$BR")" = "$(id -u)" ]; } || usage "FEISHU_BROWSER_DIR must be a private directory (0700, owned by you): it holds page snapshots of a logged-in session and takes commands"
{ [ -f "$BR/ready.txt" ] && [ ! -L "$BR/ready.txt" ]; } || usage "the browser session is not logged in (FEISHU_BROWSER_DIR has no ready.txt written by feishu_browser.js: it must be a regular file, not a symbolic link)"
LARK="${LARK_CLI:-lark-cli}"
AGENTS="${LARK_AGENTS_HOME:-$HOME/.config/lark-agents}"
URL_WAIT="${URL_WAIT_SECONDS:-40}"; STEP_WAIT="${STEP_TIMEOUT_SECONDS:-60}"; SUBMIT_WAIT="${SUBMIT_WAIT_SECONDS:-300}"
for n in "$URL_WAIT" "$STEP_WAIT" "$SUBMIT_WAIT"; do [[ "$n" =~ ^[0-9]+$ ]] || usage "timeouts must be whole seconds"; done

AGENT_DIR="$AGENTS/$A"; CFG="$AGENT_DIR/config"; DAT="$AGENT_DIR/data"
# These directories hold the agent's lark-cli login state and are chmod-ed below: never follow a symbolic link to somewhere else
# (chmod and mkdir -p would change a directory that is not ours to change). A link is an environment problem: nothing was done yet.
for d in "$AGENTS" "$AGENT_DIR" "$CFG" "$DAT"; do
  [ ! -L "$d" ] || usage "$d is a symbolic link; refusing to follow it (LARK_AGENTS_HOME and the per-agent directories must be real directories)"
done
mkdir -p "$CFG" "$DAT" && chmod 700 "$AGENTS" "$AGENT_DIR" "$CFG" "$DAT" || fail "could not create the isolated directories under $AGENT_DIR"
# ...and check again what is there now: real directories, ours, private (bash cannot open a path without following links, so re-verify)
for d in "$AGENTS" "$AGENT_DIR" "$CFG" "$DAT"; do
  { [ -d "$d" ] && [ ! -L "$d" ] && [ "$(path_uid "$d")" = "$(id -u)" ] && [ "$(path_mode "$d")" = "700" ]; } || fail "$d is not a private directory of yours (a symbolic link, someone else's, or not 0700)"
done
# scratch files in the browser directory are created exclusively (noclobber = O_EXCL, which does not follow a link planted in advance)
LOG="$BR/init-$A.log"; rm -f "$LOG"; ( set -C; : > "$LOG" ) || fail "could not create $LOG"

CLI_PID=""; OK=0
cleanup() {
  # lark-cli leads its own process group (its pid is the group id, see below): stop the whole group, not just the process we
  # started, and do it even when that process has already exited, because the children it started may not have.
  if [ "$OK" != 1 ] && [ -n "$CLI_PID" ]; then
    kill -TERM -- "-$CLI_PID" 2>/dev/null
    sleep 0.2
    kill -KILL -- "-$CLI_PID" 2>/dev/null
  fi
  # page snapshots and the command channel belong to one run
  rm -f "$BR/page.png" "$BR/page.html" "$BR/page.txt" "$BR/qr.png" "$BR/cmd.txt" "$BR/cmd.txt.tmp" "$BR/result.txt"
  [ "$OK" = 1 ] && rm -f "$LOG"
  return 0
}
trap cleanup EXIT

lark() { LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_CONFIG_DIR="$CFG" LARKSUITE_CLI_DATA_DIR="$DAT" "$LARK" "$@"; }

# send_cmd <json>: hand the browser one command (written to a temp file and renamed, so it never reads half of it) and
# wait for its answer; anything but OK, or no answer in time, ends the run.
send_cmd() {
  rm -f "$BR/result.txt" "$BR/cmd.txt.tmp"
  ( set -C; printf '%s' "$1" > "$BR/cmd.txt.tmp" ) && mv -f "$BR/cmd.txt.tmp" "$BR/cmd.txt" || fail "could not write a command for the browser"
  local waited=0 answer
  while [ ! -e "$BR/result.txt" ]; do
    [ "$waited" -ge $((STEP_WAIT * 10)) ] && fail "the browser did not answer within ${STEP_WAIT}s (is feishu_browser.js still running?)"
    sleep 0.1; waited=$((waited + 1))
  done
  answer="$(head -c 300 "$BR/result.txt")"
  case "$answer" in OK*) ;; *) fail "browser step failed: $answer" ;; esac
}

# The CLI leads its own process group, so its children go with it when it is stopped. python3 makes the group (setsid(2), then
# exec: the pid stays the same, so $! is the group id) instead of the setsid command, which not every system has (macOS does not).
cd "$AGENT_DIR" || fail "could not enter $AGENT_DIR"
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_CONFIG_DIR="$CFG" LARKSUITE_CLI_DATA_DIR="$DAT" \
  "$PY" -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$LARK" config init --new --lang zh >>"$LOG" 2>&1 &
CLI_PID=$!

URL=""; waited=0
while [ -z "$URL" ]; do
  URL="$(grep -oE 'https://open\.feishu\.cn/page/cli\?user_code=[A-Z0-9-]+' "$LOG" | head -1)"
  [ -n "$URL" ] && break
  kill -0 "$CLI_PID" 2>/dev/null || fail "lark-cli exited before printing a verification URL (user_code); see $LOG"
  [ "$waited" -ge $((URL_WAIT * 10)) ] && fail "no user_code from lark-cli within ${URL_WAIT}s; see $LOG"
  sleep 0.1; waited=$((waited + 1))
done
echo "$A: $URL" >&2

GOTO="$("$PY" -c 'import json,sys; print(json.dumps({"goto": sys.argv[1] + "&lpv=1.0.85&ocv=1.0.85&from=cli", "wait": 4000}))' "$URL")" || fail "could not build the browser command"
send_cmd "$GOTO"
FILL="$("$PY" -c 'import json,sys; print(json.dumps({"fill": [["input.ud__native-input", sys.argv[1]]], "wait": 800, "click": ["button[type=\"submit\"]"]}))' "$A")" || fail "could not build the browser command"
send_cmd "$FILL"

# Creation takes a few seconds: keep refreshing the page text until it says done or failed.
started=$SECONDS
while :; do
  PAGE="$(head -c 4000 "$BR/page.txt" 2>/dev/null)"
  case "$PAGE" in
    *创建成功*) break ;;
    *创建失败*) fail "创建失败: the confirmation page reported a failure (reload the same URL and submit again)" ;;
  esac
  [ $((SECONDS - started)) -ge "$SUBMIT_WAIT" ] && fail "the app was not reported created within ${SUBMIT_WAIT}s"
  send_cmd '{"wait":1000}'
done

started=$SECONDS
while kill -0 "$CLI_PID" 2>/dev/null; do
  [ $((SECONDS - started)) -ge "$SUBMIT_WAIT" ] && fail "lark-cli did not finish within ${SUBMIT_WAIT}s"
  sleep 0.1
done
wait "$CLI_PID"; RC=$?
[ "$RC" -eq 0 ] || fail "lark-cli config init exited with status $RC; see $LOG"

APP="$(lark auth status 2>/dev/null | "$PY" -c 'import sys,json; print(json.load(sys.stdin).get("appId",""))' 2>/dev/null)" || APP=""
[[ "$APP" =~ ^cli_[0-9A-Za-z_]+$ ]] || fail "auth status gave no valid appId"
NAME="$(lark api GET "/open-apis/application/v6/applications/$APP" --params '{"lang":"zh_cn"}' --as bot 2>/dev/null \
  | "$PY" -c 'import sys,json; print(((json.load(sys.stdin).get("data") or {}).get("app") or {}).get("app_name",""))' 2>/dev/null)" || NAME=""
[ "$NAME" = "$A" ] || fail "app_name mismatch: expected '$A', got '${NAME:0:80}' (the confirmation page did not take the name; rename it in the developer console)"

OK=1
echo "$A -> app_id=$APP app_name=$NAME"

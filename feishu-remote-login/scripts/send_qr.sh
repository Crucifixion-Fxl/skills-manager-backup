#!/bin/bash
# 发一张新的飞书登录二维码：让运行中的 remote_login.js 会话刷新并截码 → 放大 → 先发一条文字说明，再发图片。
#
# 用法：
#   send_qr.sh --to <open_id> (--context <文字> | --context-file <文件>) [--label <序号说明>]
#              [--workdir <目录>] [--as bot|user] [--wait-seconds <秒>] [--dry-run]
#   send_qr.sh --help
#
# 为什么强制要文字说明：光秃秃的二维码，收的人看不出是哪个系统的登录、扫哪张、多久失效。
# 没有 --context / --context-file 就拒绝发送（退出码 2），不是靠文档提醒。
# 说明文字写「用途」：在登录哪个站点、为哪件事；不要写 token、cookie 等敏感内容。
# 收件人：必须设置环境变量 REMOTE_LOGIN_OWNER_OPEN_ID（owner 的 open_id），--to 必须等于它，否则拒绝（退出码 2），没设置也拒绝
#   （防止手滑或被诱导把登录二维码发给别人：对方扫码确认，就等于用他的身份登录）。默认拒绝，不是靠调用方自觉。
#
# 输出：最后一行是 JSON，{"status":"sent"|"logged_in"|"dry_run"|"awaiting_phone_confirmation"|"error", ...}。
# 退出码：0 成功（含已登录）；2 用法错误；3 会话没有给出新码；4 发文字失败；5 发图片失败；
#   6 页面正显示「确认登录」（已扫码、等手机确认）：此时刷新二维码会作废这次登录，所以不刷新、不发送。
# 环境变量：LARK_CLI（lark-cli 可执行文件，默认 lark-cli）、REMOTE_LOGIN_OWNER_OPEN_ID（见上）。
# 「已登录」的判据是页面 URL 已离开 accounts.feishu.cn（url.txt）；state.txt 里残留的 LOGGED_IN 不算数。
# 注意：lark-cli 的 --image 只接受相对路径，所以本脚本在 --workdir 里执行并用相对路径 qr-big.png。
set -u
umask 077
here=$(cd "$(dirname "$0")" && pwd)
LARK=${LARK_CLI:-lark-cli}
export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
emit() {  # emit key value [key value ...] -> one valid JSON line; a key written as "sent#" carries a number, every other value is a string
  python3 - "$@" <<'PY'
import json, sys
a = sys.argv[1:]
print(json.dumps({(k[:-1] if k.endswith("#") else k): (int(v) if k.endswith("#") else v) for k, v in zip(a[0::2], a[1::2])},
                 ensure_ascii=False, separators=(",", ":")))
PY
}
fail() { emit status error reason "$2"; exit "$1"; }
need() { [ $# -ge 2 ] || fail 2 "missing value for $1"; }

to=""; ctx=""; ctx_file=""; label="第1张"; workdir="."; as="bot"; wait_s=20; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --to) need "$@"; to=$2; shift 2 ;;
    --context) need "$@"; ctx=$2; shift 2 ;;
    --context-file) need "$@"; ctx_file=$2; shift 2 ;;
    --label) need "$@"; label=$2; shift 2 ;;
    --workdir) need "$@"; workdir=$2; shift 2 ;;
    --as) need "$@"; as=$2; shift 2 ;;
    --wait-seconds) need "$@"; wait_s=$2; shift 2 ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail 2 "unknown argument: $1" ;;
  esac
done

[ -n "$to" ] || fail 2 "--to <open_id> is required"
[ -n "${REMOTE_LOGIN_OWNER_OPEN_ID:-}" ] || fail 2 "REMOTE_LOGIN_OWNER_OPEN_ID is required (the owner's open_id): a login QR goes to the owner only, whoever scans and confirms it logs the session in as themselves"
[ "$to" = "$REMOTE_LOGIN_OWNER_OPEN_ID" ] || fail 2 "recipient is not the configured owner (REMOTE_LOGIN_OWNER_OPEN_ID): refusing to send a login QR to anyone else"
if [ -z "$ctx" ] && [ -n "$ctx_file" ]; then
  [ -f "$ctx_file" ] || fail 2 "context file not found"
  [ "$(stat -c %s "$ctx_file")" -le 4096 ] || fail 2 "context file too large (max 4096 bytes)"
  ctx=$(cat "$ctx_file")
fi
[ -n "$ctx" ] || fail 2 "refusing to send an image without a context text (--context / --context-file)"
case "$as" in bot|user) ;; *) fail 2 "--as must be bot or user" ;; esac
case "$wait_s" in ''|*[!0-9]*) fail 2 "--wait-seconds must be a whole number" ;; esac
[ "${#wait_s}" -le 9 ] || fail 2 "--wait-seconds is too large (at most 9 digits)"
[ -d "$workdir" ] || fail 2 "workdir not found: $workdir"
cd "$workdir" || fail 2 "cannot enter workdir"
{ [ "$(stat -c %u .)" = "$(id -u)" ] && [ "$(stat -c %a .)" = 700 ]; } || fail 2 "workdir must be a private directory (0700, owned by you): it holds page snapshots of a logged-in session"

# the text that goes out right before the image
text="【飞书登录二维码 ${label} · $(date +%H:%M:%S) 发出】
${ctx}
请用手机飞书「扫一扫」扫紧接着的这张图，扫完在手机上点「确认登录」。二维码约 25 秒失效，来不及就回我「再发」，我会补发一张新的；只扫最新一张，旧的已作废。"
if [ "$dry" = 1 ]; then  # a dry run never touches the live session: refreshing would void a QR the owner may be scanning
  emit status dry_run text "$text" image qr-big.png
  exit 0
fi

[ -f url.txt ] || fail 3 "no live remote_login.js session in this directory (no url.txt)"

page_host() {  # host of url.txt when it is a real http(s) page, else empty
  local url; url=$(cat url.txt 2>/dev/null)
  case "$url" in http://*|https://*) printf '%s' "$url" | cut -d/ -f3 | sed 's/^.*@//; s/:[0-9]*$//' ;; esac
}
host=$(page_host)
if [ -n "$host" ] && [ "$host" != accounts.feishu.cn ]; then emit status logged_in host "$host"; exit 0; fi

# never refresh while the owner is confirming on the phone: it would void the login being confirmed
if grep -q '确认登录' page.txt 2>/dev/null; then
  emit status awaiting_phone_confirmation reason "the page shows 确认登录: scanned, waiting for the phone. Not refreshing the QR."
  exit 6
fi

# 1) ask the live session for a fresh QR. Before login the session watches shoot.txt; after login (ready.txt) it takes commands.
prev=$(cat state.txt 2>/dev/null)
if [ -f ready.txt ]; then
  [ ! -e cmd.txt ] || fail 3 "the session has a pending command (cmd.txt): wait for it to finish, then retry"
  printf '%s' '{"shoot":true}' > cmd.txt.tmp && mv cmd.txt.tmp cmd.txt
else
  : > shoot.txt
fi
polls=$((10#$wait_s * 2)); i=0  # 10#: "08" is decimal, not a bad octal number
while [ "$i" -lt "$polls" ]; do
  cur=$(cat state.txt 2>/dev/null)
  [ "$cur" != "$prev" ] && break
  sleep 0.5; i=$((i + 1))
done
cur=$(cat state.txt 2>/dev/null)
host=$(page_host)
if [ -n "$host" ] && [ "$host" != accounts.feishu.cn ]; then emit status logged_in host "$host"; exit 0; fi
case "$cur" in
  QR*) [ "$cur" != "$prev" ] || fail 3 "the session produced no new QR within ${wait_s}s (not on a Feishu QR page? result: $(cat result.txt 2>/dev/null | head -c 120))" ;;
  *) fail 3 "the session produced no QR (state: ${cur:-none}; result: $(cat result.txt 2>/dev/null | head -c 120))" ;;
esac

# 2) enlarge so it scans from a chat message
python3 "$here/qr_enlarge.py" qr.png qr-big.png >/dev/null || fail 3 "could not enlarge qr.png"

# 3) context text first, then the image, back to back
ok() {  # lark-cli prints e.g. "uploading image: ..." before its JSON; parse from the first "{"
  python3 -c '
import json, sys
t = sys.stdin.read()
try:
    d = json.loads(t[t.find("{"):])
    sys.exit(0 if d.get("ok") else 1)
except Exception:
    sys.exit(1)'
}
"$LARK" im +messages-send --as "$as" --user-id "$to" --text "$text" 2>&1 | ok || fail 4 "sending the context text failed"
"$LARK" im +messages-send --as "$as" --user-id "$to" --image qr-big.png 2>&1 | ok || fail 5 "sending the image failed"
emit status sent label "$label" at "$(date +%H:%M:%S)"

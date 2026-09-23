#!/bin/bash
# 发完第一张码之后盯着会话，直到登录成功：不用让人回报「我扫了」，页面自己会说。
#
# 用法：
#   watch_login.sh --to <open_id> (--context <文字> | --context-file <文件>) [--workdir <目录>] [--as bot|user]
#                  [--max <秒，默认 150>] [--resend <秒，默认 45>] [--max-resends <次，默认 3>]
#   watch_login.sh --help
#
# 每 2 秒读 remote_login.js 写出的 url.txt / page.txt：
#   * URL 是 http(s) 页面且主机不是 accounts.feishu.cn → 登录成功，退出码 0（about:blank、错误页不算）
#   * 页面文字（整份 page.txt）含「确认登录」（已扫码，等手机确认）→ 什么都不做，重置补发计时；此时刷新二维码会作废这次登录
#   * 一直是二维码、超过 --resend 秒没动静           → 用 send_qr.sh 补发一张新码，序号递增
#   补发最多尝试 --max-resends 次；发送失败的尝试也计入次数，但不计入「已发送」数。
# 补发的码经 send_qr.sh 发出，所以同样必须设置 REMOTE_LOGIN_OWNER_OPEN_ID，--to 必须是它（启动时就检查，不等到补发那一刻才发现）。
# 注意：页面里一直有静态文字「扫码成功」，不能用它判断是否已扫；只认「确认登录」。
#
# 输出：状态变化行，最后一行是 JSON：{"status":"logged_in"|"timeout"|"session_ended"|"interrupted"|"error","sent":N,...}
# 退出码：0 登录成功；1 超时；2 用法错误；3 会话已结束（url.txt 不见了：会话退出或工作目录被删，不用干等到超时）；130 被中断（也会输出一行 interrupted）。
set -u
umask 077
here=$(cd "$(dirname "$0")" && pwd)

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
emit() {  # emit key value [key value ...] -> one valid JSON line; a key written as "sent#" carries a number, every other value is a string
  python3 - "$@" <<'PY'
import json, sys
a = sys.argv[1:]
print(json.dumps({(k[:-1] if k.endswith("#") else k): (int(v) if k.endswith("#") else v) for k, v in zip(a[0::2], a[1::2])},
                 ensure_ascii=False, separators=(",", ":")))
PY
}
fail() { emit status error reason "$1"; exit 2; }
need() { [ $# -ge 2 ] || fail "missing value for $1"; }

to=""; ctx=""; ctx_file=""; workdir="."; as="bot"; max=150; resend=45; max_resends=3
while [ $# -gt 0 ]; do
  case "$1" in
    --to) need "$@"; to=$2; shift 2 ;;
    --context) need "$@"; ctx=$2; shift 2 ;;
    --context-file) need "$@"; ctx_file=$2; shift 2 ;;
    --workdir) need "$@"; workdir=$2; shift 2 ;;
    --as) need "$@"; as=$2; shift 2 ;;
    --max) need "$@"; max=$2; shift 2 ;;
    --resend) need "$@"; resend=$2; shift 2 ;;
    --max-resends) need "$@"; max_resends=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[ -n "$to" ] || fail "--to <open_id> is required"
[ -n "${REMOTE_LOGIN_OWNER_OPEN_ID:-}" ] || fail "REMOTE_LOGIN_OWNER_OPEN_ID is required (the owner's open_id): re-sent QR codes go to the owner only"
[ "$to" = "$REMOTE_LOGIN_OWNER_OPEN_ID" ] || fail "recipient is not the configured owner (REMOTE_LOGIN_OWNER_OPEN_ID)"
if [ -z "$ctx" ] && [ -n "$ctx_file" ]; then
  [ -f "$ctx_file" ] || fail "context file not found"
  [ "$(stat -c %s "$ctx_file")" -le 4096 ] || fail "context file too large (max 4096 bytes)"
  ctx=$(cat "$ctx_file")
fi
[ -n "$ctx" ] || fail "a context text is required (--context / --context-file): re-sent QR codes carry it too"
for n in "$max" "$resend" "$max_resends"; do
  case "$n" in ''|*[!0-9]*) fail "--max/--resend/--max-resends must be whole numbers" ;; esac
  [ "${#n}" -le 9 ] || fail "--max/--resend/--max-resends are too large (at most 9 digits)"
done
max=$((10#$max)); resend=$((10#$resend)); max_resends=$((10#$max_resends))   # 10#: "08" is decimal, not a bad octal number
[ -d "$workdir" ] || fail "workdir not found: $workdir"
cd "$workdir" || fail "cannot enter workdir"
{ [ "$(stat -c %u .)" = "$(id -u)" ] && [ "$(stat -c %a .)" = 700 ]; } || fail "workdir must be a private directory (0700, owned by you): it holds page snapshots of a logged-in session"
[ -f url.txt ] || fail "no live remote_login.js session in this directory (no url.txt)"
trap 'emit status interrupted; exit 130' INT TERM

page_host() {  # host of url.txt when it is a real http(s) page, else empty
  local url; url=$(cat url.txt 2>/dev/null)
  case "$url" in http://*|https://*) printf '%s' "$url" | cut -d/ -f3 | sed 's/^.*@//; s/:[0-9]*$//' ;; esac
}
page_line() {  # the first lines of the page on one line, cut by characters (for display only)
  python3 - <<'PY'
try:
    lines = open("page.txt", encoding="utf-8", errors="replace").read().splitlines()[:8]
except OSError:
    lines = []
print("|".join(lines)[:110])
PY
}

start=$(date +%s); last_send=$start; sent=1; attempts=0; last=""
while [ $(( $(date +%s) - start )) -lt "$max" ]; do
  if [ ! -f url.txt ]; then  # the session writes url.txt every ~1.2s and removes it on exit: gone means nobody is logging in any more
    printf '%s the session has ended (url.txt is gone)\n' "$(date +%T)"
    emit status session_ended sent# "$sent"; exit 3
  fi
  host=$(page_host)
  if [ -n "$host" ] && [ "$host" != accounts.feishu.cn ]; then
    printf '%s logged in -> %s\n' "$(date +%T)" "$host"
    emit status logged_in host "$host" sent# "$sent"; exit 0
  fi
  line=$(page_line)
  if [ "$line" != "$last" ]; then printf '%s page: %s\n' "$(date +%T)" "$line"; last=$line; fi
  if grep -q '确认登录' page.txt 2>/dev/null; then last_send=$(date +%s); fi   # scanned, waiting for the phone: never refresh now
  if [ $(( $(date +%s) - last_send )) -ge "$resend" ] && [ "$attempts" -lt "$max_resends" ]; then
    attempts=$((attempts + 1)); last_send=$(date +%s)
    out=$("$here/send_qr.sh" --to "$to" --context "$ctx" --label "第$((sent + 1))张（上一张已过期）" --workdir . --as "$as" 2>&1 | tail -1)
    case "$out" in
      *'"status":"sent"'*) sent=$((sent + 1)); printf '%s no scan yet -> resend #%s: %s\n' "$(date +%T)" "$sent" "$out" ;;
      *'"status":"awaiting_phone_confirmation"'*) attempts=$((attempts - 1)); printf '%s a phone confirmation is pending: not resending\n' "$(date +%T)" ;;
      *) printf '%s resend attempt %s failed: %s\n' "$(date +%T)" "$attempts" "$out" ;;
    esac
  fi
  sleep 2
done
printf '%s timeout after %ss\n' "$(date +%T)" "$max"
emit status timeout sent# "$sent"; exit 1

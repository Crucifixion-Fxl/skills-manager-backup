# login2 会话认证

账号密码来自当前运行环境已经授权的变量。不得在命令行参数、日志、报告或错误回显中暴露密码、Token、Cookie。`SUPERSET_USER` 是规范用户名；只有它为空时才回退到 `SUPERSET_USERNAME`。若 owner prompt／运行配置声明了 `SUPERSET_EXPECTED_USER`，它是本次预期服务身份。

agent 的 Bash 工具入参只有 `command` 和 `description`，**没有 `login` 参数**，调用时没法关掉 login shell；而 Bash 工具每次起的 login／interactive shell 会读取个人 profile（`~/.bashrc` 等），可能把已被启动器清空的同名变量（个人 Superset 账号、别的个人密钥）重新注入 Agent。这一层要靠主机侧的 `~/.bashrc` 门禁堵住，见 buzz-agent-setup 的 runtime-setup.md「白名单只管启动那一刻」。skill 侧能做的是运行时比对：联网前比较有效用户名与 `SUPERSET_EXPECTED_USER`；不一致时停止并报告“运行环境身份冲突”，不能尝试另一个个人身份，也不能据此判定密码失效。

探针只依赖 `python3` 与 `curl`，**不依赖 `jq`**（agent 运行环境里通常没有 `jq`）。缺任何一个依赖时脚本会明确报错退出，不会发出空用户名／空密码的表单。

## 成功契约

登录不是“收到 302”就算成功，必须同时满足：

1. GET `/login2/?next=<目标路径>`，从表单取得 `csrf_token`，保存初始 cookie jar。
2. POST 同一个 URL 时携带相同 cookie jar、`Referer`、浏览器式 `User-Agent` 和 CSRF。
3. POST 返回的跳转是请求的目标页，不是 `302 /login/` 或另一个登录入口。
4. 带同一个 cookie jar 访问目标页得到 `HTTP 200`。
5. 带同一个 cookie jar 访问目标 API（例如 Dashboard charts）也得到 `HTTP 200` 和结构化结果。

若第 3–5 步失败，只能说明本次会话认证失败；不能把查询失败解释成指标为 0，也不能删除密码或宣布密码必须轮换。

## 安全的有限探针

下面使用权限为 0600 的临时 curl 配置文件传递表单，避免密码出现在进程参数里。探针结束会删除 curl 配置文件、响应、CSRF 和 Cookie。只输出 HTTP 状态与非敏感计数。

```bash
set -euo pipefail
SUPERSET_URL="${SUPERSET_URL:-https://superset-us.addx.live}"
SUPERSET_LOGIN_USER="${SUPERSET_EXPECTED_USER:-${SUPERSET_USER:-${SUPERSET_USERNAME:-}}}"
test -n "$SUPERSET_LOGIN_USER" && test -n "${SUPERSET_PASSWORD:-}"
if test -n "${SUPERSET_EXPECTED_USER:-}"; then
  test "$SUPERSET_LOGIN_USER" = "$SUPERSET_EXPECTED_USER" || {
    printf '%s\n' 'Superset 运行环境身份冲突' >&2
    exit 1
  }
fi

for SUPERSET_DEP in curl python3; do
  command -v "$SUPERSET_DEP" >/dev/null || { printf '%s\n' "缺少依赖：$SUPERSET_DEP" >&2; exit 1; }
done
# URL 编码从 stdin 读，值不进 argv。
superset_urlenc() {
  python3 -c 'import sys,urllib.parse;sys.stdout.write(urllib.parse.quote(sys.stdin.read(),safe=""))'
}

SUPERSET_SESSION_DIR=$(mktemp -d)
chmod 700 "$SUPERSET_SESSION_DIR"
trap 'rm -rf "$SUPERSET_SESSION_DIR"' EXIT
SUPERSET_COOKIE_JAR="$SUPERSET_SESSION_DIR/cookies"
SUPERSET_TARGET_PATH="/superset/dashboard/${DASH_ID}/"
SUPERSET_LOGIN_URL="$SUPERSET_URL/login2/?next=$(printf '%s' "$SUPERSET_TARGET_PATH" | superset_urlenc)"
SUPERSET_USER_AGENT='Mozilla/5.0 SupersetSessionClient/1.0'

curl -sS -A "$SUPERSET_USER_AGENT" -c "$SUPERSET_COOKIE_JAR" \
  "$SUPERSET_LOGIN_URL" -o "$SUPERSET_SESSION_DIR/login.html"
SUPERSET_FORM_CSRF=$(sed -n 's/.*name="csrf_token"[^>]*value="\([^"]*\)".*/\1/p' \
  "$SUPERSET_SESSION_DIR/login.html" | head -n 1)
test -n "$SUPERSET_FORM_CSRF"

SUPERSET_USER_ENCODED=$(printf '%s' "$SUPERSET_LOGIN_USER" | superset_urlenc)
SUPERSET_PASSWORD_ENCODED=$(printf '%s' "$SUPERSET_PASSWORD" | superset_urlenc)
SUPERSET_CSRF_ENCODED=$(printf '%s' "$SUPERSET_FORM_CSRF" | superset_urlenc)
# 编码结果为空就停：宁可报错，也不要把空用户名／空密码 POST 出去。
test -n "$SUPERSET_USER_ENCODED" && test -n "$SUPERSET_PASSWORD_ENCODED" && test -n "$SUPERSET_CSRF_ENCODED"

# 密码只进入权限为 0600 的临时 curl 配置文件，不进入 curl argv。
printf '%s\n' \
  'request = "POST"' \
  "url = \"$SUPERSET_LOGIN_URL\"" \
  "referer = \"$SUPERSET_LOGIN_URL\"" \
  "user-agent = \"$SUPERSET_USER_AGENT\"" \
  "cookie = \"$SUPERSET_COOKIE_JAR\"" \
  "cookie-jar = \"$SUPERSET_COOKIE_JAR\"" \
  "data = \"csrf_token=$SUPERSET_CSRF_ENCODED&username=$SUPERSET_USER_ENCODED&password=$SUPERSET_PASSWORD_ENCODED&remember=y\"" \
  'header = "Content-Type: application/x-www-form-urlencoded"' \
  'silent' 'show-error' \
  "dump-header = \"$SUPERSET_SESSION_DIR/login.headers\"" \
  "output = \"$SUPERSET_SESSION_DIR/login.body\"" \
  > "$SUPERSET_SESSION_DIR/login.conf"
chmod 600 "$SUPERSET_SESSION_DIR/login.conf"
curl --config "$SUPERSET_SESSION_DIR/login.conf"

SUPERSET_LOGIN_LOCATION=$(awk 'BEGIN{IGNORECASE=1} /^location:/ {
  gsub("\\r", ""); sub(/^[^:]+:[[:space:]]*/, ""); location=$0
} END{print location}' "$SUPERSET_SESSION_DIR/login.headers")
test "$SUPERSET_LOGIN_LOCATION" = "$SUPERSET_TARGET_PATH"

SUPERSET_TARGET_CODE=$(curl -sS -A "$SUPERSET_USER_AGENT" \
  -b "$SUPERSET_COOKIE_JAR" -o "$SUPERSET_SESSION_DIR/target.html" -w '%{http_code}' \
  "$SUPERSET_URL$SUPERSET_TARGET_PATH")
SUPERSET_API_CODE=$(curl -sS -A "$SUPERSET_USER_AGENT" \
  -b "$SUPERSET_COOKIE_JAR" -H "Referer: $SUPERSET_URL$SUPERSET_TARGET_PATH" \
  -o "$SUPERSET_SESSION_DIR/charts.json" -w '%{http_code}' \
  "$SUPERSET_URL/api/v1/dashboard/$DASH_ID/charts")
test "$SUPERSET_TARGET_CODE" = 200 && test "$SUPERSET_API_CODE" = 200
python3 -c 'import json,sys;print(json.dumps({"chart_count":len(json.load(sys.stdin)["result"])}))' \
  < "$SUPERSET_SESSION_DIR/charts.json"
```

会话验证后，GET 元数据 API 复用 `SUPERSET_COOKIE_JAR`。POST Chart data / SQL Lab 前，再用同一 cookie jar 请求 `/api/v1/security/csrf_token/`，并携带返回的 CSRF、与页面一致的 `Referer`。

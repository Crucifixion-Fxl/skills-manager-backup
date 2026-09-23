#!/usr/bin/env bash
# catalog-query.sh — 查 Backstage/RHDH Software Catalog（service-catalog-search skill 的执行体）
# 用法：
#   catalog-query.sh find-capability <api-name | 关键词 | 自然语言>
#   catalog-query.sh describe-service <service-name | 自然语言>
#   catalog-query.sh get-integration-spec <service-name | api-name>[#anchor]
#   catalog-query.sh list-services [--layer L] [--type T] [--owner O] [--system S] [--domain D]
#   catalog-query.sh list-capabilities [--domain D]
#   catalog-query.sh raw <catalog-api-path>            # 直接打 Catalog API（调试用）
#
# 配置（环境变量 / ~/.config/service-catalog-search/env / 当前目录 .env）：
#   RHDH_BASE_URL   门户地址，如 http://localhost:7008 或 https://<LAN-IP>:8443（默认 http://localhost:7007）
#   RHDH_TOKEN      Backstage backend service token（没设则自动用 guest provider 拿一个）
#   RHDH_INSECURE   仅本机/PoC 自签名 HTTPS 设为 1；默认始终校验 TLS 证书
#   GITLAB_TOKEN / GITLAB_HOST   门户离线降级直读 catalog-info.yaml 用（read_api scope；GITLAB_HOST 从环境变量读，见下方）
#   CATALOG_GROUPS  降级时扫的 GitLab group 列表（逗号分隔；默认 infra,services,iotplatform,applications）
#   CATALOG_BRANCH  降级/discovery 用的分支（默认 add-catalog-info，仓里没那分支就 main）
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 配置加载 ----
for f in "$HOME/.config/service-catalog-search/env" "$PWD/.env"; do [ -f "$f" ] && . "$f"; done
RHDH_BASE_URL="${RHDH_BASE_URL:-http://localhost:7007}"
# 网络目标说明：
#   主路径 → RHDH_BASE_URL（RHDH/Backstage Catalog API；内网门户，由调用方通过环境变量指定）
#   降级路径 → GITLAB_HOST（内部 GitLab 实例；只读 catalog-info.yaml，read_api scope；由调用方通过环境变量 GITLAB_HOST 注入，不内置默认域名）
GITLAB_HOST="${GITLAB_HOST:-}"  # 必须通过环境变量注入（内部 GitLab 实例域名；降级模式才用，不设则降级时报错）
CATALOG_GROUPS="${CATALOG_GROUPS:-infra,services,iotplatform,applications}"
CATALOG_BRANCH="${CATALOG_BRANCH:-add-catalog-info}"
CURL=(curl -s --max-time 12)
[ "${RHDH_INSECURE:-0}" = "1" ] && CURL+=(-k)

err() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
note() { printf '\033[36m·\033[0m %s\n' "$*" >&2; }

# ---- 拿 Backstage token（优先 RHDH_TOKEN，否则 guest provider）----
get_token() {
  [ -n "${RHDH_TOKEN:-}" ] && { echo "$RHDH_TOKEN"; return 0; }
  local t
  t=$("${CURL[@]}" -X POST "$RHDH_BASE_URL/api/auth/guest/refresh" -H 'x-requested-with: XMLHttpRequest' 2>/dev/null \
      | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("backstageIdentity",{}).get("token",""))' 2>/dev/null)
  [ -z "$t" ] && err "拿不到 token —— 设 RHDH_TOKEN（门户 backend.auth.externalAccess 里 type:static 的 token）；或确认门户启用了 guest provider（本机 app-config.local.yaml 里 auth.providers.guest）"
  echo "$t"
}

# ---- Catalog API 调用（带 token；错误响应不抛给 jq，转成 [] + 提示）----
TOKEN=""
cat_api() {  # cat_api <path-and-query>
  [ -z "$TOKEN" ] && TOKEN="$(get_token)"
  local out; out=$("${CURL[@]}" -H "Authorization: Bearer $TOKEN" "$RHDH_BASE_URL/api/catalog$1")
  if printf '%s' "$out" | head -c 80 | grep -q '"error"[[:space:]]*:'; then
    err "Catalog API 报错（$1）: $(printf '%s' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("error",{}).get("message",""))' 2>/dev/null)"
    echo "[]"; return 0
  fi
  printf '%s' "$out"
}

# `GET /entities` is deprecated in current Backstage. Keep its raw response
# available for debugging, but use `by-query` for all list operations. The
# helper also accepts the legacy array response during rollout.
cat_query() {  # cat_query <filter> <fields> [limit]
  [ -z "$TOKEN" ] && TOKEN="$(get_token)"
  local filter="$1" fields="$2" limit="${3:-100}" cursor="" out next
  local seen_cursors="" page_count=0 max_pages=1000 tmp status
  tmp=$(mktemp "${TMPDIR:-/tmp}/service-catalog-query.XXXXXX") || return 1

  while :; do
    page_count=$((page_count + 1))
    if [ "$page_count" -gt "$max_pages" ]; then
      err "Catalog API pagination exceeded $max_pages pages"
      rm -f "$tmp"
      return 1
    fi

    local -a args=("${CURL[@]}" --get "$RHDH_BASE_URL/api/catalog/entities/by-query"
      -H "Authorization: Bearer $TOKEN"
      --data-urlencode "fields=$fields"
      --data-urlencode "limit=$limit")
    if [ -n "$cursor" ]; then
      args+=(--data-urlencode "cursor=$cursor")
    else
      args+=(--data-urlencode "filter=$filter")
    fi
    if ! out=$("${args[@]}"); then
      err "Catalog API request failed (/entities/by-query)"
      rm -f "$tmp"
      return 1
    fi
    if printf '%s' "$out" | jq -e 'type == "object" and (.error != null)' >/dev/null 2>&1; then
      err "Catalog API error (/entities/by-query): $(printf '%s' "$out" | jq -r '.error.message // "unknown error"')"
      rm -f "$tmp"
      return 1
    fi
    if ! printf '%s' "$out" | jq -e 'type == "array" or (.items | type == "array")' >/dev/null 2>&1; then
      err "Unrecognized Catalog API response (/entities/by-query)"
      rm -f "$tmp"
      return 1
    fi
    if ! printf '%s\n' "$out" | jq -c 'if type == "array" then . else (.items // []) end' >> "$tmp"; then
      err "Failed to buffer Catalog API response"
      rm -f "$tmp"
      return 1
    fi
    next=$(printf '%s' "$out" | jq -r 'if type == "object" then (.pageInfo.nextCursor // "") else "" end')
    [ -z "$next" ] && break
    if printf '%s\n' "$seen_cursors" | grep -Fxq -- "$next"; then
      err "Catalog API returned a repeated pagination cursor"
      rm -f "$tmp"
      return 1
    fi
    seen_cursors="${seen_cursors}${seen_cursors:+$'\n'}${next}"
    cursor="$next"
  done

  jq -s 'add' "$tmp"
  status=$?
  rm -f "$tmp"
  return "$status"
}

portal_up() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$RHDH_BASE_URL/healthcheck" 2>/dev/null | grep -q '^2'; }

# ===================== 降级：直读 GitLab catalog-info.yaml =====================
gitlab_api() { curl -s --max-time 12 --header "PRIVATE-TOKEN: ${GITLAB_TOKEN:-}" "https://$GITLAB_HOST/api/v4$1"; }
degraded_load_entities() {  # 把目标 group 下各仓的 catalog-info.yaml 解析成 JSON 实体数组（stdout）
  [ -n "${GITLAB_TOKEN:-}" ] || { err "门户离线且没设 GITLAB_TOKEN —— 没法降级"; return 1; }
  note "降级模式：直读 GitLab catalog-info.yaml（分支 $CATALOG_BRANCH / fallback main，group: $CATALOG_GROUPS）—— 结果未经门户处理（无运行态、无关系解析、无 schema 校验）"
  python3 - "$GITLAB_HOST" "${GITLAB_TOKEN}" "$CATALOG_GROUPS" "$CATALOG_BRANCH" <<'PY'
import sys, json, urllib.parse, urllib.request, ssl
host, token, groups, branch = sys.argv[1], sys.argv[2], sys.argv[3].split(','), sys.argv[4]
ctx = ssl.create_default_context()
def api(path):
    req = urllib.request.Request(f"https://{host}/api/v4{path}", headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r: return r.read()
    except Exception: return None
try:
    import yaml
except Exception:
    print("[]"); sys.exit(0)
entities=[]
for g in groups:
    g=g.strip()
    page=1
    while True:
        body = api(f"/groups/{urllib.parse.quote(g, safe='')}/projects?include_subgroups=true&per_page=100&page={page}&archived=false")
        if not body: break
        projs = json.loads(body)
        if not projs: break
        for p in projs:
            pid = p["id"]
            raw = api(f"/projects/{pid}/repository/files/catalog-info.yaml/raw?ref={urllib.parse.quote(branch, safe='')}")
            if raw is None:
                raw = api(f"/projects/{pid}/repository/files/catalog-info.yaml/raw?ref=main")
            if raw is None: continue
            try:
                for doc in yaml.safe_load_all(raw):
                    if doc and isinstance(doc, dict) and doc.get("kind"):
                        doc.setdefault("_repo", p["path_with_namespace"]); entities.append(doc)
            except Exception: pass
        if len(projs) < 100: break
        page += 1
print(json.dumps(entities))
PY
}

# ===================== 命令实现 =====================
cmd="${1:-help}"; shift || true

map_keyword() {  # 自然语言/关键词 → API 名 或 tag 关键词（references/capability-map.md 的极简内置版；完整看那文件）
  local q="$*"
  case "$q" in
    *推送*|*push*|*notification*) echo "push-notification";;
    *灰度*|*feature*flag*|*feature-flag*|*实验*分流*|*ab*) echo "feature-flag-eval";;
    *权益*|*订阅*期*|*entitlement*) echo "entitlement-check";;
    *用户*画像*|*user*profile*|*profile*) echo "user-profile";;
    *设备*属性*|*设备*上*下行*|*uplink*|*downlink*|*device*event*) echo "device-uplink-downlink";;
    *物模型*|*thing*model*) echo "thing-model-register";;
    *固件*升级*|*ota*) echo "ota-rollout";;
    *录像*|*回看*|*recording*) echo "recording-playback";;
    *弹窗*|*popup*|*rating*) echo "smart-popup";;
    *paywall*|*导购*) echo "paywall-funnel";;
    *crash*|*错误*监控*) echo "crash-monitoring";;
    *) echo "$q";;   # 当作 API 名 / tag 关键词原样返回
  esac
}

case "$cmd" in
  find-capability)
    q="$*"; [ -z "$q" ] && { err "用法: catalog-query.sh find-capability <api-name|关键词|自然语言>"; exit 1; }
    key="$(map_keyword "$q")"
    if portal_up; then
      # 先按名查 API；查不到再按 tag 粗筛
      api_json=$(cat_api "/entities/by-name/api/default/$key" 2>/dev/null)
      if echo "$api_json" | jq -e '.kind=="API"' >/dev/null 2>&1; then apis="[$api_json]"
      elif ! apis=$(cat_query "kind=API,metadata.tags=$key" "kind,metadata.name,metadata.title,metadata.tags,metadata.links,spec.lifecycle,relations"); then exit 1; fi
      echo "$apis" | jq -r --arg q "$q" --arg key "$key" '
        if length==0 then "（没找到能力：\($q) → 试过 API 名/tag「\($key)」。看 references/capability-map.md 确认关键词，或 list-capabilities 浏览）"
        else (.[] | "capability(API)=\(.metadata.name)  —  \(.metadata.title // "")  [tags: \((.metadata.tags//[])|join(", "))]  lifecycle=\(.spec.lifecycle // "?")\n  接入规格: \((.metadata.links//[]|map(select(.title|test("接入规格|integrat";"i")))|.[0].url) // "（未设——describe-service 看）")\n  apiProvidedBy: \((.relations//[]|map(select(.type=="apiProvidedBy"))|map(.targetRef)|join(", ")) // "?")") end'
      # 对每个 providedBy 的 Component 再查一下约束/owner
      for ref in $(echo "$apis" | jq -r '.[].relations//[] | map(select(.type=="apiProvidedBy")) | .[].targetRef' 2>/dev/null); do
        name="${ref#component:default/}"
        c=$(cat_api "/entities/by-name/component/default/$name" 2>/dev/null)
        echo "$c" | jq -r 'select(.kind=="Component") | "  → Component: \(.metadata.name)  (System: \(.spec.system//"-"), \((.metadata.tags//[]|map(select(startswith("layer-")))|.[0]) // "layer-?"), lifecycle: \(.spec.lifecycle//"?"))\n     约束: \((.metadata.tags//[]|map(select(startswith("no-direct-")))|join(", ")) // "（无）")\n     owner: \(.spec.owner//"?")"' 2>/dev/null
      done
    else
      degraded_load_entities | jq -r --arg key "$key" '
        (map(select(.kind=="API" and ((.metadata.name==$key) or ((.metadata.tags//[])|index($key))))) ) as $apis |
        if ($apis|length)==0 then "（降级模式：没找到 API「\($key)」）"
        else ($apis[] | "capability(API)=\(.metadata.name)  [tags: \((.metadata.tags//[])|join(", "))]  lifecycle=\(.spec.lifecycle//"?")  —— 提供方需在门户在线时才能解析 apiProvidedBy；降级下看哪个 Component 的 spec.providesApis 含它") end'
    fi
    ;;

  describe-service)
    q="$*"; [ -z "$q" ] && { err "用法: catalog-query.sh describe-service <service-name>"; exit 1; }
    if portal_up; then
      c=$(cat_api "/entities/by-name/component/default/$q" 2>/dev/null)
      if echo "$c" | jq -e '.kind=="Component"' >/dev/null 2>&1; then
        echo "$c" | jq -r '"# Component: \(.metadata.name)  —  \(.metadata.title//"")\n  描述: \(.metadata.description//"-"|gsub("\n";" "))\n  type: \(.spec.type//"?")  lifecycle: \(.spec.lifecycle//"?")  层: \((.metadata.tags//[]|map(select(startswith("layer-")))|.[0])//"layer-?")  System: \(.spec.system//"-")\n  owner: \(.spec.owner//"?")\n  约束(no-direct-*): \((.metadata.tags//[]|map(select(startswith("no-direct-")))|join(", "))//"（无）")\n  providesApis(=能力): \((.spec.providesApis//[])|join(", ")//"（无）")\n  consumesApis: \((.spec.consumesApis//[])|join(", ")//"（无）")\n  dependsOn: \((.spec.dependsOn//[])|join(", ")//"（无）")\n  下游消费者: \((.relations//[]|map(select(.type=="apiConsumedBy" or .type=="dependencyOf"))|map(.targetRef)|unique|join(", "))//"（无/未解析）")\n  links: \((.metadata.links//[]|map("\(.title): \(.url)")|join("  |  "))//"-")\n  运行态关联注解: \([.metadata.annotations//{}|to_entries[]|select(.key|test("kubernetes-id|argocd|sentry|crashlytics|memfault|pagerduty|techdocs-ref"))|"\(.key)=\(.value)"]|join("  ")//"-")"'
        # 运行态摘要（按已配的插件，best-effort）
        kid=$(echo "$c" | jq -r '.metadata.annotations["backstage.io/kubernetes-id"] // empty')
        [ -n "$kid" ] && note "（K8s 工作负载 / ArgoCD 状态 / CI / Sentry 等运行态在门户 UI 的该实体页上看；脚本不逐一拉）"
      else err "目录里没有 component:default/$q —— 试 list-services 看有哪些，或确认服务名（用全局 domain-model 规范名）"; fi
    else
      degraded_load_entities | jq -r --arg q "$q" 'map(select(.kind=="Component" and .metadata.name==$q)) | if length==0 then "（降级模式：没找到 component:default/\($q)）" else (.[0] | "# Component: \(.metadata.name)（降级模式，未经门户处理）\n  type: \(.spec.type//"?")  lifecycle: \(.spec.lifecycle//"?")  层: \((.metadata.tags//[]|map(select(startswith("layer-")))|.[0])//"?")  System: \(.spec.system//"-")\n  owner: \(.spec.owner//"?")  仓: \(._repo//"?")\n  providesApis: \((.spec.providesApis//[])|join(", "))  consumesApis: \((.spec.consumesApis//[])|join(", "))  dependsOn: \((.spec.dependsOn//[])|join(", "))\n  约束: \((.metadata.tags//[]|map(select(startswith("no-direct-")))|join(", "))//"-")") end'
    fi
    ;;

  get-integration-spec)
    arg="${1:-}"; [ -z "$arg" ] && { err "用法: catalog-query.sh get-integration-spec <service-name|api-name>[#anchor]"; exit 1; }
    name="${arg%%#*}"; anchor="${arg#*#}"; [ "$anchor" = "$arg" ] && anchor=""
    if portal_up; then
      # 先当 Component；不是再当 API
      e=$(cat_api "/entities/by-name/component/default/$name" 2>/dev/null)
      echo "$e" | jq -e '.kind=="Component"' >/dev/null 2>&1 || e=$(cat_api "/entities/by-name/api/default/$name" 2>/dev/null)
      doclink=$(echo "$e" | jq -r '(.metadata.links//[]|map(select(.title|test("接入规格|integrat";"i")))|.[0].url) // empty')
      [ -n "$doclink" ] && [ -n "$anchor" ] && doclink="${doclink%%#*}#$anchor"
      echo "$e" | jq -r '"# 接入规格: \(.kind) \(.metadata.name)\n  描述: \(.metadata.description//"-"|gsub("\n";" "))\n  约束(no-direct-*): \((.metadata.tags//[]|map(select(startswith("no-direct-")))|join(", "))//"（无）")\n  机读契约(API.spec.definition): \(.spec.definition // (if (.spec.providesApis//[]|length)>0 then "见 providesApis: \((.spec.providesApis)|join(", ")) 各 API 实体的 spec.definition" else "（无）" end))"'
      if [ -n "$doclink" ]; then
        echo "  接入文档（$doclink）："
        if [[ "$doclink" == "$RHDH_BASE_URL"* ]]; then "${CURL[@]}" -H "Authorization: Bearer ${TOKEN:-$(get_token)}" "$doclink" 2>/dev/null | python3 -c 'import sys,re,html; t=sys.stdin.read(); t=re.sub(r"<script.*?</script>","",t,flags=re.S); t=re.sub(r"<[^>]+>"," ",t); print(re.sub(r"\s+\n","\n",html.unescape(t))[:6000])' 2>/dev/null
        else echo "  （文档在外部 URL，请直接打开：$doclink）"; fi
      else echo "  （该实体没设 metadata.links 的「接入规格」—— 让该服务团队补；先看 describe-service / 它的 README）"; fi
    else err "门户离线 —— 接入规格文档要门户在线（TechDocs）；降级下只能给你 catalog-info.yaml 里的 links 链接：" ; degraded_load_entities | jq -r --arg n "$name" 'map(select((.kind=="Component" or .kind=="API") and .metadata.name==$n)) | .[0].metadata.links // [] | map("  \(.title): \(.url)")[]' ; fi
    ;;

  list-services)
    layer="" typ="" owner="" system="" domain=""
    while [ $# -gt 0 ]; do case "$1" in --layer) layer="$2";shift 2;; --type) typ="$2";shift 2;; --owner) owner="$2";shift 2;; --system) system="$2";shift 2;; --domain) domain="$2";shift 2;; *) shift;; esac; done
    if portal_up; then
      f="kind=Component"; [ -n "$typ" ] && f="$f,spec.type=$typ"; [ -n "$owner" ] && f="$f,spec.owner=$owner"; [ -n "$system" ] && f="$f,spec.system=$system"
      [ -n "$layer" ] && f="$f,metadata.tags=$layer"
      entities=$(cat_query "$f" "metadata.name,metadata.title,metadata.tags,spec.type,spec.lifecycle,spec.owner,spec.system") || exit 1
      printf '%s\n' "$entities" | jq -r '(sort_by(.spec.type, .metadata.name)[]) | "\(.spec.type//"?")\t\(.metadata.name)\t\((.metadata.tags//[]|map(select(startswith("layer-")))|.[0])//"")\t\(.spec.system//"-")\t\(.spec.owner//"")\t\(.spec.lifecycle//"")"' \
        | { echo -e "TYPE\tNAME\tLAYER\tSYSTEM\tOWNER\tLIFECYCLE"; cat; } | column -t -s $'\t'
      if [ -n "$domain" ]; then note "（按 Domain 筛：门户里 Domain 页可看其下 System/Component；脚本没做 Domain→Component 的反查）"; fi
    else degraded_load_entities | jq -r --arg l "$layer" --arg t "$typ" 'map(select(.kind=="Component" and (($l=="") or ((.metadata.tags//[])|index($l))) and (($t=="") or (.spec.type==$t)))) | sort_by(.spec.type,.metadata.name)[] | "\(.spec.type//"?")  \(.metadata.name)  \((.metadata.tags//[]|map(select(startswith("layer-")))|.[0])//"")  sys=\(.spec.system//"-")  repo=\(._repo//"")"'; fi
    ;;

  list-capabilities)
    domain=""; while [ $# -gt 0 ]; do case "$1" in --domain) domain="$2";shift 2;; *) shift;; esac; done
    if portal_up; then
      entities=$(cat_query "kind=API" "metadata.name,metadata.title,metadata.tags,spec.type,spec.lifecycle,spec.system,relations") || exit 1
      printf '%s\n' "$entities" | jq -r 'sort_by(.metadata.name)[] | "\(.metadata.name)\t\(.spec.type//"?")\t\((.metadata.tags//[])|join(","))\t\(.spec.system//"-")\t\(.spec.lifecycle//"")\t\((.relations//[]|map(select(.type=="apiProvidedBy"))|map(.targetRef|sub("component:default/";""))|join(","))//"?")"' \
        | { echo -e "API(=能力)\tTYPE\tTAGS\tSYSTEM\tLIFECYCLE\tPROVIDED-BY"; cat; } | column -t -s $'\t'
    else degraded_load_entities | jq -r 'map(select(.kind=="API")) | sort_by(.metadata.name)[] | "\(.metadata.name)  type=\(.spec.type//"?")  tags=\((.metadata.tags//[])|join(","))  sys=\(.spec.system//"-")"'; fi
    ;;

  raw)
    p="${1:-/entities/by-query?limit=20}"; cat_api "$p" | jq . 2>/dev/null || cat_api "$p"
    ;;

  help|-h|--help|*)
    echo "用法: catalog-query.sh {find-capability|describe-service|get-integration-spec|list-services|list-capabilities|raw}"
    echo "  RHDH_BASE_URL=$RHDH_BASE_URL  (portal $(portal_up && echo up || echo DOWN→降级))"
    echo "  详见 ../SKILL.md"
    ;;
esac

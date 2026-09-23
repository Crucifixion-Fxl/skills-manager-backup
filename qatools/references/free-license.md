# Free License 生命周期

> 本文档是 `qatools` skill 的 Free License 执行契约。调用前必须完整读取，不能只看 curl 片段。

## 目录

- [运行依赖](#运行依赖)
- [1. 能力边界](#1-能力边界)
- [2. 环境与鉴权](#2-环境与鉴权)
- [3. Fixture 前置条件](#3-fixture-前置条件)
- [4. 公开接口](#4-公开接口)
- [5. 设备级 1002/1005](#5-设备级-10021005)
- [6. 账户级 Nature 1006](#6-账户级-nature-1006)
- [7. 错误与不确定结果](#7-错误与不确定结果)
- [8. 回归场景](#8-回归场景)

## 运行依赖

执行任何请求前先检查本文档直接使用的命令；缺少依赖时立即停止，不继续 readiness 或 mutation：

```bash
for required_command in curl jq awk mktemp chmod cp tail rm; do
  command -v "$required_command" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$required_command" >&2
    exit 1
  }
done
```

Operation 6 不提供无 `jq` 执行路径，也不会在运行过程中自动安装软件。CI/cron 调用前必须在实际执行镜像或 runner 中预装上述依赖；`engineering/skills` 的 MR 流水线只验证 Skill 文档，不执行本流程。

## 1. 能力边界

Free License 是独立于 Stripe/VIP 的受控测试生命周期：

- 设备级：`1002/1005` 的 `options/redeem/status/refresh/revoke/reset`。
- 账户级 Nature：`1006` 的 `status/grant/reset`。
- 查询真实 Free License 源记录和设备最终生效 tier，不以“接口返回成功”代替结果验证。
- qa-tools 只调同环境 IoT 原子能力，不直写业务数据库，不复制套餐优先级算法。
- `1006` 不是设备级 redeem option，禁止把它传给 `/redeem`。

该能力只操作权威数据证明为 `@qa.test + internal=1` 的测试账号和其未分享独占设备。它不能操作普通用户、共享设备或其他 writer 拥有的历史记录。

## 2. 环境与鉴权

| 环境 | Base URL |
|------|----------|
| staging | `https://qa-tools-staging.addx.live` |
| pre | `https://qa-tools-pre.addx.live` |
| prod | `https://qa-tools.addx.live` |

三个环境独立部署、独立凭据、独立数据。必须遵守：

1. 用户未明确环境时先问，不默认将 Free License mutation 扩展到 pre/prod。
2. 环境只由 Base URL 选择，请求 body 不包含 environment。
3. 不同环境的 API key/JWT 不交叉复用。`qatools_*` key 由当前实例的本地 validator 校验；Web bearer token 由当前实例在线校验。
4. staging 可复用主 Skill 现有 token 读取规则。pre/prod 必须显式设置对应 `FREE_LICENSE_URL` 和 `FREE_LICENSE_TOKEN`，不猜测凭据。
5. 即使 URL 存在，也必须先通过 readiness；`404`、`enabled=false`、环境不匹配或无效 revision 都立即停止。
6. pre/prod 缺少当前环境 token 时立即停止，不读取 password 文件中的 `staging-api-key` 或 `staging`。

```bash
FREE_LICENSE_ENVIRONMENT='staging' # 必须是用户明确选择的 staging/pre/prod
FREE_LICENSE_URL='https://qa-tools-staging.addx.live' # 按明确环境替换
FREE_LICENSE_TOKEN='<environment-specific bearer token>'
[[ "$FREE_LICENSE_ENVIRONMENT" =~ ^(staging|pre|prod)$ ]] || exit 1
[[ -n "$FREE_LICENSE_URL" && -n "$FREE_LICENSE_TOKEN" ]] || exit 1

# 每次运行使用独立、仅当前用户可读的临时目录；任何退出路径都删除原始响应。
umask 077
QA_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/qatools-free-license.XXXXXX") || exit 1
chmod 700 "$QA_TMP_DIR"
trap 'rm -rf "$QA_TMP_DIR"' EXIT HUP INT TERM
QA_BODY_FILE="$QA_TMP_DIR/response.json"
QA_HEADER_FILE="$QA_TMP_DIR/response.headers"
QA_REQUEST_FILE="$QA_TMP_DIR/request.json"
QA_AUTH_HEADER_FILE="$QA_TMP_DIR/auth.header"
printf 'Authorization: Bearer %s\n' "$FREE_LICENSE_TOKEN" > "$QA_AUTH_HEADER_FILE"
chmod 600 "$QA_AUTH_HEADER_FILE"
unset FREE_LICENSE_TOKEN

# 调用后只通过 QA_* 变量和受限文件做校验，禁止 cat/pretty-print QA_BODY_FILE。
qa_request() {
  local method="$1" path="$2" body="${3-}" trace_required="${4:-true}" curl_exit
  : > "$QA_BODY_FILE"
  : > "$QA_HEADER_FILE"
  chmod 600 "$QA_BODY_FILE" "$QA_HEADER_FILE"
  if [[ "$method" == "GET" ]]; then
    QA_HTTP_STATUS=$(curl -sS --connect-timeout 5 --max-time 30 \
      -X GET "$FREE_LICENSE_URL$path" \
      -H "@$QA_AUTH_HEADER_FILE" \
      -D "$QA_HEADER_FILE" -o "$QA_BODY_FILE" -w '%{http_code}')
    curl_exit=$?
  else
    printf '%s' "$body" > "$QA_REQUEST_FILE"
    chmod 600 "$QA_REQUEST_FILE"
    QA_HTTP_STATUS=$(curl -sS --connect-timeout 5 --max-time 30 \
      -X "$method" "$FREE_LICENSE_URL$path" \
      -H "@$QA_AUTH_HEADER_FILE" \
      -H 'Content-Type: application/json' --data-binary "@$QA_REQUEST_FILE" \
      -D "$QA_HEADER_FILE" -o "$QA_BODY_FILE" -w '%{http_code}')
    curl_exit=$?
  fi
  QA_CURL_EXIT=$curl_exit
  QA_TRACE_ID=$(awk 'tolower($0) ~ /^x-qa-trace-id:[[:space:]]*/ {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print}' "$QA_HEADER_FILE" | tail -1)
  QA_PUBLIC_CODE=$(jq -r 'if type == "object" then (.code // empty) else empty end' "$QA_BODY_FILE" 2>/dev/null)
  QA_REQUEST_ID=$(jq -r 'if type == "object" then (.requestId // empty) else empty end' "$QA_BODY_FILE" 2>/dev/null)
  if [[ "$QA_CURL_EXIT" -ne 0 || ! "$QA_HTTP_STATUS" =~ ^[0-9]{3}$ \
      || ( "$trace_required" == true && -z "$QA_TRACE_ID" ) ]] \
      || ! jq -e . "$QA_BODY_FILE" >/dev/null 2>&1; then
    printf 'Uncertain response: curlExit=%s http=%s; use status/readback, do not replay mutation.\n' \
      "$QA_CURL_EXIT" "${QA_HTTP_STATUS:-missing}" >&2
    return 70
  fi
}

qa_summary() {
  printf 'http=%s code=%s requestId=%s trace=%s\n' \
    "$QA_HTTP_STATUS" "${QA_PUBLIC_CODE:-SUCCESS}" "${QA_REQUEST_ID:-none}" "${QA_TRACE_ID:-none}"
}

# Mutation 只有可靠 200 才继续。transport/STATUS_REQUIRED/可靠快照冲突先做只读回读，
# 保存脱敏定位信息后返回非零；调用方必须停止，不能把 readback 当作 mutation 成功。
qa_mutation() {
  local path="$1" body="$2" status_path="$3" status_body="$4" request_rc=0
  local mutation_http mutation_code mutation_request_id mutation_trace
  qa_request POST "$path" "$body" || request_rc=$?
  if [[ "$request_rc" -eq 0 && "$QA_HTTP_STATUS" == 200 ]]; then
    return 0
  fi

  mutation_http="${QA_HTTP_STATUS:-missing}"
  mutation_code="${QA_PUBLIC_CODE:-missing}"
  mutation_request_id="${QA_REQUEST_ID:-missing}"
  mutation_trace="${QA_TRACE_ID:-missing}"
  qa_summary

  if [[ "$request_rc" -ne 70 && "$mutation_code" != STATUS_REQUIRED \
      && ! ( "$mutation_http" == 409 && "$mutation_code" == SNAPSHOT_CONFLICT ) ]]; then
    return 1
  fi

  qa_request POST "$status_path" "$status_body" || {
    printf 'Readback failed; ownerRef=%s mutationRequestId=%s mutationTrace=%s\n' \
      "${OWNER_RECORD_REF:-missing}" "$mutation_request_id" "$mutation_trace" >&2
    return 70
  }
  qa_summary
  [[ "$QA_HTTP_STATUS" == 200 ]] || return 70
  QA_READBACK_RECORD_ID=''
  if [[ "$status_path" == '/api/free-license/account-1006/status' ]]; then
    QA_READBACK_RECORD_ID=$(jq -r '.accountFreeLicense.recordId // empty' "$QA_BODY_FILE")
  fi
  printf 'Mutation not replayed; ownerRef=%s readbackRecordId=%s mutationHttp=%s mutationCode=%s mutationRequestId=%s mutationTrace=%s\n' \
    "${OWNER_RECORD_REF:-missing}" "${QA_READBACK_RECORD_ID:-none}" \
    "$mutation_http" "$mutation_code" "$mutation_request_id" "$mutation_trace" >&2
  [[ "$mutation_http" == 409 && "$mutation_code" == SNAPSHOT_CONFLICT ]] && return 75
  return 70
}

qa_request GET '/api/free-license/readiness' || exit $?
qa_summary
```

readiness 只在下列条件全部成立时通过：

- HTTP `200`。
- `environment` 等于用户选择的环境。
- `enabled=true`。
- `contractVersion="issue-48-minimal-v1"`。
- `iotGitSha` 是 7–40 位十六进制 revision。

readiness body 只在受限的 `QA_BODY_FILE` 中校验，不输出原文：

```bash
[[ "$QA_HTTP_STATUS" == 200 ]] && jq -e \
  --arg environment "$FREE_LICENSE_ENVIRONMENT" \
  '.environment == $environment and .enabled == true
   and .contractVersion == "issue-48-minimal-v1"
   and (.iotGitSha | test("^[0-9a-fA-F]{7,40}$"))' \
  "$QA_BODY_FILE" >/dev/null || exit 1
```

### 2.1 Mutation 部署前置条件

readiness 不包含 Pod/rollout 状态。**每个 mutation 请求发出前（包括 cleanup）**都必须通过当前环境的 ArgoCD/Kubernetes 只读 readback 验证 qa-tools workload：

- 选择的应用、集群和 namespace 与 `FREE_LICENSE_URL` 对应环境一致。
- 没有正在执行的 sync、rollout 或旧 ReplicaSet 缩容过程。
- desired/current/updated/ready/available replicas 均为 `1`，并且只有一个 Ready Pod。
- Ready Pod 使用的 image/revision 与本次记录的部署证据一致。

无法取得实时部署证据，或同一流程中 evidence/revision 已变化时，只能执行 readiness/status/options，不能执行 redeem/refresh/revoke/reset/grant；遗留状态交给环境 owner 收敛。该检查只约束现有进程内 scope lock 的成立条件，不新增分布式锁或 API。

## 3. Fixture 前置条件

执行首次 mutation 前必须完成：

- 每个回归场景使用新建账号和新绑定 Mock 设备，不复用其他运行留下的 fixture。
- 账号由 qa-tools 权威查询证明 email 以 `@qa.test` 结尾且 `internal=1`。
- `tenantId` 与账号归属一致。
- `deviceList` 等于该账号在当前环境的完整管理员设备集；status 最多 5 台，mutation 必须恰好 1 台。
- mutation 设备必须只有当前用户一条管理员关系，不能被分享。
- Mock 设备使用 `grantMockAiTier=false`。若 status 返回 `qaAiTierPresent=true`，不将该 fixture 用于套餐优先级验收。
- 先 status baseline，保存原始 Free License、VIP 和 effective tier；不覆盖、认领或删除无法证明归属的历史数据。
- `1006` 需要一台 IoT 已验证支持 `bird` 的独占设备；不在 Skill 内硬编码某个型号，以当前环境的型号配置和 bind 校验为准。

### 3.1 从零创建专用 fixture

如果用户没有已经满足上述条件的专用 fixture，复用现有注册和 Mock 绑定接口创建，不新增接口：

```bash
TENANT_ID='vicoo'
REGISTER_BODY=$(jq -nc --arg tenantId "$TENANT_ID" \
  '{tenantId:$tenantId,countryNo:"US",supportFreeLicense:true}')
qa_request POST '/api/test-user/register' "$REGISTER_BODY" false || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
[[ "$(jq -r '.result' "$QA_BODY_FILE")" == 0 ]] || exit 1
USER_ID=$(jq -er '.userId | select(type == "number" and . > 0)' "$QA_BODY_FILE") || exit 1
jq -e '.generatedEmail | test("^[a-z0-9]{4,5}@qa\\.test$")' "$QA_BODY_FILE" >/dev/null || exit 1
jq -e '.marked == true' "$QA_BODY_FILE" >/dev/null || exit 1

# MODEL_NO/DEVICE_TYPE 必须来自当前 tenant 的型号能力；1006 选择已验证支持 bird 的型号。
MODEL_NO='<verified model in current environment>'
DEVICE_TYPE='<0|1|2 matching the verified model>'
BIND_BODY=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" \
  --arg modelNo "$MODEL_NO" --argjson deviceType "$DEVICE_TYPE" \
  '{userId:$userId,tenantId:$tenantId,modelNo:$modelNo,deviceType:$deviceType,
    grantMockAiTier:false}')
qa_request POST '/api/device/bind-mock' "$BIND_BODY" false || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
[[ "$(jq -r '.result' "$QA_BODY_FILE")" == 0 ]] || exit 1
SN=$(jq -er '.data.device.serialNumber | select(type == "string" and length > 0)' "$QA_BODY_FILE") || exit 1
```

`BIND_BODY` 必须省略 `deviceFreeTierId`，不能传 `null` 或预置套餐。不同环境的旧版 bind-mock 响应不一定回显 `grantMockAiTier/deviceFreeTierId`，因此不能把响应字段当作 Oracle；绑定后必须由 Free License device status 权威证明 `qaAiTierPresent=false`。若注册、打标、绑定或后续 status 任一步失败，废弃该 fixture，不继续 mutation。

首次 status baseline 还必须满足对应场景：

- 设备级 `1002/1005`：没有历史、未来或 active 设备 Free License；没有账户级 Free Tier、Nature、兑换码或非零 device VIP 污染。VIP 要么为空，要么恰好一条已确认的 active `213/313`，且剩余有效期至少 5 分钟。
- 账户级 `1006`：`accountFreeLicense=null`、没有设备级 Free License，且设备当前没有有效套餐。`1006 -> 213/313` 场景必须先 grant 并证明 1006，再由现有 VIP 能力创建 213/313，不能反过来。
- 任一谓词不满足都废弃 fixture，不通过 reset 历史数据来“修干净”。

### 3.2 Mutation owner 预登记

设备级 mutation 或 account-1006 grant 发出前，必须由操作者在现有受限 owner channel 预登记 `environment/userId/tenantId/SN/action/tier`；设备级记录再补 `autoRedeem`。必须先取得可长期检索的引用：

```bash
OWNER_RECORD_REF='<existing restricted owner record reference>'
[[ -n "$OWNER_RECORD_REF" && "$OWNER_RECORD_REF" != *'<'* ]] || exit 1
```

首次可靠创建返回 `idempotent=false` 后，必须先把精确 `recordId` 补到同一个 owner item，再执行后续 mutation 或 cleanup。若创建返回 `STATUS_REQUIRED`，wrapper 会输出已预登记的 owner ref、mutation requestId/trace 及 account status 回读到的最小 `readbackRecordId`；操作者必须将这些脱敏定位信息补入同一 item 后再交给环境 owner。该记录不保存 bearer、完整 body 或 `snapshotToken`。account-1006 的 reset 授权仍以 IoT 创建的 Redis marker 为准，owner item 只是恢复索引，不新增归属数据源。

## 4. 公开接口

| 接口 | 用途 | 写入 |
|------|------|------|
| `GET /api/free-license/readiness` | 校验同环境能力和 IoT revision | 否 |
| `POST /api/free-license/options` | 查询 IoT 当前可用设备级 option，只允许 `1002/1005` | 否 |
| `POST /api/free-license/status` | 读取设备级源记录、VIP 和 effective tier | 否 |
| `POST /api/free-license/redeem` | 精确创建或幂等复用一条 `1002/1005` | 是 |
| `POST /api/free-license/refresh` | 在已知源记录与 tier 下验证投影 | 可能 |
| `POST /api/free-license/revoke` | 使一条精确记录过期并刷新 | 是 |
| `POST /api/free-license/reset` | 删除一条精确记录并刷新 | 是 |
| `POST /api/free-license/account-1006/status` | 读取账户级 Nature 1006 及设备投影 | 否 |
| `POST /api/free-license/account-1006/grant` | 为已验证 Bird fixture 创建真实账户级 1006 | 是 |
| `POST /api/free-license/account-1006/reset` | 删除本次拥有的精确 1006 及精确投影 | 是 |

所有 POST 请求 `additionalProperties=false`：不能增加未定义字段。`deviceList`、`recordId`、`snapshotToken` 必须使用当前响应的原值，禁止猜测或从另一环境复制。

## 5. 设备级 `1002/1005`

### 5.1 Baseline 与 options

```bash
USER_ID=12345
TENANT_ID='vicoo'
SN='MOCK_EXAMPLE'

SCOPE=$(jq -nc --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn]}')

qa_request POST '/api/free-license/status' "$SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
DEVICE_BASELINE_FILE="$QA_TMP_DIR/device-baseline.json"
cp "$QA_BODY_FILE" "$DEVICE_BASELINE_FILE"
chmod 600 "$DEVICE_BASELINE_FILE"
jq -e '.qaAiTierPresent == false' "$DEVICE_BASELINE_FILE" >/dev/null || exit 1

qa_request POST '/api/free-license/options' "$SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
jq -e '.options | length > 0 and all(.[]; (.tierId == 1002 or .tierId == 1005) and .tierType == 5)' \
  "$QA_BODY_FILE" >/dev/null || exit 1
```

options 中的每一项必须是 `{tierId:1002|1005,tierType:5}`。空 options 是当前环境数据不可用，不能用 skill 内置值绕过。

### 5.2 Redeem 与结果 Oracle

`expectedTierId` 是回归场景的最终期望，不一定等于 `freeLicenseId`。例如 `313 + 1005` 的期望仍可能是 `313`。

```bash
FREE_LICENSE_ID=1005
EXPECTED_TIER_ID=1005

# 专用新 fixture 的 baseline 必须没有任何设备级 Free License。
jq -e '.devices | all(.[]; (.freeLicenses | length) == 0)' \
  "$DEVICE_BASELINE_FILE" >/dev/null || exit 1

REDEEM_BODY=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --argjson freeLicenseId "$FREE_LICENSE_ID" --argjson expectedTierId "$EXPECTED_TIER_ID" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],freeLicenseId:$freeLicenseId,
    autoRedeem:false,expectedTierId:$expectedTierId}')

DEVICE_EXPECTED_STATUS=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --argjson expectedTierId "$EXPECTED_TIER_ID" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],expectedTierId:$expectedTierId}')

qa_mutation '/api/free-license/redeem' "$REDEEM_BODY" \
  '/api/free-license/status' "$DEVICE_EXPECTED_STATUS" || exit $?
qa_summary

# 首次调用只有 idempotent=false 才证明本流程新建记录；
# 本流程后续的 idempotent=true 必须保留并校验已有 owned ID。
REDEEM_IDEMPOTENT=$(jq -r '.idempotent' "$QA_BODY_FILE") || exit 1
[[ "$REDEEM_IDEMPOTENT" == true || "$REDEEM_IDEMPOTENT" == false ]] || exit 1
REDEEM_RECORD_ID=$(jq -er \
  --arg sn "$SN" --argjson tierId "$FREE_LICENSE_ID" \
  '[.status.devices[] | select(.serialNumber == $sn) | .freeLicenses[]
    | select(.active == true and .tierId == $tierId and .autoRedeem == false) | .recordId]
   | select(length == 1) | .[0]' "$QA_BODY_FILE") || exit 1
if [[ "$REDEEM_IDEMPOTENT" == false ]]; then
  [[ -z "${OWNED_DEVICE_RECORD_ID:-}" ]] || exit 1
  OWNED_DEVICE_RECORD_ID="$REDEEM_RECORD_ID"
elif [[ -n "${OWNED_DEVICE_RECORD_ID:-}" ]]; then
  [[ "$REDEEM_RECORD_ID" == "$OWNED_DEVICE_RECORD_ID" ]] || exit 1
else
  OWNED_DEVICE_RECORD_ID=''
fi
```

若 `OWNED_DEVICE_RECORD_ID` 非空，先把它补入 `OWNER_RECORD_REF` 指向的既有 owner item；完成后由操作者显式设置 `OWNER_TARGET_RECORDED=true`。未完成登记时不能继续任何设备级 mutation。

HTTP 200 仍需检查：

- `action="REDEEM"`。首次返回 `idempotent=true` 代表记录在本次调用前已存在，只能查询/refresh，不能认领后 cleanup。
- 只有同一运行已经保存 `OWNED_DEVICE_RECORD_ID` 后，才可重复同一请求验证幂等；重复响应的 `recordId` 必须仍等于该值。
- `status.environment` 与目标环境一致。
- `status.devices` 的 serial-number 集与请求完全相等。
- 目标设备恰好有一条匹配 tier 与 `autoRedeem` 的 active Free License。
- `effectiveTierId=expectedTierId` 且 `matchesExpected=true`。
- `qaAiTierPresent=false`，`unexpectedFacts` 没有未在 baseline 中的污染。

### 5.3 Refresh

refresh 只用于已有恰好一条 active `1002/1005` 源，且 status 已证明当前 effective tier 符合期望的 fixture。它不用于修复来源不明、缺失或多条记录。

```bash
[[ "${OWNER_TARGET_RECORDED:-false}" == true ]] || exit 1
REFRESH_BODY=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --argjson expectedTierId "$EXPECTED_TIER_ID" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],expectedTierId:$expectedTierId}')

qa_mutation '/api/free-license/refresh' "$REFRESH_BODY" \
  '/api/free-license/status' "$REFRESH_BODY" || exit $?
qa_summary
```

### 5.4 精确 cleanup

cleanup 前必须同时满足：baseline 没有目标 source、首次 redeem 明确返回 `idempotent=false`、精确 recordId 已补入 owner item、当前唯一目标的 `recordId` 等于 `OWNED_DEVICE_RECORD_ID`。`revoke` 与 `reset` 是互斥终态：要保留过期记录用于验收就 revoke；要删除记录就在它仍 active 时直接 reset，不能 revoke 后再 reset。`snapshotToken` 从 cleanup 前最新 status 取得；若任一条件不成立，停止自动 cleanup。

```bash
[[ -n "$OWNED_DEVICE_RECORD_ID" ]] || exit 1
[[ "${OWNER_TARGET_RECORDED:-false}" == true ]] || exit 1
qa_request POST '/api/free-license/status' "$SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
RECORD_ID=$(jq -er \
  --arg sn "$SN" --arg recordId "$OWNED_DEVICE_RECORD_ID" --argjson tierId "$FREE_LICENSE_ID" \
  '[.devices[] | select(.serialNumber == $sn) | .freeLicenses[]
    | select(.tierId == $tierId and .recordId == $recordId)]
   | select(length == 1) | .[0].recordId' "$QA_BODY_FILE") || exit 1
SNAPSHOT_TOKEN=$(jq -er \
  --arg sn "$SN" --arg recordId "$OWNED_DEVICE_RECORD_ID" \
  '[.devices[] | select(.serialNumber == $sn) | .freeLicenses[] | select(.recordId == $recordId)]
   | select(length == 1) | .[0].snapshotToken' "$QA_BODY_FILE") || exit 1
EXPECTED_AFTER_JSON='null' # 或已确认的付费 tier，例如 213/313

RESET_BODY=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --arg recordId "$RECORD_ID" --argjson freeLicenseId "$FREE_LICENSE_ID" \
  --arg snapshotToken "$SNAPSHOT_TOKEN" --argjson expectedTierId "$EXPECTED_AFTER_JSON" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],serialNumber:$sn,
    recordId:$recordId,freeLicenseId:$freeLicenseId,snapshotToken:$snapshotToken,
    expectedTierId:$expectedTierId}')

RESET_STATUS_BODY=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --argjson expectedTierId "$EXPECTED_AFTER_JSON" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],expectedTierId:$expectedTierId}')

qa_mutation '/api/free-license/reset' "$RESET_BODY" \
  '/api/free-license/status' "$RESET_STATUS_BODY" || exit $?
qa_summary
```

reset 成功后再调 status，确认目标源已消失、其他记录未变、effective tier 符合 `expectedTierId`。revoke 仅用于“过期”验收，请求形状与 reset 相同，但 endpoint 改为 `/api/free-license/revoke`；revoke 成功即结束该 fixture 的 mutation，不再尝试 reset。

## 6. 账户级 Nature `1006`

1006 使用独立路径，不读设备级 options，不传 `freeLicenseId`。

```bash
ACCOUNT_SCOPE=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn]}')

qa_request POST '/api/free-license/account-1006/status' "$ACCOUNT_SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
ACCOUNT_BASELINE_FILE="$QA_TMP_DIR/account-baseline.json"
cp "$QA_BODY_FILE" "$ACCOUNT_BASELINE_FILE"
chmod 600 "$ACCOUNT_BASELINE_FILE"
jq -e '.accountFreeLicense == null' "$ACCOUNT_BASELINE_FILE" >/dev/null || exit 1

# account status 不包含设备级 source；再用设备 status 证明该设备没有 Free License。
qa_request POST '/api/free-license/status' "$ACCOUNT_SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
jq -e '.qaAiTierPresent == false
       and (.devices | all(.[]; (.freeLicenses | length) == 0 and .effectiveTierId == null))' \
  "$QA_BODY_FILE" >/dev/null || exit 1

ACCOUNT_GRANT=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --argjson expectedTierId 1006 \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],expectedTierId:$expectedTierId}')

[[ -n "${OWNER_RECORD_REF:-}" ]] || exit 1
qa_mutation '/api/free-license/account-1006/grant' "$ACCOUNT_GRANT" \
  '/api/free-license/account-1006/status' "$ACCOUNT_GRANT" || exit $?
qa_summary
ACCOUNT_GRANT_IDEMPOTENT=$(jq -r '.idempotent' "$QA_BODY_FILE") || exit 1
[[ "$ACCOUNT_GRANT_IDEMPOTENT" == true || "$ACCOUNT_GRANT_IDEMPOTENT" == false ]] || exit 1
ACCOUNT_GRANT_RECORD_ID=$(jq -er '.status.accountFreeLicense.recordId' "$QA_BODY_FILE") || exit 1
if [[ "$ACCOUNT_GRANT_IDEMPOTENT" == false ]]; then
  [[ -z "${OWNED_ACCOUNT_RECORD_ID:-}" ]] || exit 1
  OWNED_ACCOUNT_RECORD_ID="$ACCOUNT_GRANT_RECORD_ID"
elif [[ -n "${OWNED_ACCOUNT_RECORD_ID:-}" ]]; then
  [[ "$ACCOUNT_GRANT_RECORD_ID" == "$OWNED_ACCOUNT_RECORD_ID" ]] || exit 1
else
  OWNED_ACCOUNT_RECORD_ID=''
fi
```

grant 的 `expectedTierId` 只能是 `1006/213/313`。成功 Oracle：

- `action="GRANT"`。首次返回 `idempotent=true` 代表账户 1006 已存在，不属于本流程，禁止自动 reset。
- 只有本流程已保存 `OWNED_ACCOUNT_RECORD_ID` 后，才可重复同一请求验证幂等，且响应 `recordId` 必须相同。
- `status.accountFreeLicense.tierType=1006`、`supportAiEvent="bird"`、`active=true`。
- 设备 `effectiveTierId` 等于已确认期望，`matchesExpected=true`。
- `recordId` 必须等于首次非幂等 grant 保存的 `OWNED_ACCOUNT_RECORD_ID`；`snapshotToken` 必须来自 reset 前最新 status。不采纳历史 Nature 记录。

若 `OWNED_ACCOUNT_RECORD_ID` 非空，必须先把它补入 `OWNER_RECORD_REF` 指向的同一 owner item，然后显式设置 `ACCOUNT_OWNER_TARGET_RECORDED=true`。未完成登记时不得继续 account mutation 或 reset。

`1006 -> 213/313` 的顺序固定为：先按上述 clean baseline grant 并证明 `effectiveTierId=1006`；再由现有具名 VIP owner 创建 `213/313`；随后用带 `expectedTierId=213|313` 的 account status 证明 paid tier 已生效；最后 reset 本次 1006，并将同一个 paid tier 作为 `EXPECTED_AFTER_JSON`。不能先创建 paid tier 再运行上面的 `expectedTierId=1006` grant 示例。

```bash
[[ -n "$OWNED_ACCOUNT_RECORD_ID" ]] || exit 1
[[ "${ACCOUNT_OWNER_TARGET_RECORDED:-false}" == true ]] || exit 1
qa_request POST '/api/free-license/account-1006/status' "$ACCOUNT_SCOPE" || exit $?
qa_summary
[[ "$QA_HTTP_STATUS" == 200 ]] || exit 1
ACCOUNT_RECORD_ID=$(jq -er \
  --arg recordId "$OWNED_ACCOUNT_RECORD_ID" \
  '.accountFreeLicense | select(.recordId == $recordId and .active == true) | .recordId' \
  "$QA_BODY_FILE") || exit 1
ACCOUNT_SNAPSHOT_TOKEN=$(jq -er \
  --arg recordId "$OWNED_ACCOUNT_RECORD_ID" \
  '.accountFreeLicense | select(.recordId == $recordId and .active == true) | .snapshotToken' \
  "$QA_BODY_FILE") || exit 1
EXPECTED_AFTER_JSON='null' # 或 213/313；reset 后不能期望 1006

ACCOUNT_RESET=$(jq -nc \
  --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
  --arg recordId "$ACCOUNT_RECORD_ID" --arg snapshotToken "$ACCOUNT_SNAPSHOT_TOKEN" \
  --argjson expectedTierId "$EXPECTED_AFTER_JSON" \
  '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],serialNumber:$sn,
    recordId:$recordId,snapshotToken:$snapshotToken,expectedTierId:$expectedTierId}')

if [[ "$EXPECTED_AFTER_JSON" == null ]]; then
  ACCOUNT_RESET_STATUS="$ACCOUNT_SCOPE" # account status 的可选 expectedTierId 不接受显式 null
else
  ACCOUNT_RESET_STATUS=$(jq -nc \
    --argjson userId "$USER_ID" --arg tenantId "$TENANT_ID" --arg sn "$SN" \
    --argjson expectedTierId "$EXPECTED_AFTER_JSON" \
    '{userId:$userId,tenantId:$tenantId,deviceList:[$sn],expectedTierId:$expectedTierId}')
fi

qa_mutation '/api/free-license/account-1006/reset' "$ACCOUNT_RESET" \
  '/api/free-license/account-1006/status' "$ACCOUNT_RESET_STATUS" || exit $?
qa_summary
```

reset 后再调 account status，确认 `accountFreeLicense=null`，设备 effective tier 符合期望。不删除付费 `213/313`、null-tier marker 或其他 writer 数据。

## 7. 错误与不确定结果

| HTTP / code | 处理 |
|-------------|------|
| `400 INVALID_REQUEST` | 修正请求形状；不能增加未定义字段。 |
| `401 AUTHENTICATION_FAILED` | 更换当前环境的过期/失效凭据，不跨环境降级。 |
| `403 CALLER_NOT_ALLOWED` / target-scope code | 账号、tenant、设备集或归属不符合契约；零 mutation 停止。 |
| `409 SNAPSHOT_CONFLICT` | 只读 status，或 account-1006 reset 返回带匹配 trace/requestId 的严格快照冲突时可直接采用；重新 status，不使用旧 `recordId/snapshotToken`。其他已发出的 mutation 冲突按 `STATUS_REQUIRED` 处理。 |
| `409 UNEXPECTED_ENTITLEMENT` | 只读 status 表示存在额外/冲突数据，应废弃该 fixture；已发出的 mutation 不把该响应当成终态，按 `STATUS_REQUIRED` 处理。两种情况都不能广泛清理。 |
| `503 DOWNSTREAM_ERROR` | 下游不可用或读取响应不可信；保留 `requestId/X-QA-Trace-Id` 排查。 |
| `503 STATUS_REQUIRED` | mutation 已发出但结果不可确认；禁止盲目重试，先 status 和授权 readback。 |
| `FEATURE_DISABLED` / readiness disabled / `404` | 当前实例不可用；不改 URL 绕过。 |

每次请求都保留 HTTP status、响应 `requestId` 和 `X-QA-Trace-Id`，但不记录 bearer token、完整请求 body 或 `snapshotToken`。

原始 request/response body/header 只保存在本次运行的 `QA_TMP_DIR`（权限 `0700`，文件权限 `0600`），由 `trap` 在正常、异常或中断退出时清理。mutation body 必须经 `QA_REQUEST_FILE` 传给 curl，不能把含 `snapshotToken` 的 JSON 放进命令行参数。只通过 `jq` 提取当前流程需要的最小字段，禁止输出响应原文或把临时文件加入日志、仓库和长期验收产物。

`qa_request` 返回 `70` 表示 transport 非零、HTTP 状态/trace 缺失或 JSON 无法解析。所有 mutation 必须经 `qa_mutation` 调用，不能直接 `qa_request ... || exit`；wrapper 会保存脱敏 mutation 定位信息、执行对应只读 status/readback，然后返回非零并停止流程。HTTP 4xx/5xx 即使 curl exit 为 0，也必须按 `QA_HTTP_STATUS + QA_PUBLIC_CODE` 分流，不能只看 curl 是否成功。

`STATUS_REQUIRED` 只能在已有 `OWNER_RECORD_REF` 且 readback 能唯一证明当前记录属于本次调用时，由对应环境 owner 决定后续精确 cleanup。account grant 的 readback 若发现 source，必须把 wrapper 输出的 `readbackRecordId + mutation requestId/trace` 补入同一 owner item；当前 wrapper 本身始终停止，不自动 unlock、不广泛 reset、不重放原 mutation。

## 8. 回归场景

Skill 不内置优先级算法；以用户指定的已确认场景决定 `expectedTierId`。常用套件：

- `1002 -> 213 -> cancel -> 1002`。
- `1002 -> 313 -> cancel -> 1002`。
- `213 + 1005 -> 1005`。
- `313 + 1005 -> 313`。
- active `1002/1005` 执行 refresh 后保持期望 tier。
- Free License reset/revoke 后回落到当前已确认的付费 tier，或无套餐时回落为 null。
- `1006 -> 213/313`，然后 account reset 保留当前付费 tier。
- 重复 redeem/grant 返回幂等结果，不产生第二条源记录。
- 非测试账号、tenant 不匹配、设备不归属、共享设备、不完整 device set 和旧 snapshot 全部 fail closed，且零下游 mutation。

验收记录必须标明环境、qa-tools/IoT 部署 revision、fixture、请求 trace、baseline、mutation 结果、最终 Oracle 和 cleanup 后回读。staging 通过不代替 pre/prod，pre 通过也不代替 prod。

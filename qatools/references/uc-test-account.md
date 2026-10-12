# user-center test accounts (Golf / neopace)

Read this reference for `uc-test-account`. Authentication and environment rules
are in [`authentication-and-environments.md`](authentication-and-environments.md).

## Contents

- [Scope](#scope)
- [Operation: uc-test-account](#operation-uc-test-account)
- [Hard rules](#hard-rules)
- [Output contract](#output-contract)

## Scope

Use this operation for the new user-center (UC) account path: UC gateway login,
account self-service, and the Golf app. Design reference: user-center#52
(ADR-0007).

`tenantId=neopace` is the Golf app (Bundle ID `com.neopace.golf`). It is the
only tenant currently allowlisted on staging/prod UC. Default to it; do not ask
the user to choose a tenant for this operation.

Do not use this operation for the legacy iot-service account/VIP/device path
(Operations 1–3). The two `userId` spaces are not interchangeable: a UC account
cannot receive iot-service VIP, and an iot-service account cannot be marked or
logged into through UC.

## Operation: uc-test-account

All four actions require the same bearer token as other operations.

| Action | Endpoint | Notes |
|---|---|---|
| Register | `POST /api/uc-test-account/register` | Body `{tenantId(default neopace), localPart?(empty = random), nickname?}`. Full email is `<localPart>@qa.test`. Returns `{userId, tenantId}`. |
| List | `GET /api/uc-test-account/list?tenantId=&limit=&offset=` | Returns `items` with the email masked. |
| Mark | `POST /api/uc-test-account/mark` | Body `{userId, tenantId, internal}`. Sets or clears the internal-test-account flag. |
| Mint token | `POST /api/uc-test-account/token` | Body `{userId, tenantId, deviceId?, deviceName?}`. Returns a real Zitadel JWT: `accessToken`/`refreshToken`/`trackerToken`/`expiresIn` (~12h). |

Full flow — register, mark internal, then mint a login token:

```bash
# 1) Register (omit tenantId to default to neopace = Golf)
curl -sS -X POST "$QA_TOOLS_URL/api/uc-test-account/register" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" -H "Content-Type: application/json" \
  -d '{"localPart":"golf-smoke-'"$RANDOM"'"}' \
  | python3 -m json.tool
# -> {"userId":1500000243,"tenantId":"neopace"}

# 2) Mark internal (required before minting a token; also what PE/collect-gateway
#    read to exclude this account from AB/analytics)
curl -sS -X POST "$QA_TOOLS_URL/api/uc-test-account/mark" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" -H "Content-Type: application/json" \
  -d '{"userId":1500000243,"tenantId":"neopace","internal":true}' \
  | python3 -m json.tool

# 3) Mint token (real login state for the Golf app or automation)
curl -sS -X POST "$QA_TOOLS_URL/api/uc-test-account/token" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" -H "Content-Type: application/json" \
  -d '{"userId":1500000243,"tenantId":"neopace","deviceId":"qa-run-1"}' \
  | python3 -m json.tool
# -> {"accessToken":"...","refreshToken":"...","trackerToken":"...","expiresIn":43200}
```

Use `accessToken` as `Authorization: Bearer <accessToken>` against user-center's
own `/uc/*` endpoints through the gateway (e.g. `GET /uc/profile`, with header
`X-Tenant-ID: neopace`). This is equivalent to a real Golf app login session and
has been e2e-verified on staging.

## Hard rules

- Never log, persist, or echo back `accessToken`/`refreshToken`/`trackerToken`.
  Tell the user it is shown once.
- Minting a token requires `internal=true` on the target account. A `403`
  response means the account is either not marked or not an `@qa.test` address;
  run the mark step first, then retry.
- `404` means the `userId` does not exist for that `tenantId`. `401` means the
  skill's own qa-tools bearer token failed, not a UC-side error.
- The list endpoint only returns masked email; the full address is known only to
  whoever registered it.
- Tenants beyond `neopace` are rejected with `403 scope denied` — the allowlist
  lives in user-center, not in qa-tools. Do not try other tenant strings.
- user-center's `status` enum is capitalized (`Active`/`Cancelled`/`Disabled`/
  `Deleting`). Do not match it lowercase when reading it back through
  `GetProfile` or any UC query.

## Output contract

Report:

- `userId` and `tenantId` (state `neopace` explicitly when it is the Golf app);
- the generated `@qa.test` email (full, not masked, since the caller owns it);
- the mark result (`internal: true/false`);
- confirmation that a token was minted, without printing its value;
- any UC-side readback used to verify the account (e.g. `/uc/profile` call),
  not just the mint response.

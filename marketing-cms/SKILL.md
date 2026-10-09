---
name: marketing-cms
description: "Use when managing Paywall pages, Promotion assets (popup/banner/video), member features, comparison templates, translation jobs, or cross-environment sync in Marketing CMS (Payload CMS). Triggers: 'paywall', 'promotion', 'CMS content', 'member feature', 'translation progress', 'sync to pre', 'transfer', 'marketing page', 'OEM tenant config', 'upload image', 'push to prod'."
dependencies:
  - feishu-auth
---

# marketing-cms

Marketing Content Center -- Payload CMS platform for App paywall, promotion, and subscription content.

## Dependencies

Requires the [feishu-auth](../../development/feishu-auth/SKILL.md) skill for authentication. Read its registry, exact-version and credential-handling contract before running any authentication command. Use the host's approved authentication entry when one is configured; never print or persist token stdout, expose registry credentials, or silently fall back to a different CLI version.

`promotion-i18n` is an optional, separately distributed dependency only for creating translated images. Check that it is installed before delegating that workflow. If unavailable, report the missing capability and request approved localized assets; do not claim that image translation is available or publish untranslated artwork as localized content. Paywall management and existing-asset operations do not require it.

## First-touch Decision Tree

On receiving a user request, determine the target:

```
User request → Identify target
├── Paywall page (carousel + product card + pricing) → paywalls collection
├── Touchpoint asset (popup/banner/video image card) → promotions collection
├── Image upload → media collection
├── Member benefit item → member-features collection
├── Plan comparison table → comparison-templates collection
├── Translation progress → translation-jobs collection
├── Push/sync to pre or prod → Transfer API (see "Cross-environment Sync")
└── Unclear → Ask which content object and operation the user intends
```

## Description

| slug | Purpose |
|------|---------|
| `paywalls` | Paywall page config (component-based), multi-OEM tenant, 23-language i18n |
| `promotions` | Touchpoint assets (popup/banner/video status bar), imageCard-based |
| `member-features` | Member benefit items (key/name/subtitle) referenced by paywalls |
| `comparison-templates` | Plan comparison tables |
| `media` | Image upload and management |
| `sync-logs` | Environment sync logs |
| `translation-jobs` | AI translation task status |

## Rules

### Environment

| Environment | Base URL | When to use |
|-------------|---------|-------------|
| **Staging US** | `https://marketing-cms-staging-us.addx.live` | **Default for all read/write operations** |
| Pre US | `https://marketing-cms-pre-us.addx.live` | QA verification + Transfer to Prod |
| Prod US | No public URL (K8s internal only, not accessible from local machine) | Users never call Prod API directly; Pre→Prod transfer is handled server-side by Pre |

API path: `/api/{slug}` (standard Payload CMS REST API).

### Authentication

Via the [feishu-auth](../../development/feishu-auth/SKILL.md) contract above (including any host-approved wrapper). The commands below require its registry and exact-version checks to have passed:

```bash
# Disable shell tracing before obtaining any credential.
set +x
export -n TOKEN
TOKEN=$(npx --yes @a4x/feishu-auth-cli@1.2.1 token)
if [ -z "$TOKEN" ]; then
  npx --yes @a4x/feishu-auth-cli@1.2.1 login
  TOKEN=$(npx --yes @a4x/feishu-auth-cli@1.2.1 token)
fi

```

Use the host-approved wrapper instead of the direct CLI commands above when configured. Never enable `-v`/`--verbose`, `--trace*`, shell tracing or credential logging. Keep `TOKEN` unexported, unset it after the operation, and do not put it in argv or a header file. All examples below use this Bash helper (curl with `--fail-with-body` support):

```bash
cms_curl() {
  set +x
  local cms_request_path="${1:-}"
  case "${BASE_URL:-}" in
    https://marketing-cms-staging-us.addx.live|https://marketing-cms-pre-us.addx.live) ;;
    *) builtin printf 'Unsupported CMS environment\n' >&2; return 64 ;;
  esac
  case "$cms_request_path" in
    /api/*) shift ;;
    *) builtin printf 'Expected a CMS /api/ path\n' >&2; return 64 ;;
  esac
  if [ -z "${TOKEN:-}" ]; then
    builtin printf 'CMS token is required\n' >&2
    return 64
  fi
  local -a cms_request_options=()
  local cms_request_value cms_upload_path cms_upload_seen=0
  while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || return 64
    cms_request_value="$2"
    case "$1" in
      -X)
        case "$cms_request_value" in GET|POST|PATCH|DELETE) ;; *) return 64 ;; esac
        cms_request_options+=(-X "$cms_request_value") ;;
      -H)
        [ "$cms_request_value" = 'Content-Type: application/json' ] || return 64
        cms_request_options+=(-H "$cms_request_value") ;;
      -d)
        case "$cms_request_value" in \{*\}) ;; *) return 64 ;; esac
        cms_request_options+=(--data-raw "$cms_request_value") ;;
      -F)
        case "$cms_request_value" in
          file=@*)
            [ "$cms_upload_seen" -eq 0 ] || return 64
            cms_upload_seen=1
            cms_upload_path="${cms_request_value#file=@}"
            case "$cms_upload_path" in
              ''|-|*';'*|*','*|*$'\n'*|*$'\r'*|*'://'*) return 64 ;;
            esac
            cms_request_options+=(-F "$cms_request_value") ;;
          _payload=\{*\}) cms_request_options+=(--form-string "$cms_request_value") ;;
          *) return 64 ;;
        esac ;;
      *) return 64 ;;
    esac
    shift 2
  done
  export -n TOKEN
  builtin printf 'Authorization: Bearer %s\n' "$TOKEN" |
    command curl --disable --globoff --fail-with-body --silent --show-error \
      --header @- "${BASE_URL}${cms_request_path}" "${cms_request_options[@]}"
}

BASE_URL="https://marketing-cms-staging-us.addx.live"
cms_curl '/api/paywalls?limit=5'
```

The helper reserves stdin for the header and returns curl's failure status; unsupported or unpaired options return 64 before curl runs. It accepts only the declared collection methods, the exact JSON Content-Type, inline object bodies, one `file=@path` upload and literal `_payload` object data. Upload paths still require user authorization; multipart attributes, multiple files, URL/config/proxy/redirect/trace/output options and stdin bodies are not supported. The API validates JSON content; successful HTTP alone does not prove a successful operation: inspect the response and read back writes.

**Token validity**: 2 hours. After expiry, re-run `npx --yes @a4x/feishu-auth-cli@1.2.1 login`.

**Note:** feishu-auth staging and prod tokens are identical. No need to switch env -- the same token works for all CMS environments (Staging, Pre, Prod).

**API Key (for automation/CI)**: Historical documentation describes `cms_...` keys from CMS Admin → API Key Management for Transfer/Sync. Expiry (including the historical no-expiry claim), endpoint scopes, environment binding, rotation and revocation have not been verified in this restoration. Before use, verify the actual key's identity and available permissions, choose the least authorization the platform supports, and use an approved secret-delivery entry. Do not assume environment sharing or invent unsupported controls; unresolved access facts block the affected key operation.

### API Reference

| Op | Method | Path | Body |
|----|--------|------|------|
| List | GET | `/api/{slug}?where[field][equals]=value&limit=10&page=1&sort=-createdAt&depth=1` | - |
| Get | GET | `/api/{slug}/{id}` | - |
| Create | POST | `/api/{slug}` | JSON |
| Update | PATCH | `/api/{slug}/{id}` | JSON (partial) |
| Delete | DELETE | `/api/{slug}/{id}` | - |

> **POST**: `key` must be unique per collection. Duplicate returns 409 with the existing record's ID — use PATCH to update instead.
> **DELETE**: Unrecoverable — linked translations and sync logs are permanently lost. Always confirm with the user before deleting.

Query parameters:
- `depth` -- Controls relation expansion. `0` = IDs only (lightweight), `1` = expand one level (includes nested object fields like `name`). **Use `depth=1` for display, `depth=0` for bulk listing.**
- `where[field][op]=value` -- Operators: `equals`, `not_equals`, `like`, `contains`, `greater_than`, `less_than`, `in`
- `sort` -- Prefix `-` for descending
- `locale` -- Specify language (`en`, `zh-hans`, `locale=all` returns all locale values)

### Write Operation Body Examples

**Create a member feature:**
```bash
cms_curl /api/member-features -X POST \
  -H "Content-Type: application/json" \
  -d '{"key": "ai_detection", "name": "AI Detection", "subtitle": "Smart alerts"}'
```

**Update a paywall (partial):**
```bash
cms_curl "/api/paywalls/${PAYWALL_ID}" -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"description": "Updated description", "_status": "draft"}'
```

**Paywall writable fields:** `tenantId`, `key`, `spmb`, `description`, `payType` ("0"=IAP/Google Play, "1"=Airwallex, "2"=Stripe, null=default), `components` (array of block objects), `_status` ("draft"/"published"), `i18nAutoTranslateEnabled` (bool)

**Publish (prerequisite for Transfer):**
```bash
cms_curl "/api/paywalls/${PAYWALL_ID}" -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"_status": "published"}'
```

### OEM Tenants (tenantId)

vicoo, itroncam, ismart, uniarchlife, monkeyvision, soliom, hthome, anlife, anverbatim, homeguardsmart, cyberviewplus, soliompro, eooeies, kiwibit, dzees, saferviz, guard, jxja, dzeesHome, rscamera, provisionhome

### Paywall Components

Paywall `components` array, each with `blockType`:

| blockType | Purpose |
|-----------|---------|
| navi-bar | Title + subtitle + background image (supports Hero Banner style) |
| carousel | Image carousel with title/subtitle |
| product-container | Subscription product cards |
| comparison | Plan feature comparison table |
| feature-list | Member benefits display |
| testimonial | User review carousel |
| subscription-terms | Free trial / cancellation / auto-renewal text |
| bottom-area | CTA buttons (free trial / subscribe / redeem) |
| purchase-notice | Payment method notices per platform (iOS/Android × native/Airwallex/Stripe) |
| text | Custom text block with configurable style, color, and alignment |

For full field definitions and example JSON, read `references/paywall-components.md`.

---

## Promotion (Touchpoint Assets)

Touchpoint assets (popup/banner/video status bar), core unit is imageCard component (one image + redirect link).

For field definitions, creation examples, and known constraints, read `references/promotion-components.md`.

> **Creating or updating a promotion? Ask these BEFORE writing any code:**
> 1. "图片上有文字/文案吗？需要做多语言翻译吗？" — 含文案 → 使用 `promotion-i18n` skill；无文案 → 遍历 23 locale 写入同一张图
> 2. "用户可以关闭吗？关闭后还需要再次展示吗？" — 决定 `dismissible` 和 `skipNoThanks`
> 3. "点击后跳转到哪？" — 从 `references/promotion-components.md` actionLink 格式表中提供选项

> **PATCH promotion components: GET first, include block `id`.** Without `id`, the API dissociates the image — `imageUrl` becomes null and the App shows nothing. This is the single most common mistake with this API. Always: GET → extract each block's `id` → include `id` in PATCH body.

### i18n Differences: Paywall vs Promotion

| Collection | Text fields | Image fields | Translation method |
|-----------|-------------|-------------|-------------------|
| Paywall | i18n (title/subtitle etc.) | **NOT** i18n, shared across all locales | Write English → Publish → User triggers translation in Admin |
| Promotion | — | **IS** i18n, each locale can have different images | Write per locale (see below) |

### Promotion Image Per-locale Write

**If images contain text that needs translation** (e.g., localized popup/banner), use the `promotion-i18n` skill instead — it handles Figma translation, image export, compression, and CMS upload in one workflow.

For universal images (no text), first read `/api/promotions/{id}?locale=all&depth=0` and preserve the complete components array, block IDs, non-image fields and every existing locale value. Identify the target block by its ID, not its array position.

Before constructing a write, verify the current API's array replacement and locale merge semantics from its contract or an approved isolated fixture. If those semantics cannot be confirmed, stop the affected write. For each authorized locale, derive a payload from the full original document and change only the target image; never send a one-element array cut from `components[0]`.

Write one locale at a time with `cms_curl`, check HTTP and business results, then read back all locales and compare against the preserved original. Continue only when the intended image changed and every other component, field and locale remained intact. On an error, unknown outcome or unexpected diff, stop and report completed and unexecuted locales without blindly replaying writes.

---

## Translation

> Translation jobs are auto-triggered on Publish via Admin UI. Never POST to `translation-jobs` directly — confirm English content is correct first, then the user publishes through Admin to trigger translation.

Translations managed via `translation-jobs` collection (NOT Payload's `?locale=` parameter):

```bash
# Check translation progress for a paywall
cms_curl "/api/translation-jobs?where[entityId][equals]=${ID}&limit=50"

# Check pending translations
cms_curl '/api/translation-jobs?where[status][equals]=pending'

# Check specific locale translation result
cms_curl "/api/translation-jobs?where[entityId][equals]=${ID}&where[targetLocale][equals]=ja"
```

Supported locales (23): ar, cs, de, es, fi, fi-fi, fr, he, he-il, id, it, ja, ko, pl, pt, pt-br, pt-pt, ru, th, tr, vi, zh-hans, zh-hant

---

## Cross-environment Sync (Transfer)

> **Transfer is irreversible** — there is no rollback API. Verify content correctness before pushing.
> **Transfer only works on published documents** (`_status: "published"`). Draft content is silently skipped.

### Environment Flow

```
Staging (read/write) → Pre (read-only) → Prod (read-only)
```

- **Staging**: All create/edit operations happen here
- **Pre**: Receives staging sync data, for QA verification, read-only
- **Prod**: Receives pre sync data, live users. No public URL -- Pre→Prod transfer is handled server-side by Pre's backend, users never call Prod API directly

### Transfer API

```
POST /api/transfer/{collection}/{key}
```

Pushes from **current environment** to **next environment** (staging→pre or pre→prod).

| Parameter | Description |
|-----------|-------------|
| `collection` | `paywalls` or `promotions` |
| `key` | Document's unique business key (not MongoDB ID) |
| Auth | feishu-auth token (`Authorization: Bearer {token}`) or API Key |
| Prerequisite | Document must be **published** status |

**Auto-handled:**
- Media files sync automatically (S3 transfer + metadata upsert), no separate media push needed
- Multi-language data synced in full (all locale translations)
- Records are upserted by key; this is not authorization to replay an unknown or partially successful transfer
- Media sync failure does not block main content sync

### Transfer Examples

**Staging → Pre (single):**
```bash
BASE_URL="https://marketing-cms-staging-us.addx.live"
cms_curl /api/transfer/paywalls/my_paywall_key -X POST \
  -H "Content-Type: application/json"
```

**Pre → Prod (batch):**

1. Produce a local plan listing the exact source/target environments, tenant, collection, keys and item count after verifying published content. This is a read-only preview, not a server-side dry-run API.
2. Check that existing user authorization covers that exact batch. If it does, continue without asking again; otherwise obtain only the missing scope before any POST. Set `BASE_URL` to the verified Pre URL for Pre→Prod.
3. Execute one planned key at a time through `cms_curl`. Require HTTP success, parsed business `success: true`, and destination-content/media readback before the next key. For internal-only Prod, use an approved server-side result/readback path; if unavailable, the result is unknown and the batch stops.
4. Media failure, HTTP/business failure, unknown outcome or unexpected content stops the batch immediately, even if the main document was synced. Report completed, failed/unknown and unexecuted keys separately; do not retry blindly. Reconcile actual state before planning any further attempt.

**Response format:**

First sync:
```json
{"success": true, "action": "created", "id": "..."}
```

Repeated sync (update):
```json
{"success": true, "action": "updated", "id": "..."}
```

### Sync Logs

Each transfer is logged in `sync-logs` collection:

```bash
cms_curl '/api/sync-logs?sort=-createdAt&limit=10'
```

Fields: `collection`, `documentKey`, `sourceEnv`, `targetEnv`, `status` (success/failed), `timestamp`, `errorMessage`

### Transfer Notes

- **feishu-auth token works for all environments** (staging/pre/prod share the same auth)
- Transfer is sequential only: staging→pre→prod, cannot skip pre to push directly to prod
- Only `paywalls` and `promotions` support transfer

---

## Pre-flight Checklist

Before executing any write operation, verify:

- [ ] The user authorized this operation, target environment, tenant and object; read back the result after writing.

- [ ] PATCH promotion: included block `id` from GET? (image loss if missing)
- [ ] Transfer: document is published? content verified? (irreversible)
- [ ] DELETE: confirmed with user? (unrecoverable)
- [ ] POST: key is unique? (409 if duplicate)
- [ ] Translation: NOT creating jobs manually? (system auto-triggers)
- [ ] Save badge: 2+ visibleProducts? (formula needs comparison pair)

## Error Diagnosis

| HTTP Code | Scenario | Cause | Fix |
|-----------|----------|-------|-----|
| 401 | Any API call | Token expired or missing | Re-run `npx --yes @a4x/feishu-auth-cli@1.2.1 login` to get fresh token |
| 403 | Any API call | API Key format invalid or insufficient permissions | Verify token is from feishu-auth-cli, not a raw API key |
| 409 | POST create | Duplicate key | Response body contains existing record ID, use PATCH to update instead |
| 400 | PATCH promotion | Missing block `id` field | GET first to obtain current components `id`, include in PATCH |

## Examples

### Bad -- Using tenantId as path ID

```
User: "Show vicoo paywalls"
AI: GET /api/paywalls/vicoo -> 404 (vicoo is tenantId, not document ID)
```

### Good -- Filter by tenantId

```
User: "Show vicoo paywalls"
AI: GET /api/paywalls?where[tenantId][equals]=vicoo&depth=1 -> returns all vicoo paywalls with expanded fields
```

### Bad -- Creating paywall without required fields

```
User: "Create a new paywall for soliom"
AI: POST /api/paywalls {"tenantId": "soliom"} -> created but missing key, description, components
```

### Good -- Creating with minimal viable fields

```
User: "Create a new paywall for soliom"
AI: POST /api/paywalls {
  "tenantId": "soliom",
  "key": "soliom_main_paywall",
  "spmb": "vip_purchase_product_page",
  "description": "Soliom main subscription paywall",
  "payType": "2",
  "_status": "draft",
  "components": [
    {"blockType": "navi-bar", "componentType": "navi-bar", "title": "Upgrade Plan", "style": "default_app_bar"},
    {"blockType": "bottom-area", "componentType": "bottom-area", "style": "default", "directPay": true}
  ]
}
```

`spmb` follows the reference's required-field contract. `componentType` matches its examples; this does not assert that the server requires it.

### Bad -- Checking translation with locale parameter

```
User: "Check Japanese translation for paywall X"
AI: GET /api/paywalls/X?locale=ja -> returns English content (locale param not supported for paywall text)
```

### Good -- Using translation-jobs collection

```
User: "Check Japanese translation for paywall X"
AI: GET /api/translation-jobs?where[entityId][equals]=X&where[targetLocale][equals]=ja
-> returns translation job with status and result fields
```

### Bad -- Creating promotion without locale

```
User: "Create gen3 phase 2 promotion assets"
AI: POST /api/promotions without locale parameter
Problem: image only written to en locale, users of other languages see no image
```

### Good -- Universal image written to all locales

```
User: "Create gen3 phase 2 promotion assets"
AI: Create the authorized record -> read its full components and all locales ->
    verify PATCH semantics -> update only the target image per authorized locale ->
    read back and preserve every other component/field/locale; stop on unknown or failed results
```

### Bad -- Skipping Pre, pushing directly to Prod

```
User: "Push staging paywall to prod"
AI: Call Staging /api/transfer/paywalls/key hoping it goes to Prod -> only reaches Pre
```

### Good -- Sequential transfer: Staging→Pre→Prod

```
User: "Push staging paywall to prod"
AI: 1. Call Staging /api/transfer/paywalls/{key} -> pushes to Pre
    2. Verify content on Pre
    3. Call Pre /api/transfer/paywalls/{key} -> Pre pushes to Prod internally
    (Same feishu-auth token works for all environments)
```

### Good -- Batch transfer pattern

```
User: "Push all gen3 promotions to prod"
AI: 1. Query Pre, then preview exact keys, tenant, environments and count without writing
    2. Proceed when existing authorization covers that exact batch; otherwise clarify missing scope
    3. Transfer one key; check HTTP, business success and destination content/media before the next
    4. Stop on failure or unknown outcome; report completed/failed-or-unknown/unexecuted keys
```

## References

- [Payload CMS REST API](https://payloadcms.com/docs/rest-api/overview)
- Admin UI: https://marketing-cms-staging-us.addx.live/admin
- Paywall component field details: `references/paywall-components.md`
- Promotion component field details: `references/promotion-components.md`

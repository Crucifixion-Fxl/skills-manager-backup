---
name: marketing-cms
description: "Use when managing Paywall pages, Promotion assets (popup/banner/video), member features, comparison templates, translation jobs, or cross-environment sync in Marketing CMS (Payload CMS). Triggers: 'paywall', 'promotion', 'CMS content', 'member feature', 'translation progress', 'sync to pre', 'transfer', 'marketing page', 'OEM tenant config', 'upload image', 'push to prod'."
dependencies:
  - feishu-auth
  - promotion-i18n
---

# marketing-cms

Marketing Content Center -- Payload CMS platform for App paywall, promotion, and subscription content.

## Dependencies

Requires `feishu-auth` skill for authentication. First-time setup:

```bash
# 1. Configure registry
npm config set @a4x:registry https://gitlab.addx.ai/api/v4/projects/1021/packages/npm/
# 2. Configure auth (requires GitLab PAT with `api` scope; `read_api` is NOT sufficient)
npm config set //gitlab.addx.ai/api/v4/projects/1021/packages/npm/:_authToken <your-gitlab-pat>
```

## First-touch Decision Tree

On receiving a user request, determine the target:

```
User request → Identify target
├── Paywall page (carousel + product card + pricing) → paywalls collection
├── Touchpoint asset (popup/banner/video image card) → promotions collection
├── Image upload → media collection
├── Plan comparison table → comparison-templates collection
├── Translation progress → translation-jobs collection
├── Push/sync to pre or prod → Transfer API (see "Cross-environment Sync")
└── Unclear → Ask user: paywall or promotion?
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

Via `feishu-auth` skill:

```bash
TOKEN=$(npx --yes @a4x/feishu-auth-cli@1.2.1 token 2>/dev/null)
if [ -z "$TOKEN" ]; then
  npx --yes @a4x/feishu-auth-cli@1.2.1 login
  TOKEN=$(npx --yes @a4x/feishu-auth-cli@1.2.1 token)
fi

# IMPORTANT: use --globoff to prevent curl from interpreting [] in query params
curl --globoff -H "Authorization: Bearer ${TOKEN}" "${BASE_URL}/api/paywalls?limit=5"
```

**Token validity**: 2 hours. After expiry, re-run `npx --yes @a4x/feishu-auth-cli@1.2.1 login`.

**Note:** feishu-auth staging and prod tokens are identical. No need to switch env -- the same token works for all CMS environments (Staging, Pre, Prod).

**API Key (for automation/CI)**: Format `cms_...`, created from CMS Admin → API Key Management. No expiry, but only supports certain endpoints (Transfer API, Sync API).

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
curl --globoff -X POST -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/api/member-features" \
  -d '{"key": "ai_detection", "name": "AI Detection", "subtitle": "Smart alerts"}'
```

**Update a paywall (partial):**
```bash
curl --globoff -X PATCH -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/api/paywalls/${PAYWALL_ID}" \
  -d '{"description": "Updated description", "_status": "draft"}'
```

**Paywall writable fields:** `tenantId`, `key`, `spmb`, `description`, `payType` ("0"=IAP/Google Play, "1"=Airwallex, "2"=Stripe, null=default), `components` (array of block objects), `_status` ("draft"/"published"), `i18nAutoTranslateEnabled` (bool)

**Publish (prerequisite for Transfer):**
```bash
curl --globoff -X PATCH -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/api/paywalls/${PAYWALL_ID}" \
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

The manual approach below is only for universal images (no text) that use the same image across all locales:

```bash
# Step 1: GET current block id (required for PATCH)
BLOCK_ID=$(curl --globoff -s -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/promotions/${ID}?depth=0" | python3 -c "import sys,json; print(json.load(sys.stdin)['components'][0]['id'])")

# Step 2: PATCH each locale with block id included
for locale in en ar cs de es fi fi-fi fr he he-il id it ja ko pl pt pt-br pt-pt ru th tr vi zh-hans zh-hant; do
  curl --globoff -X PATCH -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "${BASE_URL}/api/promotions/${ID}?locale=$locale" \
    -d "{\"components\":[{\"id\":\"${BLOCK_ID}\",\"blockType\":\"imageCard\",\"image\":\"${MEDIA_ID}\"}]}"
done

# Step 3: Verify all locale values
curl --globoff -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/promotions/${ID}?locale=all&depth=0"
```

---

## Translation

> Translation jobs are auto-triggered on Publish via Admin UI. Never POST to `translation-jobs` directly — confirm English content is correct first, then the user publishes through Admin to trigger translation.

Translations managed via `translation-jobs` collection (NOT Payload's `?locale=` parameter):

```bash
# Check translation progress for a paywall
curl --globoff -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/translation-jobs?where[entityId][equals]=${ID}&limit=50"

# Check pending translations
curl --globoff -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/translation-jobs?where[status][equals]=pending"

# Check specific locale translation result
curl --globoff -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/translation-jobs?where[entityId][equals]=${ID}&where[targetLocale][equals]=ja"
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
- Idempotent: repeated transfer is safe (upsert by key)
- Media sync failure does not block main content sync

### Transfer Examples

**Staging → Pre (single):**
```bash
curl --globoff -s -X POST \
  -H "Authorization: Bearer ${STAGING_TOKEN}" \
  -H "Content-Type: application/json" \
  "https://marketing-cms-staging-us.addx.live/api/transfer/paywalls/my_paywall_key"
```

**Pre → Prod (batch):**
```bash
PRE_TOKEN=$(npx --yes @a4x/feishu-auth-cli@1.2.1 token)
PRE="https://marketing-cms-pre-us.addx.live"

for key in paywall_key_1 paywall_key_2; do
  curl --globoff -s -X POST \
    -H "Authorization: Bearer ${PRE_TOKEN}" \
    -H "Content-Type: application/json" \
    "${PRE}/api/transfer/paywalls/${key}"
done
```

**Response format:**
```json
{"success": true, "action": "created", "id": "..."}   // first sync
{"success": true, "action": "updated", "id": "..."}   // repeated sync (update)
```

### Sync Logs

Each transfer is logged in `sync-logs` collection:

```bash
curl --globoff -s -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/api/sync-logs?sort=-createdAt&limit=10"
```

Fields: `collection`, `documentKey`, `sourceEnv`, `targetEnv`, `status` (success/failed), `timestamp`, `errorMessage`

### Transfer Notes

- **feishu-auth token works for all environments** (staging/pre/prod share the same auth)
- Transfer is sequential only: staging→pre→prod, cannot skip pre to push directly to prod
- Only `paywalls` and `promotions` support transfer

---

## Pre-flight Checklist

Before executing any write operation, verify:

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
  "description": "Soliom main subscription paywall",
  "payType": "2",
  "_status": "draft",
  "components": [
    {"blockType": "navi-bar", "title": "Upgrade Plan", "style": "default_app_bar"},
    {"blockType": "bottom-area", "style": "default", "directPay": true}
  ]
}
```

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
AI: POST /api/promotions?locale=en to create record ->
    iterate all locales, PATCH /api/promotions/{id}?locale={locale} to write same image
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
AI: 1. Query Pre with where[key][like]=gen3 to find all matching keys
    2. Iterate each key: POST /api/transfer/promotions/{key}
    3. Summarize results table (key / action / success)
```

## References

- [Payload CMS REST API](https://payloadcms.com/docs/rest-api/overview)
- Admin UI: https://marketing-cms-staging-us.addx.live/admin
- Paywall component field details: `references/paywall-components.md`
- Promotion component field details: `references/promotion-components.md`

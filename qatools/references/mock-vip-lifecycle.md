# Mock VIP lifecycle

Read this reference for `mock-vip-lifecycle`. Authentication, staging selection,
and credential handling are defined in
[`authentication-and-environments.md`](authentication-and-environments.md).

## Contents

- [Purpose and boundary](#purpose-and-boundary)
- [Request contract](#request-contract)
- [Tenant and channel selection](#tenant-and-channel-selection)
- [Execution and readback](#execution-and-readback)
- [Failure handling](#failure-handling)

## Purpose and boundary

`POST /api/mock-pay/notify` delegates to iot-service
`/private/mock-pay/notify`, replaying the application lifecycle handlers for
`create`, `renew`, `switch`, `cancel`, `expire`, or `refund`. It writes mock-marked
VIP state and refreshes device tier cache without real payment.

This operation is staging-only and must never target a real account. Before the
write, qa-tools calls `/private/account/check-internal`. iot-service independently
enforces both:

1. the account email ends with `@qa.test`;
2. authoritative `user.internal == 1`.

A caller-supplied email or claimed test label is not proof. Stop if either guard
cannot be verified. Mock subscriptions remain test data and must not be treated as
real paid records or included in financial acceptance.

Use this operation when a payment sandbox is unavailable or the scenario requires
an exact lifecycle callback without consuming payment-provider quota. Do not use
it when the intended acceptance criterion is real provider checkout or callback
behavior.

## Request contract

| Field | Required | Constraint |
|---|---|---|
| `userId` | yes | Positive ID of the proven internal test account |
| `tenantId` | yes | Must match both the account and product |
| `productId` | yes except switch semantics below | Numeric ID from a fresh product-list lookup |
| `channel` | yes | `stripe`, `apple`, `google`, or `airwallex` |
| `event` | yes | `create`, `renew`, `switch`, `cancel`, `expire`, or `refund` |
| `newProductId` | for `switch` | Numeric target product ID from the same tenant |
| `transactionId` | no | Omit to generate `mock_*`; supply only for an intentional idempotency test |

Never quote numeric product IDs in JSON. For renew/cancel/expire/refund, resolve
the active product and transaction context from authoritative account/VIP state;
do not guess from an earlier conversation.

## Tenant and channel selection

| Tenant | Recommended channel | Constraint |
|---|---|---|
| `vicoo`, `viconature` | `stripe` | These tenants also support configured IAP/Airwallex paths |
| Other IAP-only tenants | `apple` or `google` | Stripe/Airwallex may be rejected by tenant configuration |

The recommendation is not a substitute for current configuration. If the user
requests a specific channel, verify that the tenant enables it. Always pass the
tenant explicitly.

Obtain product IDs from `/api/product/list` and filter by tenant plus the user's
plan/service requirements. For `switch`, verify both old and new products belong
to the same intended tenant.

## Execution and readback

Example create request:

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/mock-pay/notify" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "userId":1144064,
    "tenantId":"kiwibit",
    "productId":1014201,
    "channel":"apple",
    "event":"create"
  }' | python3 -m json.tool
```

Example switch request:

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/mock-pay/notify" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "userId":1144064,
    "tenantId":"kiwibit",
    "productId":1014201,
    "newProductId":1014202,
    "channel":"apple",
    "event":"switch"
  }' | python3 -m json.tool
```

Require `result == 0` and preserve `mockTransactionId`, `userVipId`, channel,
and event. Then query the authoritative account/VIP view for the same account and
tenant.

Readback depends on the event:

| Event | Minimum business readback |
|---|---|
| `create` | Intended product/VIP is active |
| `renew` | Expiry/renewal state advanced for the same record |
| `switch` | Target product active and old product transition reflected |
| `cancel` | Cancellation state matches immediate/end-of-period semantics |
| `expire` | VIP inactive or expiry moved to the past |
| `refund` | Refunded/invalid entitlement state reflected |

For profile/account verification, use the normalized email and matching tenant.
Report before/after fields when the user asked to validate a transition. A
successful mock endpoint response alone is not final proof.

## Failure handling

qa-tools validation errors:

| Error | Response |
|---|---|
| `tenantId is required` | Resolve the account/product tenant; never infer it only from channel |
| invalid `channel` or `event` | Correct the explicit enum; do not substitute another lifecycle |
| `NOT_TEST_USER` | Stop; create/use an internal `@qa.test` account instead |
| `CHECK_INTERNAL_FAILED` | Stop; investigate qa-tools/iot-service availability before any write retry |

iot-service errors:

| Code | Meaning | Response |
|---|---|---|
| `-1000` | missing/non-positive user ID | Correct the exact target |
| `-1001` | email is not `@qa.test` | Stop; do not bypass |
| `-1002` | over 30 calls/minute for the user | Stop and respect the rate limit |
| `-1006` | required `productId` absent | Resolve the active/target product |
| `-1007` | `switch` missing `newProductId` | Resolve a same-tenant target product |
| `403` | authoritative `internal != 1` | Stop; do not mutate this account |
| `500 DISPATCH_ERROR` | lifecycle handler failed | Capture the error and read back state before deciding on retry |

An uncertain response, timeout, or dispatch error may occur after a write. Query
the account/VIP state and transaction identity before retrying. If an intentional
idempotency test uses an explicit `transactionId`, keep it stable; otherwise do
not manufacture a duplicate transaction to work around uncertainty.

# Test accounts and VIP provisioning

Read this reference for `register-only`, `register-and-vip`, and
`add-vip-to-existing`. Authentication and environment rules are in
[`authentication-and-environments.md`](authentication-and-environments.md).

## Contents

- [Live product selection](#live-product-selection)
- [Operation 1: register-only](#operation-1-register-only)
- [Operation 2: register-and-vip](#operation-2-register-and-vip)
- [Operation 3: add-vip-to-existing](#operation-3-add-vip-to-existing)
- [Related queries and cancellation](#related-queries-and-cancellation)
- [Failure and retry rules](#failure-and-retry-rules)
- [Output contract](#output-contract)

## Live product selection

Never maintain or reuse a hard-coded product table. Every time product selection
is needed, query the current qa-tools view of iot-service:

```bash
curl -sS "$QA_TOOLS_URL/api/product/list" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  | python3 -m json.tool
```

The response is `{result,msg,data:[...]}`. Relevant row fields are:

| Field | Type | Meaning |
|---|---|---|
| `id` | number | `productId` used by quick setup and subscription calls |
| `subject` | string | Current display name; use this in output |
| `month` | number | `1` monthly, `12` yearly |
| `tierType` | number | `3` Gen3; non-3 is the broader V2/Gen2 family |
| `tierServiceType` | number | `0` cloud, `1` 4G, `2` Nature/bird feeder |
| `subscriptionPeriod` | number | Billing period reference |
| `maxDeviceNum` | number | Maximum device count; `-1` means unlimited |
| `tenantId` | string or null | Product tenant; must match the account and request |

`tierType !== 3` includes more than ordinary V2 cloud plans. Apply the user's
device count, period, and service-type requirements so 4G, emergency, Nature,
or code products are not mislabeled as a generic V2 plan.

### Tenant decision

- An explicit brand/tenant selects that tenant.
- No tenant or brand defaults ordinary cloud scenarios to `vicoo`.
- Nature/Bird/feeder wording is tenant-ambiguous. Ask which tenant before
  filtering, then filter by both `tenantId` and `tierServiceType == 2`.
- An isolated brand word whose meaning is unclear also requires confirmation.
- `tenantId` sent to quick setup must equal the selected product row's tenant.

### Product decision

- A supplied `productId` still requires a fresh list lookup to prove it exists
  and obtain its `subject` and tenant.
- A sufficiently specific plan name may be resolved without a second confirmation
  when the fresh list has exactly one match.
- If the user does not specify a service type, filter candidates to
  `tierServiceType == 0` so 4G and Nature plans are not mixed into ordinary
  cloud-plan choices.
- If the request contains no plan clue, show the relevant live rows as
  `productId + subject` and wait for the user's choice.
- If the user explicitly delegates the choice, recommend the lowest-ID ordinary
  Gen3 monthly row and explain that it is the basic, shortest-cycle test plan;
  wait for confirmation before creating a subscription.
- Product IDs in JSON are numbers, never strings.

Do not cache the response between separate user requests.

## Operation 1: register-only

Use `POST /api/test-user/register` when the user needs an internal test account
without VIP or subscription state.

| Field | Required | Default/constraint |
|---|---|---|
| `tenantId` | no | `vicoo` |
| `countryNo` | no | `US` |
| `password` | no | `Aa123456`; server stores a SHA-256 derivative |
| `phone` | no | Omitted by default |
| `supportFreeLicense` | no | Omit for legacy behavior; set `true` only for a Free License fixture |

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/test-user/register" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenantId":"vicoo","countryNo":"US"}' \
  | python3 -m json.tool
```

The server generates a 4–5 character lowercase alphanumeric `@qa.test` email.
Success requires `result == 0`. `marked:true` proves the internal-user mark was
applied. `marked:false` means the account exists but marking failed; report that
state and use `/api/test-user/mark` only when explicitly requested.

For multiple accounts, call the register endpoint separately or use the documented
batch endpoint. Preserve each returned `userId`, email, and `marked` result.

## Operation 2: register-and-vip

Use `POST /api/quick-setup` for a new account that should immediately receive
test VIP state. The request must include:

| Field | Required | Constraint |
|---|---|---|
| `email` | yes | Lowercase `@qa.test`; caller generates it |
| `password` | yes | Usually `Aa123456` |
| `deviceType` | yes | Use `0` only when no more specific supported type is known |
| `modelNo` | no | Let iot-service choose by device type when omitted |
| `productId` | yes | Numeric ID from the fresh product list |
| `tenantId` | no | Defaults to `vicoo`; must be explicit for any other product tenant |
| `mockPay` | no | Explicit value wins; otherwise tenant routing decides |
| `mockChannel` | conditional | Used by mock-pay; must be supported by the tenant |
| `countryNo` | no | Defaults to `US` |
| `phone` | no | Omitted by default |
| `trialPeriodDays` | no | Omit for immediate test charge |

Generate the email with 4–5 lowercase alphanumeric characters and no semantic
prefix unless the user explicitly requests one. iot-service lowercases the full
email before persistence. If the user supplies uppercase characters, explain
the exact lowercase stored/login value and wait for confirmation before
continuing. After confirmation, use that normalized value in output.

Quick setup runs serially:

1. register;
2. login for marking;
3. bind a Mock device;
4. either create Stripe Customer then create Subscription, or call mock-pay;
5. return completed identifiers.

When `mockPay` is omitted, `vicoo/viconature` route to Stripe and other IAP-only
tenants route to mock-pay. The mock-pay branch sets `grantMockAiTier=false` so QA
AI Tier `90001` does not contaminate subscription/CDC verification. It returns a
`mockTransactionId` and does not create Stripe Customer/Subscription steps.

Example Stripe request:

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/quick-setup" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email":"a7f2k@qa.test",
    "password":"Aa123456",
    "deviceType":0,
    "productId":30401,
    "tenantId":"vicoo",
    "countryNo":"US"
  }' | python3 -m json.tool
```

For the Stripe branch, require all five steps to be `success` and a non-empty
`completedData.subscriptionId`. For the mock-pay branch, require all four steps
to be `success`, a non-empty `completedData.mockTransactionId`, and absence of
the Stripe Customer/Subscription steps. Then perform the appropriate business
readback; pipeline completion alone is not final proof.

## Operation 3: add-vip-to-existing

Use this operation for an existing internal `userId`. Do not use quick setup,
because that creates another account.

### Step 1: ensure a test Customer and payment method

Call `POST /api/stripe/customer/attach-test-card` with the exact `userId` and,
when known, its registered email. Ask for the email only if it cannot be queried;
it makes the test Customer identifiable in Stripe Dashboard. Use the anonymous
path only when the user cannot provide or retrieve it and still chooses to
continue.

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/stripe/customer/attach-test-card" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":26259,"email":"ab3cd@qa.test"}' \
  | python3 -m json.tool
```

The endpoint is idempotent:

- existing Customer with default payment method/source: returns `skipped:true`;
- existing Customer without one: attaches the test payment method;
- no Customer: creates one with `metadata[userId]`, optional email, and
  `source=tok_visa`, then binds it in iot-service.

It is valid only with a Stripe test key. A 403 indicating non-test mode is a hard
stop, not permission to use another environment or real payment method.

### Step 2: create the selected subscription

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/stripe/subscription" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":26259,"productId":30401}' \
  | python3 -m json.tool
```

If the account already has an active subscription, this can create a second one.
Do not cancel or replace the old subscription unless the user requested that
mutation. Read back account/VIP state after creation.

## Related queries and cancellation

Query account information by normalized email and tenant:

```bash
curl -sS -G "$QA_TOOLS_URL/api/user-query/account-brief-info" \
  --data-urlencode "email=ab3cd@qa.test" \
  --data-urlencode "tenantId=vicoo" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  | python3 -m json.tool
```

Get the five-minute login OTP only for an internal `@qa.test` account:

```bash
curl -sS -G "$QA_TOOLS_URL/api/test-user/login-code" \
  --data-urlencode "email=ab3cd@qa.test" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  | python3 -m json.tool
```

Cancel only with the `userId` and `subscriptionId` returned for the same internal
account:

```bash
curl -sS -X POST "$QA_TOOLS_URL/api/stripe/subscription/cancel" \
  -H "Authorization: Bearer $QA_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":26302,"subscriptionId":"sub_xxx"}' \
  | python3 -m json.tool
```

The `userId` enables owner checking before a webhook has created a local order;
iot-service also verifies Customer and Subscription ownership. Never substitute
another test user's ID.

## Failure and retry rules

Quick setup has no automatic rollback. A failed later step may leave an account,
device, Stripe Customer, or mock transaction. Preserve and report every identifier
in `completedData` before deciding the next action.

| Failed step/condition | Safe response |
|---|---|
| `register`: duplicate email | Generate a new lowercase email and retry registration only |
| `bind-device`: unsupported type/schema | Verify the intended model/type; use `deviceType=0` only when appropriate |
| `create-stripe-customer`: missing/non-test key | Stop and have operations verify staging uses `sk_test_*` |
| `create-subscription`: binding/payment method failure | Reuse the existing user/Customer through Operation 3 after checking state |
| attach-test-card bind failure | Verify the exact internal user; do not attach to another account |

Stripe metadata Search may lag about one minute after Customer creation. The
pipeline's first subscription is not affected. If a separate follow-up action
cannot find the new Customer immediately, wait for Search consistency rather
than creating a duplicate.

## Output contract

For `register-only`, return `userId`, lowercase generated email, the password the
caller chose/defaulted, tenant, country, and `marked` status.

For Stripe `register-and-vip` or `add-vip-to-existing`, return:

- `userId` and lowercase email;
- tenant and product ID;
- the current product `subject` from the same live list response;
- Mock device serial when created;
- Customer and Subscription IDs;
- subscription status/trial information;
- the account/VIP readback result.

For the mock-pay quick-setup branch, return `mockTransactionId`, channel, event,
and the business-state readback; do not wait for or invent a Subscription ID.

If the selected ID disappeared from the fresh list, report it as unknown/removed
and stop instead of using a stale local name.

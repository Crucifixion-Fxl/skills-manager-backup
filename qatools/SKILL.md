---
name: qatools
description: Use when QA or developers need to create internal test accounts, create or manage test VIP or subscription state, replay payment lifecycle events, adjust test VIP expiry, or exercise Free License test scenarios.
---

# qatools

Use the qa-tools REST API for controlled test-account, VIP, subscription, and
Free License scenarios. Select the operation here, then read only the reference
required for that operation.

## Description

| Operation | Use when |
|---|---|
| `register-only` | Create one or more internal `@qa.test` accounts without VIP |
| `register-and-vip` | Create a new internal account and provision its initial test VIP |
| `add-vip-to-existing` | Add a test subscription to an existing internal `userId` |
| `mock-vip-lifecycle` | Replay `create/renew/switch/cancel/expire/refund` without real payment |
| `set-vip-endtime` | Move one known VIP record to an exact expiry time |
| `free-license-lifecycle` | Query, create, refresh, revoke, reset, and clean up controlled `1002/1005/1006` scenarios |

Operations 1–5 are staging-only unless an authorized qa-tools contributor
explicitly requests the local override. Free License is a separate operation
with explicit `staging`, `pre`, or `prod` environment selection and separate
credentials.

Do not use this skill for real customer accounts, production subscription
changes, real payment operations, or production data repair.

## Required routing

Read the linked reference completely when its condition applies:

- For any Operation 1–5 call, read
  [authentication-and-environments.md](references/authentication-and-environments.md).
  It defines token priority, credential handling, staging defaults, 401 recovery,
  and the contributor-only local override.
- For `register-only`, `register-and-vip`, or `add-vip-to-existing`, read
  [account-and-vip.md](references/account-and-vip.md). It defines live product
  lookup, tenant matching, request shapes, pipeline checks, failure recovery,
  cancellation, and output fields.
- For `mock-vip-lifecycle`, read
  [mock-vip-lifecycle.md](references/mock-vip-lifecycle.md). It defines supported
  channels/events, internal-user guards, write/readback sequence, and errors.
- For `set-vip-endtime`, read
  [vip-endtime.md](references/vip-endtime.md). It defines exact row selection,
  epoch seconds, original-value capture, write, and business readback.
- For `free-license-lifecycle`, read
  [free-license.md](references/free-license.md). It defines environment isolation,
  readiness, fixture rules, mutation ownership, the status Oracle, and cleanup.
- Read [api-reference.md](references/api-reference.md) only when exact endpoint
  fields, response envelopes, error codes, or local iot-service debugging facts
  are needed.

All references are one hop from this file. Do not follow an old copied procedure
instead of the referenced source.

## Select the operation

| User goal | Operation |
|---|---|
| New account, no VIP | `register-only` |
| New account with VIP | `register-and-vip` |
| Existing `userId` needs VIP | `add-vip-to-existing` |
| Replay a lifecycle event | `mock-vip-lifecycle` |
| Set one existing VIP to an exact time | `set-vip-endtime` |
| Exercise Free License or `1002/1005/1006` | `free-license-lifecycle` |

Do not run `register-and-vip` for an existing `userId`; that creates another
account. Do not translate Free License requests into product, Stripe, or ordinary
VIP operations.

## Shared execution contract

1. Select exactly one target environment. Operations 1–5 default to staging;
   local requires the explicit contributor-only override. Free License never
   inherits a staging choice for pre/prod.
2. Load credentials according to the selected operation's reference. Never ask
   the user to paste a token into chat and never print, log, or persist a token in
   generated output.
3. Resolve the target from authoritative data before mutation:
   - for an existing account, confirm it is an internal test account;
   - for a new-account flow, use a synthetic `@qa.test` identity and verify the
     returned internal mark before relying on the account for later mutations;
   - query the current product list instead of using a memorized `productId`;
   - keep `tenantId` aligned with the account and selected product;
   - select an exact `userVipId`, device, record, or snapshot when required.
4. When the operation can overwrite or clean up existing state, capture its
   operation-specific baseline and recovery identifiers. Never claim ownership
   of a pre-existing record merely because an API returned it.
5. Execute the smallest requested mutation. Stop on an uncertain transport result,
   guard failure, stale snapshot, or unexpected response; read back state before
   deciding whether any retry is safe.
6. Verify the target business state with the operation-specific readback. A 2xx
   response or completed request is not sufficient proof.
7. Report the exact target, operation, returned identifiers, readback result, and
   any residual data or cleanup state. Redact credentials and unrelated personal
   data.

## Rules

- Mutations of existing state target only accounts that qa-tools/iot-service
  proves are internal test accounts. New-account flows must use synthetic
  `@qa.test` identities and verify marking. Caller-supplied email, `userId`,
  tenant, or a UI label is not sufficient proof for an existing target.
- Operations 1–5 use staging and Stripe test mode only. Never map their
  `cus_*`, subscriptions, or mock records onto production customers.
- `mock-vip-lifecycle` is staging-only and requires both the `@qa.test` email
  check and `user.internal == 1` guard. Do not bypass either guard.
- Keep `tenantId` consistent across account, product, quick setup, mock-pay, and
  readback. If the user's brand or Nature/Bird wording leaves tenant ambiguous,
  ask for the tenant before choosing a product.
- Product IDs are numbers. Query `/api/product/list` each time product selection
  is needed; do not cache or hard-code the catalog.
- A failed quick-setup pipeline does not roll back completed steps. Report every
  created account, device, Customer, subscription, or mock transaction before
  proposing recovery.
- Free License uses `FREE_LICENSE_URL/FREE_LICENSE_TOKEN`, not the generic
  staging variables for pre/prod. Its mutation/readiness/owner/cleanup rules in
  `free-license.md` are mandatory.
- Cancellation, cleanup, retry, revoke, and reset are mutations too; apply the
  same target proof, authorization, and readback requirements.

## Minimum output

For success, report:

- environment and operation;
- `userId`, normalized lowercase email, and tenant when applicable;
- exact product/VIP/device/record identifiers touched;
- subscription, Customer, mock transaction, snapshot, or cleanup identifiers
  produced by that operation;
- the business-state readback, not only the request response;
- remaining test data and, when the operation defines cleanup, its cleanup status.

For failure, report the failed step, returned code/message, all earlier steps
that succeeded, data already created, and the next safe action. Never imply an
automatic rollback.

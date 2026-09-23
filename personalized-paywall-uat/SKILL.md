---
name: personalized-paywall-uat
description: Quantitatively execute and certify Personalized Paywall UAT for Never, Lapsed, and future audience segments. Use when creating or validating UAT accounts, running staging evaluate-to-CMS checks, comparing H5 with design, testing physical iOS/Android banner and real prices, exercising Apple IAP, Google Play Billing, Stripe, or Airwallex cancellation and sandbox/test payment, verifying order/subscription/VIP/segment changes, collecting evidence, deciding API/UI/Native/Full UAT status, or repairing a failed UAT gate. Partial runs, mock prices, missing evidence, and self-reported PASS never count as certification.
---

# Personalized Paywall UAT

## Description

Use the target repository's machine-readable policy and gate as the certification
authority. This skill coordinates the run; it does not redefine pass criteria.

## Rules

The following SSOT, profile, execution, evidence, and repair rules are mandatory.

## Required SSOT

Read these files completely before acting:

- `testdata/personalized-offer/uat/uat-gate-policy.v1.json`
- `testdata/personalized-offer/uat/gate.mjs`
- `docs/testing/scenarios/personalized-paywall-uat-standard.html`
- the current scenario manifest and UAT runner

If any required SSOT file is absent, stop. Do not certify from prose, screenshots,
historical results, or a manually written PASS.

Read `references/device-payment-runbook.md` before P5-P9. When P6-P9 is in
scope, also read `references/payment-channel-runbook.md` for channel routing,
fixture preparation, provider-specific checkpoints, and evidence. Read
`references/owner-routing.md` after a failure or whenever payment is in scope.
For Android Google Play work, also read
`references/android-google-play-uat.md` before build or provider diagnosis.

## Certification Profiles

Choose the smallest profile matching the requested claim:

| Profile | Required phases | Only allowed certification |
|---|---|---|
| `API_GATE` | P0-P2 | `API_READY` |
| `UI_GATE` | P0-P4 | `UI_READY` |
| `NATIVE_GATE` | P0-P6 | `NATIVE_READY` |
| `FULL_UAT_GATE` | P0-P9 | `FULL_UAT_PASSED` |

Phases are cumulative:

| Phase | Quantified subject |
|---|---|
| P0 Contract | schema, manifest expansion, legacy-field rejection |
| P1 Fixture | one account per case and exact targeting-fact readback |
| P2 Staging API | evaluate, expected experiment/paywall, CMS matrix |
| P3 Mock H5 | deterministic content, cards, CTA, payload, visual checks |
| P4 Real-CMS H5 | complete live CMS payload rendered without fixture substitution |
| P5 Native | physical iOS/Android banner, navigation, host context, strict real prices |
| P6 Cancellation | every required channel, correct cancel semantics, zero backend mutation |
| P7 Payment | successful sandbox payments for the policy matrix |
| P8 Entitlement | order/subscription/VIP/profile/segment/restart/idempotency |
| P9 Observability | correlated events, delivery, retry, and reliability |

For `FULL_UAT_GATE`, the non-reducible payment baseline is:

| Channel ID | Required test mode | P6 cancellation | P7 success | P8 entitlement |
|---|---|---:|---:|---:|
| `APPLE_IAP` | Apple IAP Sandbox on physical iOS | 1/1 | 1/1 | 1/1 |
| `GOOGLE_PLAY` | Google Play Billing test purchase on physical Android | 1/1 | 1/1 | 1/1 |
| `STRIPE` | Stripe Test Mode through the supported product flow | 1/1 | 1/1 | 1/1 |
| `AIRWALLEX` | Airwallex demo/sandbox through the supported product flow | 1/1 | 1/1 | 1/1 |

All four logical channels are mandatory. Native IAP means both Apple IAP and
Google Play; one never substitutes for the other. A policy may add supported
platform-channel pairs or cases but must not
remove, rename, substitute, or mark any baseline channel `N/A`. `SKIPPED`,
`NOT_RUN`, mock-provider success, or missing channel evidence fails the full gate.
Use a unique account for each P7 channel case; do not reuse a consumed account.

The repository policy must enumerate every enabled platform-channel pair. A
Stripe or Airwallex result on one platform does not prove the other platform's
host adapter. If the policy does not yet contain a supported pair, update the
policy, denominator, and gate regression before claiming `FULL_UAT_PASSED`.

For every required check, use the denominator and comparison from the policy.
`SKIPPED`, `NOT_RUN`, missing evidence, a bad artifact hash, or a filtered selection
fails the named gate. Never hide a new scenario outside the policy denominator.

## Execution Workflow

### 1. Freeze identity and expand coverage

Record exact repository/app/SDK SHA, policy version, environment, region, manifest
hash, and requested profile. For native work also record the installed artifact
SHA/build identity separately from the current checkout, the remote H5 deployment
identity when applicable, a non-sensitive device reference, model, OS, flavor,
locale, and payment test mode. Pulling a branch after installation does not update
the installed app.

Expand every family and boundary before creating accounts. The computed counts
must equal `policy.coverage`. A new audience requires the manifest, policy
denominator, checks, and a gate regression in one reviewed change.

### 2. Prepare fixtures idempotently

Use one unique account per scenario. Keep credentials outside Git and evidence.
Persist only opaque account reference, scenario ID, targeting facts, and lifecycle
state in evidence. A protected fixture registry may retain the user ID needed for
execution, but must not copy it into the evidence bundle.

When creating an account through `POST /api/test-user/register`, omit the
`password` field entirely and let qa-tools apply its server-owned default. Build
the request from the documented registration-field allowlist and assert that the
serialized body has no `password` key. The qa-tools API key belongs only in the
`Authorization` header; never copy an API key, token, environment secret, or
caller-supplied credential into the account password field. Treat a required
`password` field as contract drift and fail closed instead of inventing a value.

Before setting device, first-bind, VIP, or paid-history fixtures, verify one login
with the documented default credential from protected local execution config.
Quarantine a failed account as `BLOCKED_CREDENTIAL_MISMATCH`; do not mutate its
fixture or silently replace the expected password.

Require finite, exact readback for every targeting fact. HTTP 2xx alone is not
success. Refuse to overwrite ambiguous paid history.

Use this state machine:

```text
AVAILABLE -> RESERVED -> PAYMENT_STARTED -> CONSUMED
```

- Non-purchase cases may return to `AVAILABLE` after readback.
- Cancelled/failed cases may return only after order, subscription, and VIP all
  prove zero mutation.
- Any confirmed entitlement makes the account permanently `CONSUMED`.
- Unknown payment outcome becomes `BLOCKED_UNKNOWN_OUTCOME`; reconcile before
  any retry.

Use `qatools` for fixture operations and exact readback. Do not fabricate
first-bind or paid-history facts locally.

### 3. Run phases in order

- P0-P4 run in GitLab CI and produce deterministic, run-scoped artifacts.
- P5-P9 run from a connected-device Mac or dedicated device host.
- GitLab may verify an uploaded P5-P9 evidence bundle; it does not execute Apple
  IAP, Google Play Billing, Stripe, or Airwallex payment.
- API order is `evaluate -> asserted paywallId -> cms-content -> asserted matrix`.
- Mock prices are valid only in P3 and must be labeled.
- P4 must retain the complete live CMS response; a repo fixture is not evidence.
- P5 requires physical iOS and Android devices plus strict typed real-price quotes.
- Approved design and the repository policy define which fields belong on each
  surface. Do not invent a card-price requirement when the approved design shows
  cadence only; verify the selected summary, typed quote, and provider sheet.
- Treat provider-owned localized copy separately from commerce identity. Unless
  the frozen policy explicitly requires exact provider-sheet copy, compare the
  provider plan title by semantic product scope and cadence, and emit both
  `exactTitleStringMatchActual` and `semanticTitleMatchActual`. Product/offer
  identity, numeric amount, ISO currency, billing period, recurrence, and
  trial/intro/promotional phases remain exact checks. Semantic equivalence must
  come from an explicit approved localization/cadence mapping; fuzzy similarity,
  broad substring matching, or dropping an unmatched word is not evidence. Do
  not request an App Store Connect, Play Console, CMS, or H5 copy change merely
  to make equivalent localized titles byte-equal.
- Before opening any provider surface, prove that the run can execute its
  read-only post-checkout reconciliation: current authentication, Swagger/query
  contract, and before snapshots for order/subscription/VIP/profile. An expired
  SSO session is a pre-checkout host blocker, not something to discover after
  cancellation or confirmation.
- Resolve the connected physical-device destination from the current toolchain
  at the start of every native run. Never reuse a prior run's human-readable
  device name, model-name selector, or raw device ID. Keep the resolved selector
  only in protected temporary execution state and sanitize it from retained
  logs. A destination mismatch is safely retryable on the same account only
  when machine-readable results prove that no test launched and the checkout
  state was never entered; otherwise reconcile as an unknown outcome.
- Initialize reusable batch infrastructure once: authenticated troubleshooting
  context, fetched Swagger/query config, frozen catalog denominator, build/test
  products, and the protected credential injection path. Reuse it across product
  checks. After each cancellation, execute independent required read-only
  payment-flow, order, subscription, and VIP checks concurrently when supported,
  but do not start the next provider sheet until all required zero-delta results
  are exact. Repeated authentication, speculative rebuilds, serial independent
  reads, and unnecessary per-product export of already-hashed successful result
  bundles are orchestration defects, not UAT coverage.
- For a multi-product cancellation scan on one account, launch once, log in once,
  and navigate to the asserted Paywall once. Iterate every frozen unique
  `productId + offerId` card in the same app process: select, open the provider
  sheet, verify, cancel, handle the policy-required dialog, prove the Paywall is
  reusable, complete the per-product zero-delta checkpoint, and only then select
  the next card. Do not unconditionally terminate/relaunch the app or create one
  isolated XCTest invocation per card. End the app lifecycle after the account's
  full card denominator completes. A classified crash or unrecoverable host/app
  state may relaunch only after the current product has a known cancelled outcome
  and exact zero deltas; an unknown outcome stops the account loop. Keep every
  successful-payment test in its own explicit account and execution path.
- Preserve the provider cancellation outcome and raw platform code separately
  from the app state, dialog, and telemetry. The repository policy owns whether
  deliberate cancellation shows no dialog or the policy-required attributable
  dialog exposure(s); no successful payment or entitlement mutation is invariant.
- Derive each scenario's expected offer policy from the manifest and live CMS.
  For a selected subscription whose CMS offer type is `PROMOTIONAL`, require a
  non-empty requested offer ID, visible H5 discount/original-or-renewal pricing,
  and exact provider resolution of that offer on every policy-required Apple
  IAP, Google Play, Stripe, or other supported channel. A real price alone is not
  proof that the promotion was applied. Keep CMS `requestedOfferId` separate from
  the provider-derived `resolvedOfferId`; an H5 echo of the request is not
  provider evidence. If checkout safely falls back to the regular/base price,
  classify it as `PROMOTIONAL_OFFER_FALLBACK` and fail the promotional scenario.
  For a policy-declared no-offer case, require the offer ID to remain absent and
  never invent one during quote or checkout.
- Human input is limited to final color/brand approval or a declared secure-system
  cancel/confirm checkpoint. Routine navigation and assertions stay automated.

Follow `references/device-payment-runbook.md` exactly for native and payment work.
Never confirm a secure payment sheet, bypass biometrics, or start an unapproved
charge on the user's behalf.

### 4. Generate evidence and run the gate

Create one immutable evidence bundle per run. Every required check includes a
numeric `actual`, artifact IDs, relative artifact paths, and SHA-256 hashes. The
gate recomputes status; it never trusts an evidence `status` field.

Never persist passwords, tokens, JWTs, cookies, authorization headers, receipts,
payment secrets, full transaction identifiers, or raw device IDs. Filtered runs
set `selection.isFullGate=false`, use run-scoped paths, and never overwrite the
canonical certification artifact.

Historical evidence is immutable. A later design clarification, policy update,
or provider-console repair requires a new run-scoped artifact; never rewrite an
old failure into a pass.

Run from the target repository root:

```bash
node testdata/personalized-offer/uat/gate.mjs \
  --profile FULL_UAT_GATE \
  --evidence test-results/personalized-offer-uat/<run-id>/evidence.json
```

Interpret exit codes strictly: `0` emits only the gate's certification; `1` is a
measured UAT failure; `2` means the harness/policy/evidence is invalid.

## Repair Loop

For exit `1` or `2`:

1. Preserve failing evidence and exact SHA.
2. Route the first causal failure with `references/owner-routing.md`.
3. Add the smallest fail-loud Red regression at the earliest deterministic layer.
4. Fix the narrowest owning layer without changing expected measurements.
5. Re-run the failed phase, dependent downstream phases, then the named gate.
6. Stop after three failed repair attempts for the same root cause and report the
   owner, evidence, attempts, and required decision.

Never repair by lowering a threshold, removing cases, marking a requirement N/A,
rewriting evidence, self-approving a visual baseline, guessing pricing phases,
reusing a consumed account, retrying an unknown payment, or merging/deploying/
publishing without authorization.

## Examples

### Bad

```text
Ran one Never scenario with mock prices: 1/1 passed, so Full UAT passed.
```

This is `NON_GATING_PARTIAL`: wrong denominator, no real CMS/native/payment data.

### Good

```text
FULL_UAT_GATE exited 0 for policy v1, exact app/repo SHA, full policy denominator,
and immutable P0-P9 evidence. Accounts with confirmed entitlement are CONSUMED.
```

## Result Report

Lead with exact certification or failure. Include profile, policy version,
environment, run ID, repository/app SHA, every phase numerator/denominator,
artifact paths and hashes, consumed accounts, failure owner, repair attempts, and
what the result does not prove. Never summarize a partial green run as UAT passed.

# Failure owner routing

Read this reference after a gate failure or whenever payment is in scope.

| Phase | Primary owner | Diagnose first | Safe automatic repair boundary |
|---|---|---|---|
| P0 Contract | CONTRACT | frozen schema, manifest expansion, legacy-field scan | contract tests and generators; stop on product contract change |
| P1 Fixture | FIXTURE | qa-tools request and exact backend/PE readback | idempotent fixture orchestration and validation; never invent paid history |
| P2 Staging API | TARGETING | PE context, GrowthBook variation, evaluate, CMS key/matrix | consumer mapping/tests; external experiment edits require authority |
| P3 Mock H5 | H5 | parser, selection state, Bridge payload, visual diff | H5 code and deterministic tests; never self-approve baseline |
| P4 Real CMS H5 | CONTENT | live CMS payload versus contract | H5 parser or CMS consumer; publishing CMS is external state |
| P5 Native Device | HOST_IOS / HOST_ANDROID | banner lifecycle, SDK pin, host context, typed price provider | host/SDK tests; shared SDK or PaymentCore requires review |
| P6-P7 Payment | PAYMENT | channel result, client callback, order/subscription/VIP readback | test harness and owned adapters; never charge or guess phases |
| P8 Post-payment | ENTITLEMENT | order/subscription/VIP/profile projection and PE segment | projection/consumer tests; protect consumed-account evidence |
| P9 Observability | OBSERVABILITY | correlation IDs, event delivery, retries, logs | instrumentation and assertions; never expose payment secrets |

## Environment classification

Classify a failure as `ENVIRONMENT` only when an independent probe proves the
required service, device, sandbox, network, or authentication system unavailable.
The gate remains failed; environment classification never waives evidence.

## Successful-payment readback minimum

Correlate one journey across:

1. client checkout result;
2. channel transaction or test-order reference;
3. business order status, amount, currency, product, offer, and channel;
4. active subscription product and validity;
5. active VIP entitlement and validity;
6. profile `hasVip` and PE segment transition;
7. cold app restart;
8. telemetry correlation and idempotent retry.

For cancellation, require no successful channel confirmation and exact zero delta
in paid order, active subscription, and VIP entitlement. Preserve the provider's
raw cancellation code and verify the app dialog plus telemetry against the target
repository policy. A dialog is a product failure only when its count, copy, state,
or attribution violates that policy.

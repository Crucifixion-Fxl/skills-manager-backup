# Payment channel UAT runbook

Read this reference for P6-P9. It defines how to select and prove Apple IAP,
Google Play, Stripe, and Airwallex journeys. It supplements, but never replaces,
the repository policy or `device-payment-runbook.md`.

## 1. Route and fixture selection

Record the real result of `/vip/available/payType` before opening the Paywall:

| `payType` | iOS route | Android route |
|---:|---|---|
| `0` | `APPLE_IAP` | `GOOGLE_PLAY` |
| `1` | `AIRWALLEX` | `AIRWALLEX` |
| `2` | `STRIPE` | `STRIPE` |

Fail when the client uses a debug hardcode, the route differs from this readback,
or the expected provider surface does not appear. A mock provider response does
not count as a real-price, cancellation, or successful-payment result.

Prepare channel-specific audience fixtures with `addx:qatools`:

- Create new scenario accounts with the register-only
  `POST /api/test-user/register` endpoint, not `/api/quick-setup`, and omit the
  `password` request field so qa-tools owns the default. Keep the qa-tools API
  key only in the `Authorization` header, verify a login before fixture mutation,
  and quarantine any credential mismatch.

- Never has no paid history. Use the `third_party_payment` experiment/allowlist
  to select the intended route, then require `/vip/available/payType` readback.
- Lapsed keeps its last real or mock payment channel. Create the historical VIP
  with `mock-vip-lifecycle` channel `apple`, `google`, `stripe`, or `airwallex`,
  set the exact expired time with `set-vip-endtime`, and require both profile and
  `/vip/available/payType` readback. An experiment allowlist alone cannot
  override historical-channel precedence.
- `mock-vip-lifecycle` is fixture preparation only. Its mock transaction never
  proves a real Apple, Google, Stripe, or Airwallex checkout.

For each policy case, freeze platform, channel, audience, account reference,
product ID, offer ID, currency, expected amount, phase sequence, app/repository
SHA, and sandbox/test environment before checkout.

## 2. Common quantified checks

Every channel case must emit numeric checks with artifact hashes.

Cancellation requires all of:

| Check | Required value |
|---|---:|
| expected provider surface shown | `1` |
| client result classified as cancellation | `1` |
| raw provider cancellation code preserved and attributed | `1` |
| repository cancellation-presentation checks pass | `1` |
| successful paid-order delta | `0` |
| active-subscription delta | `0` |
| active-VIP delta | `0` |
| original audience still matched after readback | `1` |

Successful payment requires all of:

| Check | Required value |
|---|---:|
| expected provider surface shown | `1` |
| selected product/offer/channel/amount/currency match | `1` |
| client success callback correlated | `1` |
| provider transaction/test-order correlated | `1` |
| paid business order correlated | `1` |
| active subscription correlated | `1` |
| active VIP correlated | `1` |
| profile `hasVip=true` | `1` |
| Never/Lapsed acquisition segment removed | `1` |
| entitlement survives cold restart | `1` |
| duplicate entitlement rows after idempotent replay | `0` |
| duplicate business events after idempotent replay | `0` |

One unique account is required per successful-payment case. Move it to
`CONSUMED` as soon as any entitlement is confirmed. Do not retry an unknown
outcome; reconcile provider, order, subscription, VIP, and profile first.

### Promotional offer evidence

Read the scenario manifest and live CMS before quote collection. Do not hard-code
lapse buckets or promotional denominators in this runbook; the repository policy
owns which scenarios expect `PROMOTIONAL` and which explicitly expect no offer.
For the current Lapsed business model, first assert that the frozen policy marks
D0-D7 as no-offer and later lapse buckets as promotional. If that contract is
missing or differs, stop as contract drift instead of hard-coding the buckets in
the runner. The frozen policy remains authoritative for each run.

For every selected subscription whose CMS offer type is `PROMOTIONAL`, require:

1. live CMS provides a non-empty `requestedOfferId`;
2. the H5 surfaces required by the approved design show the discounted amount,
   the regular or renewal comparison amount, promotion duration, and
   post-promotion recurrence; when a card intentionally omits price, require
   these values in the selected summary instead;
3. the native/provider quote returns a provider-derived `resolvedOfferId` that
   equals `requestedOfferId`, or matches the policy's frozen authoritative
   mapping when the provider uses a structurally different identifier;
4. the normalized quote contains discount phase(s) followed by the regular
   recurring phase, with matching product, currency, billing period, recurrence,
   and a discounted amount lower than the regular amount;
5. Continue submits the exact selected product and requested offer identity;
6. the provider payment sheet shows the same promotion, discounted amount,
   regular/original comparison amount, currency, and recurrence as H5; and
7. fallback-to-regular count is zero for the promotional UAT denominator.

Keep `requestedOfferId` and `resolvedOfferId` as separate evidence fields. The
first comes from CMS; the second must come from the provider catalog/checkout
selection. Copying the request into the response, receiving any real price, or
opening a provider sheet does not prove promotional resolution.

Provider identity is channel-specific:

- Apple IAP: product plus the StoreKit-resolved promotional offer identifier;
- Google Play: product, base plan, and resolved offer ID or an opaque offer-token
  hash;
- Stripe: price plus the coupon/promotion identity actually applied to Checkout,
  Subscription, or invoice data;
- Airwallex or another supported channel: the provider-native discount identity
  required by the repository policy.

If the product deliberately allows checkout to fall back to its regular/base
price, preserve that operational behavior but emit
`PROMOTIONAL_OFFER_FALLBACK`. The checkout may remain usable; the promotional
scenario still fails. For a policy-declared no-offer scenario, require CMS,
quote, Continue payload, and provider selection to keep offer identity absent.

## 3. Native IAP: Apple and Google

Native IAP is two independently required channels.

### Apple IAP Sandbox

Preconditions:

- physical iPhone/iPad, staging build, correct bundle ID and StoreKit product;
- device has an Apple Sandbox tester available for the system purchase sheet;
- `/vip/available/payType` returns `0` and the client opens the Apple IAP sheet;
- App Store product identity, numeric price, ISO currency, subscription period,
  introductory/trial eligibility, and offer identifier match the strict typed
  quote shown by H5.

The Apple sheet owns its localized product title and formatting. Unless the
frozen repository policy explicitly makes that provider copy exact, do not
require byte-for-byte equality between an H5/catalog title and Apple metadata.
Instead:

1. preserve the raw observed Apple title in sanitized evidence;
2. require semantic product scope and cadence to match (for example,
   `Annual` and `Yearly` both mean one year);
3. emit exact-string and semantic-title results separately;
4. compare price as numeric amount plus ISO currency and billing period, so
   `$99.99/year` and localized `US$99.99/年` can match without weakening the
   amount/currency/period checks; and
5. normalize Unicode punctuation and approved localized trial/intro wording for
   OCR, while keeping product/offer identity and price phases exact.

The semantic mapping must be an explicit allowlist from approved localization or
the frozen contract. Do not use general fuzzy matching, edit distance, or a broad
substring test that could accept a different device tier or cadence.

A semantically equivalent localized title is not a reason to request an App
Store Connect metadata change. A wrong product scope, cadence, amount, currency,
offer, or phase remains a hard failure.

Automate app navigation, card selection, checkout initiation, callback handling,
backend polling, screenshots, and evidence. Pause only for the system-required
double-click/biometric/passcode confirmation or cancellation. Never automate or
bypass that secure action.

For success, correlate the StoreKit transaction with the business order,
subscription, VIP, server receipt/transaction verification, and applicable App
Store server notification. Store only an opaque/hash-derived transaction
reference; never persist the receipt or signed transaction. Cold-restart and
verify entitlement, then verify Restore does not create a duplicate entitlement.

### Google Play Billing test purchase

Read `android-google-play-uat.md` before this case. It defines sideloaded-build
preflight, composite product identity, account-specific offer eligibility,
accelerated test renewal display, design-aware All Plans assertions, cancellation
attribution, and immutable evidence.

Preconditions:

- physical Android device with an approved license-tester account;
- install the exact signed staging/test-track build whose application ID and
  Play product/base-plan/offer configuration match the case;
- `/vip/available/payType` returns `0` and the client opens the Google Play
  purchase sheet;
- product, base plan, offer token semantics, localized price, billing period,
  trial/intro phases, and eligibility match the strict typed quote shown by H5.

Automate everything except any Play-controlled secure confirmation that the
device tooling cannot safely control. For success, correlate the purchase with
the business order, acknowledged subscription, VIP, backend purchase-token
verification, and applicable real-time developer notification. Persist only an
opaque/hash-derived purchase reference, never the raw purchase token. Cold-start
and restore/query purchases; require no duplicate entitlement.

Apple success does not prove Google success, and vice versa.

## 4. Stripe Test Mode

Preconditions:

- `/vip/available/payType` returns `2` and the client opens the supported Stripe
  Test Mode surface;
- Customer, price, product, currency, and test payment method belong to the same
  Stripe test account used by the backend;
- H5 strict typed phases match the provider price. When an offer/coupon is used,
  verify the discounted amount, duration, recurrence, and post-discount amount
  against the Stripe test invoice/checkout data; the mere existence of a coupon
  is not evidence that it was applied.

Run cancellation before confirmation and require the common zero-delta checks.
For success, pause only at a required 3DS/provider confirmation checkpoint. Then
correlate PaymentIntent/Checkout/Subscription state, webhook delivery, business
order, subscription, VIP, and profile. Use opaque/hash-derived provider
references and do not persist client secrets, payment-method data, or complete
webhook payloads.

If Stripe is supported on both iOS and Android, execute every platform pair in
the policy; one host adapter cannot certify the other.

## 5. Airwallex demo/sandbox

Preconditions:

- `/vip/available/payType` returns `1` and the client opens the supported
  Airwallex demo/sandbox surface;
- merchant/account, product, amount, currency, and test payment method belong to
  the same Airwallex non-production environment used by the backend;
- H5 strict typed phases and selected offer match the PaymentIntent/order data.

Run cancellation before provider confirmation and require the common zero-delta
checks. For success, pause only for a required provider/3DS secure confirmation.
Then correlate the Airwallex PaymentIntent/payment result and webhook with the
business order, subscription, VIP, and profile. Persist only opaque/hash-derived
references; never store client secrets, card data, redirect tokens, or complete
webhook payloads.

If Airwallex is supported on both iOS and Android, execute every platform pair in
the policy. A successful Stripe journey does not substitute for Airwallex.

## 6. Stop conditions

Stop without retrying checkout when:

- route readback and visible provider disagree;
- product, offer, amount, currency, or phase differs at the secure checkpoint;
- the provider environment might be production;
- callback outcome is missing or conflicts with provider/backend state;
- a previously successful or unknown-outcome account would be reused;
- PaymentCore cannot supply strict typed phases without guessing.

Classify the failure with `owner-routing.md`, keep the gate failed, and preserve
the sanitized before/after evidence.

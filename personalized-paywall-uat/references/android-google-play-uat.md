# Android Google Play UAT lessons

Read this reference before Android Google Play P5-P9 work. It captures failure
modes that are easy to misclassify when a remote H5, an Android host adapter,
Google Play Billing, live CMS metadata, and an account-specific offer catalog are
tested together.

## 1. Freeze three independent identities

Record these independently:

1. current Android repository and host/SDK SHAs;
2. installed APK package, version name/code, build fingerprint or commit, APK
   SHA-256, signing identity, and installer source;
3. remote H5 URL plus a deployment/content identity and live CMS response hash.

Fetching or fast-forwarding the checkout after installation does not update the
APK. Deploying H5 does not update the APK either. Never report the current branch
HEAD as the tested app SHA unless the installed artifact proves that identity.

## 2. Prove billing with one non-consumptive pilot

Before creating or leasing a large account matrix, use one fresh Never account to
prove all of the following on a physical device:

- `/vip/available/payType=0` and the Android host interprets it as Google Play;
- the expected package/application ID, signing, Play listing, products, tester,
  and environment can open a real `com.android.vending` test purchase sheet;
- strict typed prices come from Google Play, not H5 mocks or placeholders;
- cancellation returns to an operable Paywall and produces zero paid-order,
  active-subscription, and active-VIP delta.

`installerPackageName=null` or an ADB-installed APK is not, by itself, proof that
Billing cannot work. Conversely, a logged-in license tester is not proof that it
will work. The decisive no-charge preflight is the real Play sheet plus the exact
typed quote for the frozen package and account. Do not repeat a known-bad install
path across the matrix.

A device may contain several Google accounts. Do not remove unrelated accounts
as the default repair. Verify the account shown by the Play sheet is the approved
license tester using an opaque reference. If Play selects an ambiguous or wrong
identity, stop and repair account selection before checkout.

## 3. Use the complete Google product identity

Never diagnose a price or trial from `basePlanId` alone. Use this tuple:

```text
Google productId + basePlanId + offerId (or an offerToken hash)
```

The same base-plan string can exist under more than one Google subscription
product. A host field named `subscriptionGroupId` may be passed to Billing as the
Google `productId`; verify the outgoing request and returned ProductDetails rather
than trusting the field name. Join live CMS runtime metadata and typed quotes with
the same complete identity.

For every unique purchasable tuple, retain a sanitized summary of:

- currency code/symbol and formatted/current/original price;
- phase type, amountMicros, billing period, recurrence, and cycle count;
- base plan and offer identity;
- selected-summary text and visible Play-sheet values.

Never retain raw purchase tokens or complete provider payloads. ProductDetails
logs may also expose account identifiers and long offer tokens; sanitize them and
store only the fields or hashes needed by the gate.

## 4. Separate CMS intent from current-account eligibility

`freeTrialDays > 0` in live CMS expresses product intent; it does not manufacture
a Google trial. The current tester's ProductDetails response is the price truth for
that checkout. A missing zero-price phase can mean:

- the offer is absent, inactive, unavailable in the tester's region, or attached
  to a different product/base plan;
- the offer eligibility rules exclude this tester;
- the tester's purchase history makes the offer ineligible;
- the host queried the wrong complete product identity.

Therefore, do not immediately blame H5 or assert “not configured.” Compare the
live runtime product, outgoing tuple, current-account typed quote, and Play sheet.
If CMS expects a trial but Google returns only a recurring phase, classify a
Play configuration/eligibility mismatch and ask the Play owner to inspect the
exact tuple. Re-query after the external repair.

For a CMS `PROMOTIONAL` subscription, distinguish the requested offer from the
offer Google actually resolves. Record `requestedOfferId` from live CMS and
derive `resolvedOfferId` from the selected ProductDetails offer; never populate
the latter by echoing the request. Require the complete Google tuple to match,
the discounted phase to precede the recurring phase, and both H5 and the Play
sheet to show the same discounted and regular/renewal prices. If the host falls
back to a regular base plan, classify `PROMOTIONAL_OFFER_FALLBACK` and fail that
promotional UAT case even when checkout remains possible.

## 5. Interpret the Google test sheet correctly

Google test subscriptions can display an accelerated renewal cadence such as
`5 min` or `30 min`. That is sandbox clock behavior, not a replacement for the
catalog billing period. Preserve both facts:

- the typed quote must still report the configured `P1M`/`P1Y` period and phases;
- the sheet may separately show the accelerated test renewal interval.

Fail when product, offer, amount, currency, or phase disagrees. Do not fail only
because the test renewal clock is accelerated.

## 6. Assert All Plans against approved design

Do not infer that every card must print a full price. If the approved All Plans
design intentionally shows only Monthly and Yearly, that is not a missing-price
defect. Still test every unique tuple and require:

- selected/check state and device-tier tab are correct;
- the bottom/selected price summary matches the typed quote;
- Continue sends the selected complete identity;
- the Play sheet matches amount, currency, period, and eligible phases;
- cancellation and backend zero-delta evidence satisfy the policy.

## 7. Keep raw cancellation semantics and product presentation separate

Google Play Billing uses raw response code `1` for user cancellation. Other
platforms can use different raw codes, such as iOS code `2`; never normalize raw
provider codes across platforms. Record separately:

- provider outcome and raw code;
- app state/result mapping;
- dialog count/copy;
- telemetry provider and raw-code attribution;
- Paywall operability;
- backend zero deltas.

The target repository policy decides whether cancellation is silent or displays
the policy-required attributable dialog exposure(s). The Skill must not hard-code
a universal UI choice. No successful channel confirmation and zero paid-order,
subscription, or VIP mutation remain invariant.

## 8. Preserve failures and retest the narrowest valid scope

Do not rewrite a prior report after design clarification, a policy update, or a
Play Console repair. Keep it as historical evidence and create a new run.

An account blocked only by one external product tuple may remain `AVAILABLE` after
zero-mutation reconciliation. After the owner repairs that tuple, retest only the
affected quote/sheet/cancel path when the installed APK, remote H5, policy,
fixture, and all other frozen identities remain unchanged. Until that rerun
passes, the selection remains blocked or `NON_GATING_PARTIAL`; it is not a named
certification.

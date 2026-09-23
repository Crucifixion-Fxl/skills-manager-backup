# Physical-device and payment UAT runbook

Use this runbook for P5-P9. GitLab CI verifies policy and evidence; it does not
execute Apple IAP, Google Play Billing, Stripe, or Airwallex payment. Before
P6-P9, read `payment-channel-runbook.md` and select the exact platform-channel
case from the repository policy.

## 1. Execution ownership

| Role | Responsibility |
|---|---|
| GitLab CI | Execute P0-P4, preserve deterministic reports, and test the gate. |
| Device automation | Install/launch exact build, navigate, inspect banner/H5/cards/prices, start checkout, resume, query backends, and produce evidence. |
| Human operator | Keep device unlocked and perform only secure-system cancel/confirm or final color/brand approval. |
| Gate | Recompute checks and issue the named certification. |

Do not call this manual UAT. It is automated UAT with explicit human checkpoints.
The human never transcribes product IDs, prices, database values, or test results.

## 2. Preconditions

Fail before account mutation unless all applicable checks pass:

- Exact repo/SDK/app SHA, environment, policy version, and manifest hash recorded.
- Record the installed app artifact hash/build fingerprint separately from the
  current checkout and remote branch. For remote H5, also freeze the deployed URL
  and a version/content identity. A fetch after installation changes neither.
- Physical device is connected, unlocked, trusted, and visible to the toolchain.
- Record model, OS, flavor, locale, region, viewport, network, and an opaque or
  hashed device reference. Never store UDID, IDFA, or IDFV.
- Payment identity is sandbox/test; never use a production payment identity.
- A unique fixture account is reserved for the case.
- Before snapshots exist for order, subscription, VIP, profile, and evaluate.
- The exact read-only backend reconciliation calls have been authenticated and
  smoke-tested before checkout. Resolve expired SSO or query-contract drift now;
  do not create an avoidable unknown outcome after the provider sheet returns.
- Batch credentials are available through one protected injection source. Once
  that source is verified, do not repeatedly ask the operator for the same test
  credential between accounts; continue to keep it out of commands and evidence.
- Disk has room for screenshots, video, logs, and test bundles.
- No unrelated controller is driving the device.

Never persist sandbox passwords, OTPs, app passwords, tokens, receipts, payment
secrets, or complete provider payloads.

## 3. iOS device-host readiness and deterministic UI execution

Complete this section before the first iOS checkout attempt. Host readiness is a
separate gate from product UAT; a slow or broken host must not consume fixture
accounts or be reported as a product failure.

### 3.1 Run the fast preflight before building

Within the first five minutes:

1. Freeze the repository path, branch, repository/app/SDK SHA, Xcode version,
   scheme, configuration, bundle ID, and intended test name. Fetch the intended
   remote branch and record ahead/behind state; do not silently test an outdated
   checkout or overwrite unrelated dirty work. Keep the installed app artifact
   identity separate from the checkout identity. Explicitly exclude every
   successful-payment test when the requested run is cancellation-only.
   Expand the live CMS plus typed-quote catalog into the expected unique
   product/offer denominator and report it before device execution. One scenario
   account can legitimately require several independent provider-sheet checks;
   never describe that work as a single checkout.
2. Discover the physical device with `xcrun xctrace list devices` or
   `xcrun xcdevice list`. An older iOS device missing from `devicectl` is not
   proof that it is disconnected. Resolve exactly one currently connected
   physical destination from fresh tool output; never copy the prior run's
   human-readable device name or assume that the hardware model is its Xcode
   destination name. Keep the raw selector in protected temporary state, use a
   short destination-resolution timeout, and redact it from retained logs.
   Record only model, OS, and an opaque device reference in evidence.
3. Check free disk space before `xcodebuild`. Budget for the existing
   `DerivedData`, module cache, one incremental rebuild, result bundles, video,
   and screenshots. Fail early if the budget does not fit.
4. Inspect application and UI-test-runner signing independently. Treat the
   checked-in Xcode signing configuration as the authority for the first build.
   The host app may already be installable while the runner's development
   certificate or provisioning profile is invalid for the device. Do not change
   the app signing, Release signing, team, identity, or profile merely because a
   cached product from another device cannot install.
5. Confirm the target bundle's installed/session state and whether first-launch
   onboarding will appear. Installing over an existing app can preserve its
   logged-in data.
6. Smoke-test staging reachability from the host. If the frozen app is already
   installed, also use one harmless in-app request on the device; otherwise make
   that check the first milestone after installation and before credential login.
   Record VPN/proxy state and handle the iOS wireless-data permission prompt if
   it appears. A login timeout is not evidence that the IPA is bad; classify
   DNS/VPN/network permission/backend reachability before any uninstall or rebuild.
7. Check for an active `xcodebuild`/Swift build service already using the same
   DerivedData. Use one build coordinator, wait for or attach to the known build,
   and never launch a competing speculative build against the same database.
8. Configure the dedicated test device to stay awake using device settings or an
   already approved runtime-opt-in test hook. Do not require disabling the device
   passcode and do not add an always-on idle-timer change to normal app startup.

Do not recursively clear broad Xcode or user directories to recover space.
List exact cache sizes first. If cleanup is necessary, remove only validated,
offline simulator/device-support caches; keep support files for the connected
OS and report what was removed and whether Xcode can restore it.

### 3.2 Use project-default signing and validate each signed layer on device changes

An `.app`, UI-test Runner, and `.xctestrun` are separate build inputs. Development
products can embed profiles selected for a previous phone, while an approved IPA
may already include the current phone or use a distribution path that is not
device-specific. Validate the app and Runner independently instead of assuming
that either both are reusable or both must be rebuilt.

If the exact frozen IPA is already installed and launches on the current phone,
record its build/hash and keep it installed; do not uninstall or rebuild it as a
signing probe. That proves only the app layer. If there is no verified Runner and
`.xctestrun` for the current source/signing/device tuple, use the repository's
documented Xcode entry point and checked-in signing settings to build those test
products for the current destination. Do not run a separate full `xcodebuild
build` first:

```bash
xcodebuild build-for-testing \
  -workspace <workspace> \
  -scheme <scheme> \
  -configuration <documented-configuration> \
  -destination "platform=iOS,id=$DEVICE_ID"
```

On this first attempt, do not pass `DEVELOPMENT_TEAM=...`,
`CODE_SIGN_STYLE=...`, `CODE_SIGN_IDENTITY=...`, or
`PROVISIONING_PROFILE_SPECIFIER=...`. Also omit `-allowProvisioningUpdates` and
`-allowProvisioningDeviceRegistration` unless the repository's documented
default workflow explicitly requires them or Xcode produces a causal diagnostic
that the signing owner approves. Do not toggle Xcode's signing UI or edit the
project file to make an automation host work.

After a successful build, require the applicable newly produced app/extension,
Runner, and `.xctestrun` timestamps/hashes and successful code-sign verification.
That is positive proof that default signing works for the current device; stop
signing investigation and advance to install/test. Never reuse an unverified
prior-device Runner or stale `.xctestrun` merely to save build time.

If the default build fails, classify the exact failing layer before any retry:

| Observation | Classification and next action |
|---|---|
| App or extension profile explicitly excludes the current device | Device/profile registration blocker; route to the signing owner. Do not switch team or profile. |
| App builds/installs but Runner fails signing or installation | UI-test Runner signing blocker; inspect that target only. Do not rewrite working app signing. |
| App and Runner build, but automation launch fails | XCTest injection, Runner launch, app startup, or device-host blocker; signing is not established as the cause. |
| Default build succeeds for the current destination | Signing preflight passed. Continue; do not run more signing probes. |

Uninstalling the app does not repair signing. Use uninstall only for an approved,
deterministic account/session reset, never as a provisioning experiment. Likewise,
do not clear all DerivedData, repeatedly reinstall, alternate device tools, or
cycle signing settings without a new causal error. Allow at most one targeted
retry after the diagnosed prerequisite changes.

### 3.3 Build once, then reuse the current-device test products

Use `build-for-testing` once per frozen source/signing/device tuple. Subsequent
scenario attempts on that same device use
`test-without-building` against the same frozen products unless application or
test-runner source, signing inputs, or the connected device changed. Do not
repeatedly trigger a full Pods/Flutter/native rebuild for account or environment
changes.

When only UI-test source changes, perform one incremental `build-for-testing`;
do not precede it with a standalone app build. When no source/signing/device input
changed, a login, VPN, targeting, CMS, or backend-auth failure must not trigger a
rebuild.

An `.xctestrun` can contain paths relative to `__TESTROOT__`. Keep a run-scoped
copy beside the original file under `Build/Products`, or rewrite every dependent
path to an absolute validated product path. Copying it unchanged to `/tmp` can
produce `Missing test product` even when the runner was built successfully.

Redirect verbose `xcodebuild` output to a protected temporary log. Compiler
warnings are diagnostic noise unless they accompany the actual failure. Redact
credentials and raw device identifiers before retaining a log, then delete the
temporary raw file.

### 3.4 Inject UI-test configuration without persisting secrets

Shell variables on the `xcodebuild` process are not sufficient proof that the
XCTest runner received them. Put non-secret values and securely read credentials
in the UI test's `EnvironmentVariables` using a run-scoped `.xctestrun` or an
equivalent test-plan mechanism, then verify the runner's sanitized configuration
attachment.

Treat structured environment values as data, not as shell-like plist commands.
For JSON that contains spaces, localized text, pipes, or punctuation, use
`plutil -insert/-replace <key-path> -string <value>` (or an API with equivalent
argument boundaries), then extract the stored value and parse it before any
device launch. Do not interpolate such JSON into a `PlistBuddy` command string:
the command parser can silently corrupt the value and make XCTest fail with an
`invalid-json-contract` before the app starts.

- Never write a credential into a shared scheme, repository file, command line,
  result attachment, or retained build log.
- A temporary credential/config file must use owner-only permissions, live
  outside evidence, and be deleted by a trap on success or failure.
- Read a documented staging batch credential once from the protected source and
  reuse that injection path for the batch. Operator repetition is not a security
  control; evidence redaction and bounded secret lifetime are.
- Missing configuration must make the test fail or explicitly skip; a skip is
  an invalid UAT attempt, never a pass.

### 3.4.1 Bound Xcode failure-diagnostic collection

A UI test may finish quickly while `xcodebuild` remains busy collecting a device
sysdiagnose. On some hosts `devicectl diagnose` can stop behind a child
`sudo -- true` until its long timeout, making a sub-second harness failure look
like a several-minute test. If the test/runner logs already prove that no app or
checkout action began, inspect the process tree and classify this separately as
host diagnostic tail time.

Keep the default diagnostics for the first attempt. If this exact stopped-sudo
signature is reproduced and the harness already retains bounded screenshots,
hierarchies, sanitized standard output, and `.xcresult` test details, retry with
`xcodebuild -collect-test-diagnostics never`. This disables the hanging verbose
sysdiagnose only; it must not suppress the UAT-owned evidence or change any
payment boundary. Stream output through an unbuffered redaction stage so the
orchestrator can distinguish active test progress from diagnostic tail time
without retaining credentials or raw device identifiers.

### 3.5 Treat `.xcresult` as the authority

`xcodebuild` exit code `0` and the text `Testing started` do not prove that a
test ran. Immediately inspect both views:

```bash
xcrun xcresulttool get test-results summary --path <run.xcresult>
xcrun xcresulttool get test-results tests --path <run.xcresult>
```

Require the exact expected total and passed counts, `failedTests=0`,
`skippedTests=0`, and `result=Passed`. Zero tests, any skip, an unknown result,
or a count mismatch is a host/harness failure. Do not advance the account or
claim a green scenario.

Export failure attachments before repair. Xcode-generated summaries, attachment
manifests, screenshots, and hierarchies may contain raw device IDs, device names,
account text, or notification previews. Sanitize them before copying into the
evidence bundle; a hash does not make sensitive content safe.

### 3.6 Make each account run deterministic

For a fixture requiring credential login, refuse an unrelated existing session.
If the app is dedicated to this UAT and local data may be cleared, uninstall the
exact bundle, verify it is absent, and let Xcode reinstall the frozen build. For
older iOS devices not managed by `devicectl`, use a scoped device tool such as
`ideviceinstaller` with the resolved device and exact bundle ID. Never erase the
device or clear Apple Sandbox purchase history.

Automate safe first-launch surfaces before login: privacy consent, notification
permission, onboarding `Skip`, expired-session alerts, and OS password-save
prompts. A login element can exist behind an onboarding overlay while remaining
non-hittable; wait for the overlay to close and require the stable accessibility
identifier to be hittable before tapping.

Run a no-checkout pilot first:

```text
fresh app state -> injected login -> home banner -> expected Paywall
  -> all typed prices loaded -> every card selectable -> attachments verified
```

Only after this pilot has a strict `.xcresult` pass may the runner enable the
cancellation test that opens a provider sheet. Keep successful-payment tests in
a separate explicit allowlist so a cancellation batch cannot select them by
name or wildcard.

For an H5 `All plans` catalog, a tap acknowledgement or locator match is not
proof that the requested tab became active. Require all of: the target tab's
selected state, loading completion, the exact expected card count, and at least
one target-specific product/price token from the typed quote. Retry the same
safe tab tap within a small bound; never continue checkout with cards retained
from the previously selected tab.

### 3.7 Time-box repairs and report real progress

Classify a failed attempt before retrying: device visibility, signing,
disk/build, runner configuration, app state, or product behavior. Repair the
narrowest layer and reuse prior products when valid. Report build completion,
test execution, and `.xcresult` validation as three separate milestones; never
describe a successful compile as a successful scenario.

For an already provisioned host and incremental build, use a ten-minute wall-clock
limit from the start of one automation attempt to either test execution or a
classified stop. If ten minutes elapse without reaching the next declared
milestone, interrupt once and report elapsed time, last successful milestone,
active process, and the exact blocker. Do not spend the next ten minutes
uninstalling, rebuilding, or changing signing speculatively. A known clean build
may use a separately declared build budget, but its time must not be hidden inside
the scenario's automation duration.

Record start/end/duration separately for preflight, build, no-checkout pilot,
each provider-sheet check, and backend reconciliation. Never aggregate repeated
failed attempts into one long silent interval; a 40-minute total must be
explainable as named milestones plus classified retries.

### 3.8 Reuse one batch context without weakening per-product safety

Batch optimization is part of harness correctness. It must remove repeated host
work, never reduce product/offer denominators, backend readback, or fail-closed
payment boundaries.

At batch start, create one protected execution context and reuse it:

1. Resolve and smoke-test the current physical destination once. Bound the first
   destination lookup so a stale selector does not consume the default Xcode
   wait. If machine-readable output proves that no test launched and no
   `CHECKOUT_REQUESTED` transition occurred, classify the failure as
   `HOST_DESTINATION_MISMATCH`, return to `PRECHECK`, and allow the same account
   to retry. If a test launched or checkout state is ambiguous, enter
   `BLOCKED_UNKNOWN_OUTCOME` instead.
2. Acquire the troubleshooting token once, validate it with the current-user
   endpoint, fetch the current Swagger and DB-query config once, and reuse that
   authenticated context. Do not invoke the token helper once per product when
   the batch token remains valid. Refresh only after one observed `401`, then
   retry the failed read exactly once.
3. Freeze one live CMS plus typed-quote product/offer matrix and one verified
   build/Runner/`.xctestrun` tuple. Use one owner-only credential injection copy
   for the batch and delete it on every exit path. Account, product, or backend
   state changes do not justify rebuilding or reinstalling.
4. Run one no-checkout pilot. For one account's multi-product provider scan,
   launch the app once, log in once, navigate to the asserted Paywall once, and
   keep that app/session alive while iterating the frozen unique
   `productId + offerId` denominator. Each iteration is an explicit checkpoint:
   select one card, open and verify the provider sheet, cancel, observe and
   dismiss the exact policy-required dialog if present, and prove the same
   Paywall remains usable. Do not model cards as independent XCTest methods and
   do not put an unconditional `terminate()` or relaunch between cards. Terminate
   only after the account denominator completes. A relaunch is allowed only for
   a classified crash or unrecoverable app/host state after the current product
   has a known cancelled outcome and exact zero backend deltas. An unknown
   outcome stops the account loop. Successful-payment tests remain on a separate
   explicit account allowlist and never share this cancellation loop.
5. After every cancellation, publish a sanitized per-product checkpoint from the
   still-running test and pause before the next card. The orchestrator must
   require the cancellation return/platform code, dialog behavior, reusable
   Paywall, and the policy-required independent read-only payment-flow, order,
   subscription, and VIP queries. Run independent queries concurrently with the
   already validated authentication; preserve a separate sanitized response and
   duration for each query. Resume the loop only when every required delta is
   zero and the fingerprints match. Do not wait for a finalized `.xcresult` to
   perform this safety handshake: use a runner-supported checkpoint stream or
   companion control channel. If the harness lacks that capability, fix the
   harness instead of falling back to per-card app termination. After the entire
   account loop finishes, parse the finalized `.xcresult` and require the exact
   test count plus one complete checkpoint for every frozen card.
6. Export failure attachments immediately. Successful `.xcresult` attachments
   may be exported and sanitized in one batch at the end, after their summaries
   and hashes have been recorded. Delete raw logs, raw device selectors, and
   credential-bearing test configuration after evidence packaging.

Report at least these mutually exclusive timing buckets: destination/preflight,
build, device UI, provider-sheet checks, backend reconciliation, and evidence
packaging. Report both total wall time and device UI time. A long total with a
much shorter device UI duration is a harness-orchestration signal, not evidence
that the phone is slow. No optimization may defer an unknown outcome, skip an
account-state transition, combine evidence across products, or start the next
checkout before the prior product is reconciled.

## 4. Persisted state machine

```text
PRECHECK
  -> APP_AUTOMATION
  -> CHECKOUT_REQUESTED
  -> AWAITING_HUMAN_CANCEL | AWAITING_HUMAN_CONFIRMATION
  -> CHECKOUT_RETURNED
  -> BACKEND_RECONCILIATION
  -> AVAILABLE | CONSUMED | BLOCKED_UNKNOWN_OUTCOME
  -> GATE_EVALUATION
```

Persist transitions before and after a human checkpoint. A restarted runner must
resume from state and readbacks, never blindly repeat checkout.

At a checkpoint show only run ID, platform/channel, opaque account reference,
case, requested `CANCEL` or `CONFIRM_SANDBOX_PAYMENT`, and a visible
`SANDBOX/TEST — NO PRODUCTION CHARGE` marker with expected product, amount, and
currency. State the expected system sheet and expiry. Never request credentials.

Confirmation is single-use for the shown run/product/amount/currency. If visible
values differ, cancel and block. Prior confirmation never authorizes a retry.

## 5. Non-purchase native flow

Fully automate:

1. Install/select exact app build and launch.
2. Log in with securely injected credentials without printing them.
3. Assert `vh_home_banner` visibility and capture targeting evidence.
4. Tap banner and assert expected paywall ID and H5 root.
5. Wait for strict typed real prices and record sanitized product/offer/phase data.
6. Select every product card; assert selection, copy, CTA, payload, and every
   price field that the approved design/policy assigns to that surface. If a card
   intentionally shows only Monthly/Yearly, verify price in the selected summary,
   strict typed quote, and provider sheet instead of inventing card copy.
   On `All plans`, prove the active tab and target-specific content after every
   tab change; element existence alone is insufficient.
7. Capture screenshots under frozen design/device conditions.
8. Cold-restart and assert stable banner/paywall behavior.

An unrelated system dialog is a precondition/environment failure, not a reason to
delegate routine app navigation to the human.

## 6. Cancellation flow

Cover this non-reducible baseline before any additional policy cases:

| Channel | Required surface | Required result |
|---|---|---|
| `APPLE_IAP` | Apple IAP Sandbox sheet on physical iOS | cancelled, zero backend mutation |
| `GOOGLE_PLAY` | Google Play Billing test sheet on physical Android | cancelled, zero backend mutation |
| `STRIPE` | Stripe Test Mode checkout in the supported product flow | cancelled, zero backend mutation |
| `AIRWALLEX` | Airwallex demo/sandbox checkout in the supported product flow | cancelled, zero backend mutation |

The logical P6 baseline denominator is 4 and must finish 4/4. The policy must
add every enabled platform-channel pair, so its denominator may be higher. A
policy may add channels,
but cannot replace a baseline channel. Missing, unavailable, `N/A`, `SKIPPED`, or
`NOT_RUN` is a gate failure, not a reduced denominator.

1. Capture before snapshots for order/subscription/VIP/profile.
2. Assert selected product and outgoing purchase payload.
3. Start checkout and assert the expected store/provider surface.
4. Cancel automatically when reliably controllable. Otherwise enter
   `AWAITING_HUMAN_CANCEL` for one cancel/close action.
5. Preserve the provider cancellation outcome and raw platform code, then assert
   the app/dialog/telemetry behavior required by the repository policy. A policy
   may require no dialog or policy-required attributable cancellation dialog
   exposure(s); do not replace the raw provider code with a cross-platform
   synthetic code. Paywall must remain usable.
6. Assert successful/active row deltas for order, subscription, and VIP are all 0.
7. Re-run profile/evaluate and assert the original audience remains.
8. Return account to `AVAILABLE` only after all zero-delta checks pass; otherwise
   use `BLOCKED_UNKNOWN_OUTCOME`.

## 7. Successful-payment flow

Run at least one successful test payment for each of `APPLE_IAP`, `GOOGLE_PLAY`,
`STRIPE`, and `AIRWALLEX`. The logical P7 baseline denominator is 4 and must
finish 4/4; execute every additional platform-channel pair in the policy. Use
one unique account per successful-payment case.

1. Capture before snapshots; set `PAYMENT_STARTED` immediately before checkout.
2. Assert product, offer, price, currency, phase, and channel.
3. Enter `AWAITING_HUMAN_CONFIRMATION` at the secure-system boundary.
4. Ask the human only for the required sandbox double-click, biometric, passcode,
   or provider confirmation. Never bypass the secure action.
5. Resume automatically after app activation or callback.
6. Correlate client result, channel transaction/test-order, and business order.
7. Poll with policy-bounded backoff for paid order, active subscription, active
   VIP, and `hasVip=true`.
8. Assert product/offer/channel/amount/currency/validity consistency across layers.
9. Re-run PE profile/evaluate; Never/Lapsed acquisition targeting must disappear.
10. Cold-restart and assert entitlement remains.
11. Exercise one idempotent callback/read retry and prove no duplicate entitlement
    or business event.
12. Mark account `CONSUMED` as soon as any successful entitlement is confirmed,
    even if later UI or telemetry checks fail.

For P8, require complete entitlement readback for all four successful baseline
transactions: 4/4 correlated business orders, 4/4 active subscriptions, 4/4
active VIP entitlements, and 4/4 expected profile/segment migrations. Apply the
same 100% rule to additional policy cases. One channel or platform's success
never substitutes for another.

Starting another payment after a product failure requires a new account and new
explicit human confirmation.

## 8. Unknown-outcome reconciliation

Never retry checkout after timeout, disconnect, crash, or lost callback following
confirmation.

1. Enter `BLOCKED_UNKNOWN_OUTCOME`.
2. Query channel status when available, then order, subscription, VIP, profile,
   and callbacks.
3. Any successful transaction/entitlement means `CONSUMED`.
4. Only unanimous proof of no transaction and no entitlement permits `AVAILABLE`.
5. Conflicting or unavailable sources remain blocked for payment-owner decision.

Unknown outcome is not cancellation or failure.

## 9. Evidence bundle

Use a run-scoped bundle:

```text
test-results/personalized-offer-uat/<run-id>/
  evidence.json
  device.json
  journey.json
  backend-before.json
  backend-after.json
  screenshots/
  video/
  sanitized-logs/
```

Every referenced file has a gate-verified SHA-256. Use opaque account/transaction
references. Inspect screenshots/video for notification previews, account identity,
payment identity, or other personal data; crop, mask, or keep unsafe artifacts
local. Never commit raw evidence or credentials.

Run `NATIVE_GATE` or `FULL_UAT_GATE` on the device host, then upload the sanitized
bundle or protected artifact URL and hashes to the MR/release record. GitLab may
download and verify the bundle.

## 10. Release decision

| Evidence | Allowed statement | Meaning |
|---|---|---|
| P0-P4 | `UI_READY` | No native-price or payment claim. |
| P0-P6 | `NATIVE_READY` | Native rendering, real price, and cancellation passed. |
| P0-P9 | `FULL_UAT_PASSED` | Full sandbox payment and entitlement journey passed. |

The release record must reference both pipeline SHA and exact device run/bundle.
Missing, stale, partial, or mismatched evidence keeps release blocked.

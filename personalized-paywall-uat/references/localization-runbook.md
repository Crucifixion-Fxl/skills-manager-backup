# Paywall localization acceptance

## Coverage and authority

Use the repository policy/manifest/gate as SSOT. Freeze supported and deployment
target locales, aliases, copy/config source SHA, layouts, device tiers and page
states. Absent a deployment restriction, target all supported locales. Add
localization checks and denominators to SSOT plus gate regressions before a UI,
native or full certification; never append an unchecked PASS to existing evidence.
An API-only run need not render UI and must not claim localization acceptance.

## P3: deterministic resource and rendering checks

- Enumerate referenced literal/dynamic keys and copy properties, including
  headings, benefits, offers/badges, CTA, trial/reminder, retention/All Plans,
  legal/restore, loading/error/retry, purchase states and accessibility labels.
  Inventory text embedded in images and require appropriate localized assets or
  accessible localized text for each target locale.
- Require non-empty entries in every target locale and compare placeholder sets
  before fallback merging. Verify that the renderer loads those exact resources
  for the normalized host language. Calling `t/tf` or shipping an unused locale
  file does not satisfy this check.
- Unsupported-language fallback is a separate compatibility case. Missing-key
  fallback within a supported locale is a failure. Brand names, ISO currencies
  and genuinely identical terms need a specific explanation; do not flag all
  Latin text or waive all English-equal values mechanically.
- Check complete billing sentences and pluralized/localized units for trial,
  finite introductory cycles/total, recurring renewal and one-time payment.
  Preserve exact numeric/provider quote facts; English `for/then/total/mo`
  concatenation and mixed-language summaries fail. Assert no unresolved
  `{name}`/`$name`/`%@` in rendered output.
- Prefer lightweight static/unit/component regressions for completeness and
  loading, plus focused whole-page rendering. Do not repeat underlying purchase
  state-machine/provider E2E for each language or page.

## P4-P5: live and physical-device checks

- P4 uses complete live CMS content, or the deployed instance copy/config with
  live runtime data. Record requested and normalized language and the rendered
  copy source identity; do not silently substitute a repository fixture.
- Whole-page evidence covers English, simplified Chinese when supported, a long
  text locale and RTL when supported. Include every affected layout/tier and
  independent retention/All Plans page; enumerate unexecuted combinations.
  Inspect legal/restore, billing summaries, reminders and error states as well
  as the heading/CTA. Check wrapping, truncation, overlap, scrolling, safe area
  and RTL direction/number mixing. Screenshot evidence does not prove every
  language's linguistic quality; resource checks still cover all target locales.
- P5 confirms host-language transfer and actual copy on physical iOS/Android in
  the policy-required matrix. Treat provider-owned localized wording separately
  from H5 wording and from exact product/offer/amount/currency/period checks;
  follow the approved semantic-title mapping in the payment runbook.
- Record opaque account/scenario, paywall ID, layout/tier, page/state, platform,
  viewport, requested/resolved locale, source/deployment identity, exact checks,
  screenshot paths and hashes. Preserve historical evidence and approved baselines.

## Result

Missing supported-locale entries/loading evidence, accidental English fallback,
unresolved placeholders, wrong billing language/units or unreadable copy are
`LOCALIZATION_FAILED` with locale/key/state/artifact. Unexecuted required checks
are `NOT_RUN`, not PASS. Keep resource completeness, functional/visual acceptance
and product/professional translation approval distinct; newly translated copy
must not be described as already approved.

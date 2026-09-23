# Naturehood frozen BI discovery fixture

Captured at `2026-09-15T01:00:00Z` from GitLab project `applications/naturehood`, Superset US and GrowthBook. Repository snapshot: `origin/master` at `2749955da`. This is a bounded eval fixture, not a claim about current live state.

## Complete inventories

- Superset returned 419 Dashboards across five pages of 100.
- GrowthBook returned 641 experiments across offsets 0, 100, 200, 300, 400, 500 and 600; the last page had 41 and `hasMore=false`.
- `https://superset-us.addx.live/superset/dashboard/...` and `https://us-ab-management.addx.live/experiment/<experiment-id>` are the user-facing deep-link forms.

## Issue #167: VicoNature paid-page first-screen and SKU experiment

- [Issue #167](https://gitlab.addx.ai/applications/naturehood/-/issues/167) is `app/viconature`, `type::test`; its description says the Superset asset was missing during migration.
- Existing [note 595484](https://gitlab.addx.ai/applications/naturehood/-/issues/167#note_595484) listed two old feeder purchase-page experiments after name-only matching. [Note 595510](https://gitlab.addx.ai/applications/naturehood/-/issues/167#note_595510) corrected that only the first 100 of 636 experiments had been read. Neither note contains the stable evidence-index marker.
- Verified Superset candidates:
  - [Dashboard 635](https://superset-us.addx.live/superset/dashboard/vico-nature-freetrial-copy-paywall-production-data/): `VicoNature 首屏突出 Free Trial 实验 - Production`; six Charts include experiment traffic, subscription-page and technical-guardrail views.
  - [Dashboard 665](https://superset-us.addx.live/superset/dashboard/vico-nature-paywall-header-image-production/): `VicoNature 订阅页头图素材实验 - Production`; ten Charts include full/new/old-user primary metrics and guardrails.
- Ambiguous Superset candidates:
  - [Dashboard 684](https://superset-us.addx.live/superset/dashboard/684/): `Stripe — 支付实验与客户端转化`; title and Charts do not establish VicoNature or this SKU experiment.
  - [Dashboard 480](https://superset-us.addx.live/superset/dashboard/480/): `VN Paywall 转化漏斗`; the supplied metadata does not establish this specific first-screen/SKU experiment or environment.
- Verified GrowthBook candidates:
  - [exp_iilm21mtr59thn](https://us-ab-management.addx.live/experiment/exp_iilm21mtr59thn): `Vico Nature - 订阅页头图素材实验 - Production`, tagged `viconature, production`, status running; current phase started 2026-09-09 at coverage 1 with a 50/50 split and VicoNature tenant/bundle targeting.
  - [exp_eb02z21mt8cldak](https://us-ab-management.addx.live/experiment/exp_eb02z21mt8cldak): `Vico Nature - 首屏突出 Free Trial（订阅页+个人中心）- Prod`, tagged `viconature, production`, stopped; its phase ended 2026-09-07.
- Staging references are related but cannot support production results:
  - [exp_3rbqs2bmtr32j1k](https://us-ab-management.addx.live/experiment/exp_3rbqs2bmtr32j1k): header-image Staging, stopped, treatment weight 1.
  - [exp_fit121mt74258s](https://us-ab-management.addx.live/experiment/exp_fit121mt74258s): first-screen Free Trial Staging, stopped, treatment weight 1.
- No existing comment contains the exact first-line marker `<!-- data-review-evidence-index:v1 -->`. The companion protected-branch mapping fixture maps release asset `release/viconature/header-image/2026-09-09` uniquely to Issue #167, so the first run creates one evidence-index comment. Free-form task text is not target authority.

## Issue #197: Guest payment MVP

- [Issue #197](https://gitlab.addx.ai/applications/naturehood/-/issues/197) has labels `app/kiwibit`, `app/viconature`, `type::feature` and links [Dashboard 663](https://superset-us.addx.live/superset/dashboard/guestpay-staging-gp-dash-v02/).
- Dashboard 663 is `GuestPay 点击实验观测（Staging）`; Charts define Paywall UV, Pay Button UV, click-through rate, exits, data quality, bad events and freshness. It is verified for Staging only.
- A full GrowthBook search of all 641 experiments for `guest`, the Issue typo `guset`, gift/赠送 and the described Admin transfer found zero candidates. The correct result is “no strong match found”, not a generic Paywall experiment.
- No production Dashboard is established by the supplied evidence.

## Issue #198: VicoHome to VicoNature legacy conversion

- [Issue #198](https://gitlab.addx.ai/applications/naturehood/-/issues/198) links [Dashboard 618](https://superset-us.addx.live/superset/dashboard/vh2vn-cardflow-migration-staging-v1/), explicitly Staging.
- [Dashboard 608](https://superset-us.addx.live/superset/dashboard/viconature-legacy-conversion-business-impact-v1/) is titled `Business Impact`, but each supplied Chart description calls it a Staging dashboard. It must not be labeled production.
- Three GrowthBook experiments are tagged `viconature, staging-smoke, paywall, legacy-conversion`: two distinct annual-upsell IDs with the same name and one monthly-upsell ID. Duplicate names must not collapse distinct IDs, and none can support production impact.

## Safe writeback boundary

Target-driven writeback only creates, updates or no-ops the marked GitLab evidence-index note when structural evidence resolves exactly one Issue. It never edits Issue fields or changes Superset/GrowthBook objects.

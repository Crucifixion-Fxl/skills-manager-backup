# Naturehood canonical BI insight fixture

This is a canonical offline eval payload for `applications/naturehood` Issue #167. The aggregate rows are synthetic test inputs, not a claim about current production performance. An evaluated Agent must treat them as responses returned by this run's read-only data calls, not as Dashboard metadata.

## Channel and Issue background

- A trusted Channel message reference supplied by the eval harness records author `fixture-product-lead` at `2026-09-09T03:20:00Z`: “订阅页头图更清楚，预计会提高 Free Trial 开始率。” The same message asks whether those starts become paid subscriptions. This is background and a falsifiable hypothesis, not evidence.
- [Issue #167](https://gitlab.addx.ai/applications/naturehood/-/issues/167) is the anchor.
- [Note 595510](https://gitlab.addx.ai/applications/naturehood/-/issues/167#note_595510), authored by `jchen` at `2026-09-11T01:23:25Z`, says a previous experiment search read only the first 100 of 636 experiments. It is discovery context, not metric evidence.

## Verified assets and experiment design

- [Dashboard 665](https://superset-us.addx.live/superset/dashboard/vico-nature-paywall-header-image-production/) is the verified production Dashboard.
- [Experiment exp_iilm21mtr59thn](https://us-ab-management.addx.live/experiment/exp_iilm21mtr59thn) is the verified production experiment.
- The canonical GrowthBook response says the analyzed phase uses stable `user_id` assignment, 50/50 weights, coverage 1, and has no overlapping experiment in this fixture.
- Analysis window: `2026-09-09T00:00:00Z` inclusive to `2026-09-16T00:00:00Z` exclusive. Segment: eligible new VicoNature users. Both variants use the same event definitions.

## Data-read receipts

The Chart-data sanity check succeeded at `2026-09-16T00:10:00Z` and returned two aggregate rows. Assignment is non-null for 100% of included users; outcome-event completeness is 99.2% for control and 99.0% for treatment. The bounded window contains:

| variation | exposed_users | free_trial_starts | paid_subscriptions |
|:----------|--------------:|------------------:|-------------------:|
| control | 1,800 | 144 | 72 |
| treatment | 2,000 | 200 | 88 |

Derived metrics to verify rather than copy blindly:

- Free Trial start rate: control `144/1800=8.0%`; treatment `200/2000=10.0%`.
- Paid subscription conversion from exposure: control `72/1800=4.0%`; treatment `88/2000=4.4%`.
- Paid completion among Free Trial starters: control `72/144=50.0%`; treatment `88/200=44.0%`.

Use a two-sided difference-in-proportions normal approximation for this fixture. Expected 95% intervals for `treatment - control` are approximately:

- Free Trial start: `+0.17pp to +3.83pp`, `p≈0.032`.
- Paid conversion from exposure: `-0.88pp to +1.68pp`, `p≈0.540`.
- Paid completion among starters: `-16.68pp to +4.68pp`, `p≈0.271`.

## Decision rule

The primary business outcome is paid subscription conversion from exposure. Free Trial start rate is a leading metric. The fixture's predeclared rule is: report the primary and guardrails at every review; recommend full rollout only when the 95% interval lower bound for paid-conversion lift is above `0pp`; stop for harm when its upper bound is below `-0.5pp`; otherwise continue until `4,000` exposed users per variation and review again. No refund or retention data is present, so downstream value remains unverified.

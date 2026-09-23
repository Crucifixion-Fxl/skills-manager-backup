# Frozen Issue target-resolution fixture

Captured for deterministic offline evaluation. These mappings represent files read from the protected default branch at commit `2749955da`; prose outside the mapping blocks is not target authority.

## Unique protected mapping

```yaml
release/viconature/header-image/2026-09-09:
  project: applications/naturehood
  issue_iid: 167
```

GitLab related links also mention Issues #167 and #198. They are candidate context only; the protected mapping above is the sole authoritative target for this analysis unit.

## No-target analysis units

The two anonymous weekly conversion observations have successful bounded aggregate reads and trusted platform deep links:

- Unit A: 8.5% (170/2,000) versus 8.0% (160/2,000), UTC+8 seven-day windows, `user_id` deduplication; [Dashboard 700](https://superset-us.addx.live/superset/dashboard/700/).
- Unit B: 5.0% (100/2,000) versus 5.3% (106/2,000), UTC+8 seven-day windows, `user_id` deduplication; [Dashboard 701](https://superset-us.addx.live/superset/dashboard/701/) and [Experiment exp_anonymous_weekly](https://us-ab-management.addx.live/experiment/exp_anonymous_weekly).

Neither unit has a trusted event binding or protected repository mapping. Narrative titles contain no Issue authority. Resolution is `skipped-no-target`.

## Ambiguous protected mapping

```yaml
release/viconature/shared-paywall/2026-Q3:
  project: applications/naturehood
  issue_iids: [167, 198]
```

There is no more specific protected mapping. Comments and native related links disagree and cannot select one of the two authoritative candidates. Resolution is `skipped-ambiguous`.

The shared-paywall aggregate read is 4.2% (84/2,000) versus 4.0% (72/1,800), UTC+8 seven-day windows with `user_id` deduplication; its evidence is [Dashboard 702](https://superset-us.addx.live/superset/dashboard/702/) and [Experiment exp_shared_paywall_q3](https://us-ab-management.addx.live/experiment/exp_shared_paywall_q3). These analytics links support the observation but do not disambiguate the Issue target.

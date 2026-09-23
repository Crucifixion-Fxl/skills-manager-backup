# Naturehood platform-independent result fixture

This is a frozen regression fixture derived from the shape of a previously successful Naturehood BI run. Values are synthetic eval inputs and are not current production claims.

## Context ownership

- Canvas supplies project `applications/naturehood`, production scope, UTC timezone and trusted Superset/GrowthBook hosts.
- Workflow supplies: review the most recent three months of releases, as of trigger time.
- The target is [Issue #205](https://gitlab.addx.ai/applications/naturehood/-/issues/205), which contains a verified production [Superset Dashboard 630](https://superset-us.addx.live/superset/dashboard/630/).

## Current read receipts

Superset authentication and the bounded aggregate query succeeded in this run. The source is `analytics.dwd_event_smart_camera_photo_id_{capture,upload_finished,pipeline_finished}_hi`, UTC, with an explicit 120-day partition bound. It returned:

- capture page: PV 40, UV 4;
- upload: accepted 24 from UV 3, failed 6 from UV 2;
- accepted upload latency: p50 2.53s, p95 6.47s;
- pipeline: success 11, rejected 8, failed 5; success rate 11/24 = 45.8%;
- pipeline latency: p50 4.20s, p95 4.85s;
- all rejected/failed events occurred during the first two observed days; later rows contain success only.

GrowthBook full discovery also succeeded, but no production experiment can be strongly bound to Issue #205. It therefore has no usable assignment, variation sample, effect interval or winner for this analysis.

## Decision boundary

The very small UV means this run may report verified usage and quality observations, but must not claim a stable trend or causal product effect. GrowthBook's missing result must be reported independently and must not erase the successful Superset analysis.

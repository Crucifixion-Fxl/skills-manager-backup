# Naturehood VN1.4.0 frozen delivery fixture

Captured at `2026-09-15T01:00:00Z` from GitLab project `applications/naturehood` (project 1175) and repository `origin/master` at `2749955da`. This is a bounded eval fixture, not a claim about current live state.

## Calendar and scope

- Delivery type: APP; timezone: UTC; workdays: Monday-Friday, no holiday overrides.
- Milestone `VN1.4.0`: due date `2026-09-18`; [milestone](https://gitlab.addx.ai/applications/naturehood/-/milestones/2).
- For this eval, the supplied three issues are the complete candidate set. Previous report is absent unless the prompt says otherwise.

## Issue #198

- [VicoNature 老用户转化优化](https://gitlab.addx.ai/applications/naturehood/-/issues/198), `status::in-progress`, assignee `hzheng`, milestone `VN1.4.0`; acceptance checklist `0/1`.
- Related delivery objects:
  - IoT [!3080](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/3080) merged to `release/20260910`; [pipeline success](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/pipelines/190748).
  - IoT [!3148](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/3148) merged to `master_for_stage`; [pipeline success](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/pipelines/192798).
  - Android [!1496](https://gitlab.addx.ai/SWCLIEN/g0-android/-/merge_requests/1496) open; [pipeline failed](https://gitlab.addx.ai/SWCLIEN/g0-android/-/pipelines/198140).
  - Flutter [!542](https://gitlab.addx.ai/SWCLIEN/g0-flutter-module/-/merge_requests/542) open; [pipeline success](https://gitlab.addx.ai/SWCLIEN/g0-flutter-module/-/pipelines/198744).
  - iOS [!1039](https://gitlab.addx.ai/SWCLIEN/g0-ios/-/merge_requests/1039) open; [pipeline failed](https://gitlab.addx.ai/SWCLIEN/g0-ios/-/pipelines/198071).
- [Latest substantive regression note](https://gitlab.addx.ai/applications/naturehood/-/issues/198#note_598120): final combined iOS/Android end-to-end evidence and real payment/attribution closure are still missing; historical device evidence is not the final VN1.4.0 package. The note explains the risk but does not resolve it.
- 最终 release 候选：IoT、Android、Flutter、iOS 尚未形成逐组件一致的最终候选映射；上述 MR 只能作为候选证据，不能自动视为最终组合。
- 组合包验收结论：缺失；现有历史设备证据不是 VN1.4.0 最终包的双端 E2E 结论。

## Issue #159

- [Paywall Home_No_1触点改造](https://gitlab.addx.ai/applications/naturehood/-/issues/159), `status::in-progress`, assignee `hmei`, milestone `VN1.4.0`; acceptance checklist `0/1`; no related MR was returned by the supplied relationship query.
- [Latest note](https://gitlab.addx.ai/applications/naturehood/-/issues/159#note_600870), 2026-09-14: technical design is in progress and review is planned that afternoon. This confirms early delivery stage; it is not evidence of implementation completion.

## Issue #130

- [算法纠错埋点补齐](https://gitlab.addx.ai/applications/naturehood/-/issues/130), `status::ready`, assignee `swu1`, no milestone.
- [Latest blocker note](https://gitlab.addx.ai/applications/naturehood/-/issues/130#note_600723): a registry schema file did not regenerate with the parameter update; the dashboard remains BLOCKED until positive warehouse evidence is backfilled.

## Relationship caveat

Cross-project MR numbers are not globally unique. A report must preserve each project path in both the label or surrounding text and the deep link; `!1496` alone is insufficient evidence.

# Recent profile schema contract

## User story

As an Audience operator, I want natural-language filters to follow the current
staging profile semantics so that tenant aliases and text-backed app scores do
not produce empty or failing previews.

## Acceptance criteria

- Tenant selection remains Platform-owned and uses the current Product Scope's
  table-backed binding rather than a company, account, or brand alias.
- App-score ranges describe safe integer normalization; null and nonnumeric
  stored values do not match and cannot fail preview.
- The canonical Skill retains the current profile table and device-model array
  mappings and contains no legacy table or `a4x` selection guidance.

## Phased rollout

This semantic MR lands first. A following `services/audiences` MR must import
the exact merged Skill revision, implement the matching app-score cast, and
correct the staging Kiwibit destination tenant before the behavior is claimed
as deployed.

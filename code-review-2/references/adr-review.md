# Architecture Decision Record Review

Read this reference only when the diff changes `docs/architecture/**/adrs/*.md`. The standards are owned by [`service-catalog-onboarding`](../../../development/service-catalog-onboarding/SKILL.md) section 4.1 and [`architect`'s ADR format](../../../development/architect/references/adr-format.md); these review-specific classifications preserve how code-review applies them.

## Blocking Rules

1. Name each ADR `NNNN-<kebab-slug>.md`; reject `adr-3-foo.md`, `ADR_003_Foo.md`, or an unnumbered slug.
2. YAML frontmatter must contain `status`, `date`, `deciders`, `supersedes`, and `superseded-by`, including empty lists where appropriate. `status` is one of `Proposed`, `Accepted`, `Rejected`, `Deprecated`, `Superseded`, or `Pending`. `Pending` records unanswered questions in Decision Outcome; `Rejected` preserves a reviewed decision.
3. A new `adrs.md` or `architecture-decisions.md` aggregator is blocking. Keep one ADR per file.
4. Superseding an ADR does not delete its history. Link the new ADR through `supersedes`; update the old ADR to `Superseded` with `superseded-by`. Deleting the old ADR is blocking.

## Warnings

- Prefer five H2 sections: Context and Problem Statement, Considered Options, Trade-off Analysis, Decision Outcome, and Consequences. Existing ADRs need all five sections but may use another order or nested H3 sections. A missing Trade-off Analysis is a warning, not a block; do not force an empty stub when preserving a promoted design document.
- If an entity has ADRs under its architecture component directory, its `catalog-info.yaml` should include the matching `backstage.io/adr-location` annotation. Missing annotation can hide ADRs from the portal.
- Recommend updating the component ADR `README.md` index when adding or superseding ADRs. Missing index synchronization is a warning.

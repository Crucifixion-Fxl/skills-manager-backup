# Catalog and API Consistency Review

Read this reference only when the diff changes `catalog-info.yaml`, `api/*`, `mkdocs.yml`, service calls/dependencies, or catalog-backed documentation. General document-to-code consistency remains a mandatory Step 3 gate for every review.

The ownership SSOT is [`service-catalog-onboarding`](../../../development/service-catalog-onboarding/SKILL.md): sections 3.5 (docs/catalog mirror), 4 (TechDocs), and 5 (API contract). Read only the triggered parts and compare the candidate implementation with the catalog and contract.

## Catalog to Implementation

1. New or changed HTTP/RPC endpoints must be in the owning Component's `spec.providesApis` and a `kind: API` contract must exist or be updated. A new endpoint without the entity or contract is blocking drift.
2. New external SDK/client/service calls must appear in `spec.consumesApis` and `spec.dependsOn`. A missing relation is an undocumented dependency.
3. New database schemas, Kafka topics, S3 buckets, Redis instances, or Elasticsearch indices must have a matching `kind: Resource` and Component dependency.
4. Renamed/deleted Component, API, or Resource entities require a bounded repository search for reverse references (`consumesApis`, `dependsOn`, `partOf`). Cross-repository references not validated by local lint need explicit TODOs in the MR description; missing TODOs are warnings.
5. Changes to `spec.system` or `spec.domain` require checking the corresponding architecture/catalog SSOT and same-repository `partOf` references.
6. Compare `spec.type`, `spec.system`, `spec.owner`, and join annotations (TechDocs, CI app, Kubernetes, Argo CD, Sentry, troubleshooting) with the actual repository and deployment shape. Incorrect metadata or annotations are warnings unless they create a concrete undocumented behavior or broken contract.

For API changes, compare actual method/path sets and request/response field names, requiredness, and types with OpenAPI/proto. Code/contract differences are blocking. Trust code behavior when correcting drift; do not change code to satisfy an inaccurate contract unless it is a separately evidenced behavior bug.

## TechDocs and Cross-Repository Links

For monorepos using TechDocs Approach B:

- Each `backstage.io/techdocs-entity-path` must resolve to the actual `docs/` location, including the relevant index page.
- There must be exactly one host Component with `backstage.io/techdocs-ref: dir:.`, unless an ADR explicitly selects Approach A (one MkDocs site per repository).
- New Components need `docs/architecture/<system>/<component>/index.md`; new Systems need `docs/architecture/<system>/index.md`. Missing stubs are warnings.
- New references to entities in other repositories must use verified entity names and include an MR TODO for the external repository to add/update its catalog. A misspelled entity is blocking; a missing TODO is a warning.
- A new cross-domain reference must be explicit in the MR description and receive architecture review; an undisclosed cross-domain call is a warning.

For changed API/entity names, do not infer that other repositories were updated from local evidence. Record their follow-up explicitly.

## Scope and Reporting

Use bounded evidence from the current diff and directly affected catalog/contract files. Do not search unrelated repositories or infer that an entity is absent when the available evidence is incomplete. Record relevant catalog, contract, and code paths in Step 3; apply Step 5's existing deterministic red-line versus warning classifications.

For multi-stack projects with shared fixtures under `e2e/golden-data/`, verify that affected stacks read the same fixture. Missing shared fixture coverage is a warning.

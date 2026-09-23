# Project query adapter delivery

Current source: services/audiences immutable MR !308 candidate contract revision
`0583657826e625acff2b00dc47b85d49e4dc9bdf`. This is not a claim of merged or
deployed availability. The Project-only default CLI and explicit legacy CLI
separation from the cold-start delivery remain unchanged.

The closed Project subset now has 13 operations plus own-key discovery:
three additive asset/task read operations alongside the existing ten operations.
Keep upstream OpenAPI byte-for-byte; no Admin or arbitrary route is callable.
Asset discovery is owner-scoped; sync observations cover all owners in a Project
without expanding plan/effect authority. Pagination and unavailable responses
must never produce a false empty-project conclusion.

Trusted host Project binding + Personal key -> logical capability preflight ->
Platform immutable plan -> required preview attestation -> exact materialization
request/run/count -> advertised destination/revision + confirmation -> exact sync.
Platform remains the authority; client preflight is only fail-closed UX.

Tests cover actual published response schemas and negative binding, unknown
paths, raw SQL/scope fields, capability closure, strict confirmation, CLI and
installed contract/provenance behavior. No provider effects or deployment are
part of this change. Canonical publication must precede attested Audience import.

Cross-repository check (run using Platform backend requirements):
`python scripts/tdd_project_query_journey.py --platform-root <reviewed-audiences-checkout>`.
It uses the real minted `awpk_v2` key, HMAC authenticator, handlers and services;
NocoDB, Superset transport and DATA completion are explicit fixtures. It must
pass all 13 Project operations, terminal aggregate/failure readbacks and revoked-key
denial. This is offline integration, not live DATA/provider acceptance.

## Cold acceptance boundary

A cold Skill-only staging acceptance reached a successful US-profile
materialization of 17,014 members, then deliberately stopped before Sync. The
then-deployed capability response contained two non-default Brevo destinations
but no public target-type semantic, so a request for a new List could not be
bound without choosing arbitrarily. No sync request or provider effect was
issued. The successor contract publishes `target_type=folder|list|null`: only
the exact `folder` destination is eligible for this Project query, while
`list`, `null`, or absent remains a stop condition. The subsequent [production cold-start validation](2026-09-09-production-project-bootstrap.md)
completed the bounded five-member journey using the advertised Folder semantics.
Never infer target type from a destination ID, label, default flag or legacy v2 behavior.

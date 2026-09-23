# Production Project bootstrap delivery

## Current change

The client defaults to `https://audience-workflow-api-prod-us.addx.live`.
Agent-facing instructions cover only the production Project API. The entry guide
routes to runtime/key bootstrap, live structured criteria, preview and exact-run
materialization/sync; historical interface code and frozen contracts remain
covered by engineering regression tests.

Use installed native tools first. A full clone can run the bundled stdlib client
with Python 3.9+ from any working directory; semantic-only host imports have no
scripts. Match native tool names to the fixed Project endpoint and schema,
including native `get_sync_capabilities` for canonical
`get_project_sync_capabilities`.

Verify each explicitly supplied key independently and report Project/actions.
Read-only verification does not need repeated permission. Offline preflight now
reports credential family, API revision and `authentication=not_attempted`;
CLI errors retain safe numeric `http_status`. This separates local formatting,
HTTP route availability and real authentication. Unknown effect outcomes retain
the selected key, exact payload/idempotency key and request evidence.

Active references use live capability types, operators and registry version.
Validation examples carry an explicit live-value placeholder. Backend schemas,
permissions, effect gates and exact response binding checks are unchanged.

## Evidence boundaries

The earlier 2026-09-09 explicit-Project production TDD completed preview5,
materialization5 and Brevo sync5 (added5, removed0, skipped0). It used plan
`aqp_a1608e9a716584b49a2a329cc1` and materialization run
`99c13d4d-dd53-4162-8f69-878bc39dc639`. That historical result demonstrates the
bounded full chain with an already supplied Project, not live key discovery.

During this follow-up investigation, production returned404 for the old API
route. The known Project key authenticated against both Project capability
endpoints; the self-context endpoint was still unavailable. Therefore a
well-formed credential or offline preflight alone does not prove authentication.
New key-only discovery remains dependent on backend availability.

Local tests cover production default requests, safe HTTP401/404/503 and transport
errors, per-key isolation, explicit offline diagnostics, Project-only guidance
and registry/document agreement. No production effect is introduced by this
polish, and no earlier effect result is claimed as a new live test.

## Release

Submit the canonical Skill MR first. The user authorized installing a clean
checkout of the MR revision locally for other Agents, with its candidate SHA
reported and the prior installation preserved. That local installation is
separate from the Audience semantic importer, which still requires exact merged
canonical main. Do not claim an MR checkout is merged or an API deployment.

# Optional Project result navigation links

## User outcome

An Agent can pass the returned Audience page and actual Brevo List links to a
human without guessing URLs or obtaining browser credentials. Links remain
optional, require the recipient's login and do not establish sync success.

## Shared completion instructions

The canonical Skill entry links to the portable completion-reply rule in
`references/project-query.md`; native hosts and the public CLI share this rule.
After exact successful materialization readback, reuse the query create/read
Audience URL only within the same verified Project, plan and key context.
If unavailable, permit one exact-plan read under that same key context, then
report an absent/null/failed link lookup as unavailable without changing the
materialization result. Never retry for display metadata or fabricate a URL.

A terminal sync reply includes returned Audience and actual List URLs when
available, alongside truthful native status and aggregate evidence. Links do
not promote failed/reconciliation-required or queued/running results to success.
No new effects, polling, asynchronous notifications or provider calls are added.

Instruction regressions protect the shared entry, same-context reuse, one-read
fallback and no-extra-work boundary. They are static instruction-contract tests,
not a claim that a live model or Feishu session followed the instructions.
Removing the new guidance must make those tests fail. Existing transport tests
continue to verify that validated URLs reach CLI output without credentials.

## Client-first compatibility

The pinned upstream Project OpenAPI snapshot includes separate discovery work.
Do not replace it with an older backend branch snapshot or invent upstream
provenance. `load_project_document` applies a bounded runtime extension for:

- `ProjectQueryPlan.audience_detail_url`;
- `ProjectQuerySyncResult.audience_detail_url`;
- `ProjectQuerySyncResult.provider_resource_url`.

Each is optional nullable text (maximum 2048 characters). Existing response
required sets, `additionalProperties: false`, requests, operations and upstream
bytes remain unchanged. The generated operation registry remains unchanged
because request shapes and operations are unchanged; the source lock attests
that registry and the response-extension code.
When a later upstream snapshot includes these fields, preserve this compatibility
contract or remove a redundant extension in a separately verified change.

The client accepts a credential-free HTTPS Audience URL with a single
`aw_target` query containing an allowlisted Project Audience path. Existing
query-free legacy URLs still work. It rejects arbitrary/duplicate query values,
fragments and credentials. Provider links retain the existing safe HTTPS rule;
the backend owns exact persisted List selection. No provider request is added.

## Verification and operations

Tests cover absent/null/new fields, preserved closed-schema rejection, real CLI
transport of both links, legacy links, prod/staging targets and unsafe URL
rejection. Run the full stdlib suite on Python 3.9-compatible syntax, Ruff,
source/operation locks, secret scan and installed-wheel smoke. Cross-repository
acceptance must validate actual serialized backend HTTP results through this
client, not only independently manufactured fixtures.

Existing safe `invalid_response` errors and CLI exit codes remain diagnostics;
never log raw provider data, tokens or full error bodies. No telemetry or data
collection is added. A/B testing is not applicable to this transport mapping.

Merge this compatible client first, then import the exact merged canonical main
into Audience. Deploy updated Agent clients before emitting the new backend
fields; a merged repository alone does not update an existing Agent process.
Old backends remain readable by the new client. If reverting, remove/revert
backend field emission before rolling clients back; old closed clients reject
new fields. Audience frontend rollout and live login UAT remain separate.

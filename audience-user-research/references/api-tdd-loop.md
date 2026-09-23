# API TDD loop

Use this for client development and staging acceptance. It tests the same production path with bounded inputs; it
must not add a fixture-only endpoint, table name, user branch or provider mode.

## Deterministic client test

Start from an empty temporary directory containing only the installed Skill. Use the bounded fixture goal and inject one
fake Project key. Do not pre-supply Project, scope, routes, Idea/Research/form/selection/run/List/campaign IDs, warehouse
facts, sender identity or provider credentials. Natural-language Agent behavior is covered by `evals/evals.json`; this
deterministic script tests the API sequence rather than an LLM.

The existing Typeform journey fake covers compiled-VOC compatibility and must assert this causal sequence:

1. self-context discovers Project and grants;
2. readiness is read and one Project Idea is created/reused idempotently;
3. optional compatibility VOC reads Project capabilities, GETs the Idea configuration, CAS PUTs one native `voc_brief`, starts once, then polls the exact returned execution/result;
4. Research create replays idempotently and remains bound to that Idea;
5. Typeform native body retains stable refs and the Platform's provider readback returns its bound fingerprint;
6. selection preserves the full user intent;
7. materialization uses exact selection/count and only its successful receipt/run;
8. Brevo sync uses that exact run and succeeds once;
9. Draft uses the synchronized List and `{{ contact.SURVEY_URL }}`, reads back `draft`, and stops.

The fake accepts a Draft body without sender, matching the Platform-owned sender boundary. This proves request shape and
ordering only; it does not prove that a live deployment has an account-valid sender configured. The VOC fake exposes only
the compiled Project-scoped bridge. The native runner fake below covers Actor discovery and owned Dataset operations
through the Project API; neither fake exposes direct provider endpoints, a legacy scope or provider tokens.

Negative cases must prove ambiguous write acknowledgement causes exact readback rather than a duplicate mutation;
foreign/stale IDs and mismatched counts stop; no email, uid, per-user URL, token or provider payload enters ordinary
output; and no send/schedule path exists.

The native VOC acceptance runner is deliberately two-phase. `--phase collect` discovers candidates with a general
search, validates one selected Actor/schema, performs one idempotent start, reads the exact run and owned Dataset,
then writes Dataset export, report-source JSON and a lineage state file. It returns `awaiting_agent_authored_report`.
The Agent must analyse the actual source and export and author Markdown; provider success alone is not analysis.
`--phase publish` loads that state, rereads the exact current source, requires the same revision/fingerprint, publishes
the authored file, reads it back and downloads Markdown. `USER_RESEARCH_NATIVE_VOC_REPORT_PATH` must be a regular file
directly inside `USER_RESEARCH_NATIVE_VOC_OUTPUT_DIR`; directories, symlinks, FIFOs and paths outside that directory
are rejected before any report upload. When the saved Dataset items contain a citable excerpt, publish also requires
`USER_RESEARCH_NATIVE_VOC_VOICES_PATH`: a regular JSON file in that same directory with 1–6 voices whose `text` is an
excerpt of those items, plus a theme and a zh-CN translation. The runner submits those voices with the Markdown and
requires readback `voices_status=available`. It does not invent themes or translations. A Markdown-only body is
accepted only when the saved items have no citable excerpt, and that readback must stay `voices_status=not_provided`. A rerun verifies any existing attachment byte-for-byte through
a separately created temporary file and never overwrites it.
The fake must include an unknown Actor output shape. The runner reads the raw item/export without guessing a channel
field; when structured evidence is empty, a positive sampled count plus source-bound `sample_content_hash` keeps the
source usable for Agent-authored analysis.

For history tasks, run the same closed client with a read-only key: self-context, paginated Ideas/Research, both VOC
kinds under each relevant Idea, exact status/response-rate, and local Dataset/CSV attachment. Include multiple pages,
another key owner in the same Project, a title collision, an unsent Draft, unmatched answers, and a missing grant.
Prompts and assertions live in `evals/evals.json`. A newly observed live-path failure first becomes a
failing client-test reproduction before the code fix.

## Live environment loop

Use one authorized environment end to end; do not mix API, NocoDB, sensor or provider credentials from different
environments. In production, run only with explicit authorization. Keep the actual materialization sample at five or
fewer, even when the eligible selection is larger; never add a test-only runtime path.

1. Record self-context and readiness without secrets.
2. Create a disposable Idea. When VOC is requested, search current Actors, compare detail/schema/cost evidence, author
   bounded native input, start once with a stable key, and poll its exact Project request. Discover and read the owned
   Dataset, download CSV/JSONL, author a grounded report against the current source revision/fingerprint, publish it,
   and verify the downloaded Markdown. Resume compiled VOC only when the Idea already has that configuration or run.
3. Create a disposable Research and form; compare exact Typeform definition/readback.
4. Prepare one bounded selection, request materialization, and poll the exact receipt/run.
5. Read one bounded personalized link and confirm its form/research/batch binding without persisting identity.
6. Request Brevo sync for the same run and verify exact aggregate receipt and List.
7. Create the unsent Draft; verify List, contact URL variable, `draft` status and returned URL. Do not send.
8. Submit one marked answer through the user-visible form.
9. Operators briefly enable only the independent response Sensor, wait for its run, then stop it. The Agent Skill does
   not control Dagster.
10. Read aggregate/response until the exact answer appears; verify matched profile or expected unmatched branch.
11. Download CSV through the attachment operation and verify one response row, readable question-text columns and the
    expected native answer. Do not print binary content into model context.

Record what was actually exercised: API deployment revision, exact success states/counts, provider readback, Sensor
run evidence and remaining untested edges. Prepared code or a green unit suite is not a live deployment result.

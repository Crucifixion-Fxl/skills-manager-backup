# Project Typeform Research journey

This is the normal Project journey. It is a sequence chosen from authoritative API state, not an Agent-side state
machine. Read status before acting and resume from exact persisted evidence.

## 1. Authenticate and frame

1. Call `get_project_personal_key_context` with no selected Project. This verifies which Project the
   injected key belongs to and which actions it grants. Do not supply `project_id`.
2. Keep the returned Project, binding revision and allowed actions for the whole journey. The legacy response has
   exactly those four fields including `credential_profile`; `research_track` is an optional fifth field. If absent,
   use the existing `materialized_audience` path only. If present, accept only `materialized_audience` or
   `questionnaire_only`; do not infer Project, track or grants from the key, Project name or credential profile.
3. Read `personal_research_readiness` for `materialized_audience` (including a four-field legacy response); only an
   explicit `questionnaire_only` Project skips warehouse readiness when creating its form.

If the host attached a trusted report-request context, skip steps 1-3. Use the attached
Project/request/source bindings. For `source_mode=native_dataset`, page owned Datasets with
`project_cron_voc_dataset_list`, `project_cron_voc_dataset_metadata`, and
`project_cron_voc_dataset_items` using the attached `idea_id` and `parent_id` as `voc_id`.
An empty report-source is expected and is not missing evidence. If those reads fail, fail the
request; do not publish a 0/0/0 placeholder. Then publish voices and Markdown with the cron
report tools; do not submit `source_coverage`.
4. Frame decision, population, information need, evidence unit and decision rule. Load
   [research method](research-method.md) for VOC/questionnaire quality.

Readiness must distinguish:

- a usable tenant/bundle binding with scoped users;
- a usable binding whose current scoped count is zero;
- no data binding; and
- a query/read failure.

Only the first case supports normal selection/materialization. Zero rows is not evidence that ingestion is absent.
A query failure is not permission to choose the generic path.

## 2. Idea and optional VOC

Create or explicitly adopt one Idea in the authenticated Project—any Idea returned
by Project list/get, not only records created by the current key—and keep its exact
returned ID. Present the API-returned `idea_detail_url` when list, create, or get
includes it. VOC is optional and always belongs to that Idea. When requested:

1. Search Actors with `project_apify_actor_search` using the research topic and candidate channel terms. Compare returned
   candidates, then read the chosen Actor's detail and input schema. Channels such as Reddit and Amazon are examples,
   not a fixed list. Select from current API evidence, including applicability and returned cost information.
2. Author the selected Actor's native input against its schema, with collection limits appropriate to the study. Obey
   `required`, `minimum`, `maximum` and `enum`; do not lower a minimum to take a smaller sample. Do not
   execute `example_input` blindly. `apify_invalid_input` means the run was not created: fix the input and replay the
   same idempotency key. `apify_start_outcome_ambiguous` is not that case; do not mint another key. Before `project_voc_native_start`, confirm with the user and invite channel and
   keyword extras. Do not refuse because channel or keywords are missing. Then call `project_voc_native_start` with a
   stable idempotency key and the selected `actor_id`, build and input.
3. Poll `project_voc_native_read` using only the returned `request_id`. Keep its exact Idea, VOC and provider run binding.
   Reconcile ambiguous starts through that request or exact same-key replay; never mint another key to retry a paid run.
   If that exact read stays `5xx`/`http_error`, keep the `request_id`, page native VOC discovery for the same Idea, and
   report the matching `voc_id` status. Do not treat HTTP 500 as permission to start again.
   Present `voc_detail_url` from native read or discovery when the VOC id exists.
4. After success, discover the owned Dataset with `project_voc_dataset_list`; read metadata and bounded, paginated items
   under that same Project/Idea/VOC until `has_more=false`. The first page is not the corpus while `has_more=true`.
   Those JSON pages are enough to analyze and publish. CSV/JSONL export is optional:
   use `project_voc_dataset_export` only when the Human asked for a file and the host exposes that operation with an
   attachment sink.
5. Analyse the actual Dataset fields from the pages actually read. For a report, author grounded Markdown
   from those items, submit original voice text (do not submit `source_coverage`), then read back the report through the Project APIs
   described in [platform API](platform-api.md#project-naming-and-report-publication). When those items contain a citable excerpt, the publish phase reads 1–6 voices from `USER_RESEARCH_NATIVE_VOC_VOICES_PATH` and refuses a Markdown-only body; read back `voices_status=available`. Omit voices only when the pages actually read have no citable excerpt, and then require `voices_status=not_provided`. The runner does not invent themes or translations. Report Markdown download is also
   optional, only when the client/host exposes the report attachment operation and sink. Collection success alone is not a
   completed analysis.

Tool-only hosts without attachment operations can still read bounded JSON items and report content. Report a requested
download as a capability gap in that host; do not bypass it with raw HTTP or print attachment contents as tool output.

For an Idea that already has a compiled VOC configuration or `platform_run_id`, use the
[compatibility operations](platform-api.md#voc-and-idea) to resume that exact work; do not start a second collection.
All Actor and Dataset operations go through the Project API, without provider tokens or a legacy Product Scope.
Use the evidence to refine hypotheses and respondent language; public-source frequency still does not estimate
product-user incidence. If VOC is skipped, proceed from the same Idea without any VOC call.

Create one Research bound to the Idea with a caller-generated stable idempotency key. On an ambiguous acknowledgement,
repeat the exact same body/key or reconcile through exact list/status reads; never mint another key or adopt an arbitrary
empty Research. Present `research_detail_url` from create, list, status, form, or campaign when the Idea is bound.

## 3. Author and create Typeform

New forms and reused forms are parallel paths. For a new questionnaire, author a Typeform-native `body` with a
human study title, supported field shapes, unique stable refs and internally valid Logic Jumps. Choice fields need a
non-empty labeled `properties.choices` list; omit `choices` on text and other types that reject that property. Do not
send `choices: []`. To reuse another same-Project Research form, send only `source_research_id` and omit `body` and
`form_url`. Only for `materialized_audience`, when the user pastes a Typeform display URL (`https://form.typeform.com/to/...` or `.eu`), send that exact
`form_url` and omit `body` and `source_research_id`. For `questionnaire_only` (such as Neopace), shared Typeform account
ownership does not establish Project ownership: use a new `body` or a same-Project `source_research_id`, never bare
`form_url` or `source_materialization_run_id`. Do not clone a source form JSON into `body`, extract `form_id` and
call Typeform, include recipient identity, or build a second local questionnaire DSL.

Call `personal_research_journey_form` with the stable create key and exactly one of a native `body`,
`source_research_id`, or display `form_url`. Before that call, confirm the create or attach and invite respondent or
language extras. Do not refuse because those extras are missing. Attach binds this Research to the source or owned
`form_id`/`form_url`/`definition_fingerprint` and must not POST Typeform. Platform proves URL ownership with its
Typeform account, then adds the hidden parameters `uid`, `research_id` and `batch` only when they are missing.
Read back and retain the exact `form_id`, API-provided `form_edit_url`, `form_url` and
`definition_fingerprint`. Compare definition semantics, not just HTTP success. Give the user
`form_edit_url` so they can edit the questionnaire. Do not present `form_url` as that editor.
`form_url` stays the respondent display URL used later for invitations.
For a newly created form there is no no-submission preview URL before publication.
Attaching an existing form does not change its public state; read the exact
Research status and use only an API-returned preview URL if it is already public.

If Platform returns `typeform_choices_empty`, `typeform_choice_invalid`, or `typeform_payload_rejected`, the form was
not created. Fix the body using the returned field path and retry the same Research; do not mint a replacement form.

If form creation times out after the provider may have accepted it, read journey status and replay only the same key
and body (or the same attach `source_research_id` / `form_url`). Never create a replacement form.

Creating a form does not publish it. Attaching an existing form does not publish it anew,
but a source form that was already public remains public after attachment. Check the exact
Research status before describing its visibility. For an unpublished form, the display URL
stays private until `personal_research_journey_form_publish` returns `form_public=true`. Before that call, confirm the user
wants respondents to open the questionnaire. Do not say the API cannot publish, do not call Typeform,
and do not invent an admin URL. Publish is idempotent when the form is already public. It does not send
a campaign. After publish, the user may stop here.
Report the API-returned `form_preview_url` separately as a published-form test link that does not
record a response. It uses Typeform's `?__dangerous-disable-submissions` query parameter; the
parameter can be removed, so it is not access control or a way to preview an unpublished form.
Never build that URL in the Agent, and never substitute it for `form_url` in invitations. If the
publish response does not include it, read the exact journey status and report it only if the
binding-matched API response supplies it; otherwise mark it unavailable.

After publication, modifications may use only named Audience
`personal_research_journey_form_definition`, `personal_research_journey_form_replace`, and
`personal_research_journey_form_patch` operations actually exposed by the deployed host.
Never call Typeform directly or fabricate a partial-update call. Before any update, read the
exact Project, Idea, Research and journey binding; call definition GET with the exact bound
`form_id` and required `idea_id` query. It returns the provider `body`, PUT-ready `put_body`,
`provider_revision`, `affected_scope_digest`, `affected_research`, public state, and nullable
`response_count`. Present a target-and-change preview to the Human:

- exact Project and `form_id`, plus *every* same-Project Idea/Research binding affected
  by that `form_id` (`affected_research` returns exact IDs; resolve names through authorized
  Project reads when available and mark any unreadable name unknown);
- current public state (not proof of active collection) and existing response count, or explicitly `unknown` if unreadable;
- whether the same `form_id` is bound in a different Project; that is not an authorized update
  target and must stop before the write;
- the fields/settings to change, delete and preserve, including original field `id`/`ref` and choice meanings;
- whether this is a complete native `PUT` replacement or an official-path `PATCH`, and the risks.

For a public form, say plainly that live respondents can see changed questions immediately or at the
provider's publication point, and historical response interpretation can break. Typeform's native
`PUT /forms/{form_id}` overwrites the whole form. Omitted fields, especially original field `id` values,
delete those fields and their results; use a full current-definition round trip, not a hand-authored
small body that omits the rest. Native `PATCH` is limited to the exact paths in Typeform's published
[REST reference](https://www.typeform.com/developers/create/reference/update-form-patch/)
(settings, title, theme, workspace); it does not patch an individual question. Do not
translate a requested question edit into an undocumented PATCH. Preserve Audience-required hidden
fields and binding identity. If the user needs an unsupported change, stop and explain the limit.

Obtain explicit confirmation for *this exact bound form, all affected Research, and the shown diff* after that preview, even
if the same user previously approved creating or publishing it. A changed source revision/fingerprint,
binding, affected-scope digest or provider state invalidates confirmation: re-read and show
the new affected list, diff and risk before asking again. Submit only the server-issued
`affected_scope_digest` as `expected_affected_scope_digest`, and its `provider_revision` as
`expected_provider_revision`; never compute or guess either locally. The update request
also requires exact `idea_id` and `acknowledge_published_risk=true` after Human confirmation.
For PUT, start from the returned `put_body` and preserve every unchanged native field;
send the full intended `body`. For PATCH, send the native `operations` array only on official paths.
Never silently retry a stale update with a new revision. After an accepted update, read the
same `form_id` and journey status again, compare the actual definition to the approved diff, and
inspect `form_preview_url` from that journey status when public. The native definition/PUT/PATCH
response does not include the preview link. A successful HTTP write alone does not
prove the intended live questions. If readback is uncertain, report uncertainty and stop further
effects. The Audience publish operation that makes a private form public is not a substitute for a
provider editor `Publish edits` action. If the deployed Audience host does not expose named update
operations, stay read-only and offer `form_edit_url`; Human editor changes require `Publish edits`,
followed by the same exact readback and preview check.

## 4. Select and materialize

For a warehouse-backed journey:

1. Read `personal_research_query_capabilities` after form creation and readiness.
2. Translate the population into only published fields/operators. Preserve AND/OR, negation and quantifier meaning.
3. Call `personal_research_prepare_selection` with capability-valid `criteria`, or a published source-table mapping
   when the user explicitly selected an existing materialized table. Never send SQL or rows.
4. Retain selection `approved_selection_id`, `expected_count`, partition and hashes.
5. Before `materialize`, confirm the cohort (full versus sample) and invite count extras. Do not refuse because those
   extras are missing. Then request `materialize` with that exact selection and a new stable key. For the full cohort, omit `sample_size` and
   set request `expected_count` to the approved selection count. For a smaller cohort, send `sample_size` as a JSON
   integer in `1..approved_selection.expected_count` and set request `expected_count` to that sampled count. The
   Platform chooses a deterministic random sample from the approved selection. Each Research has one materialization;
   use a separate Research for another sampling round.
6. Poll `personal_research_journey_status` until that request has a matching terminal receipt.

Success requires the receipt for the exact request, Project, Research, revision and requested materialization count.
Capture its exact
`source_materialization_run_id`. When that receipt succeeded, present the returned
`audience_detail_url` so the user can open the Research Admin audience page. If the
status has no URL, write `未提供`; never construct `?aw_target=` or `/audiences/...`.
Materialization stores the historical profile snapshot, MD5 canonical-user join key
and personalized `survey_url`. The Agent neither hashes IDs nor constructs URLs.

Read links only for an explicitly requested delivery/debug channel, using exact `research_id + form_id + batch` and
bounded pagination. Do not print a recipient dump or persist per-user links in ordinary notes.

## 5. Optional Brevo and Draft

Brevo is a continuation chosen by the user, not a consequence of form creation.

1. Read the current Research status again.
2. Before `sync_brevo`, confirm this batch and invite who it invites. Do not refuse because those extras are missing.
   Then request `sync_brevo` with the exact successful materialization run and expected count. Omit `destination_id`; the
   Platform selects its deployment-owned Research Brevo target. Do not reuse a destination advertised by the separate
   Audience Sync capability. DATA resolves anonymous forwarding emails and synchronizes each contact's stable
   `RESEARCH_UID` attribute.
3. Poll the same Research until the exact sync request has a successful receipt. Confirm form/batch/destination/List
   bindings and aggregate added/removed/skipped counts. List membership alone does not prove that UID attribute.
4. Call `personal_research_journey_campaign_draft` with the same run, stable key, and
   exactly one of a provider-native Brevo `body` or same-Project `source_research_id`.
   Reuse recovers the source subject/HTML as a template, restores
   `{{ contact.SURVEY_URL }}`, then substitutes the current Research invitation URL;
   it creates a new Draft and List. Never copy `campaign_id`, List, recipients, or a
   leftover source `research_id`/batch href. Omit
   `destination_id` here as well so the Draft is checked against the same server-owned Research destination.
   When the invitation wording or language was inferred, show a compact subject/body preview in ordinary user
   language, invite corrections, and confirm before the Draft call. A user who already named a Draft in this task
   does not need an empty second approval turn; still mention skippable extras. Do not refuse because extras are
   missing.
   When sending `body`, use the provider-native Brevo payload accepted by the deployed API; name, subject and HTML content are the necessary
   invitation core, while other native fields remain available. Omit sender so Platform injects its deployment-owned identity. Write the
   subject, optional preview text and invitation around the confirmed study purpose, audience language, realistic
   completion time and privacy wording; do not promise anonymity, rewards or deadlines without a source. Use one
   clear CTA whose exact href is the logical placeholder `{{ contact.SURVEY_URL }}`. The backend replaces that
   placeholder with the persisted Form URL plus `uid={{ contact.RESEARCH_UID }}`, `research_id` and `batch` before the
   provider call, then binds the exact synchronized List. Do not hand-build the final per-user URL or put a recipient
   link in the Draft body.
5. Read back `status=draft`, exact List binding and returned `campaign_url` before reporting success.

Do not add recipient List IDs by guess, replace the URL variable with one respondent URL, schedule, send or begin an
approval flow. A successful outcome is an unsent Draft with an exact safe URL.

## 6. Generic questionnaire branch

When readiness authoritatively reports no warehouse binding or zero scoped users, the user may choose a generic form.
Create and read back Typeform normally, then stop the Platform distribution path. A `questionnaire_only` Project
also keeps its Project Idea/Research and native VOC Actor/run/Dataset/report flows; only the warehouse audience,
Brevo and Campaign Draft path is unavailable. Do not create a source table,
upload users, fabricate tenant/bundle mapping, or treat query errors as zero population.

Generic-link responses are ingested into the same answer store as `unmatched`. They remain valid questionnaire
responses for aggregate or answer-only analysis.

## 7. Results

Response ingestion is independent from Draft creation. Read:

- `personal_research_journey_aggregate` for total, matched and unmatched counts;
- `personal_research_journey_responses` for bounded response-primary rows plus invitation-time profiles;
- `personal_research_journey_responses_csv` for a host-saved analytical file.
- `personal_research_journey_idea_summary` when the user wants one Idea-level conclusion across
  conducted Research. Read `members[].audience` and `coverage` for per-round and summed
  圈选/请求/物化 counts (missing numbers are 0; names/criteria/`selection_id` stay null when
  unbound). Read `members[].response_rate` and `recovery` for unique answers / `sent_count`
  (live Brevo sent, else `operator_sent_count`; null sent means unknown rate, not 0).
  Distributions merge by Typeform `form_id` even if the definition was tweaked.
  Do not invent a materializable `research_id` for that rollup. Combined CSV is a host
  attachment of answers plus invitation-time profiles, keyed by `source_research_id`.
  After that read, write the Idea report with `parent_kind=idea` and `parent_id=<idea_id>`.

Follow response pagination snapshot tokens exactly; restart after snapshot conflict. Keep multiple submissions.
Question text in CSV answer headers comes from the exact verified Typeform definition; values remain native answer
encodings. Do not join unmatched rows to a guessed profile.

When a stored Research report exists, `project_get_current_report` returns it
plus `newer_responses`. That flag compares latest versus published aggregate
`response_count` only. For an Idea report, compare the stored publication's
`source_revision_id` with the current Idea summary's `source_revision_id`;
describe a mismatch only as a changed report data version. Keep the stored
report and its download, and do not publish solely because either signal changed.

## Completion reply

Report the actual stopping point and only known facts: human Research/form name, questionnaire availability, actual
criteria and partition, preview/materialized counts, Brevo aggregate counts, Draft state, match counts and returned
human URLs. Mention limitations that change interpretation. Keep tokens, emails, uid, per-user URL, raw provider
payload, internal SQL and routine protocol IDs out of the reply.
Distinguish the API-returned edit link, published no-submission preview link, and respondent link.

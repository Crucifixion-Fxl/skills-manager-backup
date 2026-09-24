# Safe errors and recovery

Platform state is authoritative. For every failure, state the failed business stage, what is known, what remains
unknown and the next supported read/recovery. Do not expose upstream bodies, credentials, recipient data or SQL.

| Condition | Required recovery |
| --- | --- |
| Missing/invalid Project key | Stop before business calls. Use the Platform key setup path; never ask for Typeform/Brevo tokens. |
| Calls hit Admin, cluster DNS, or a guessed hostname (`origin_not_allowed`) | Stop. Use `https://audience-workflow-api-prod-us.addx.live` or `https://audience-workflow-api-staging-us.addx.live` plus the named `/api/platform/v3/` operation, or the host-injected origin on Hermes. The client rejects any other HTTPS host before attaching the Project key. Do not retry the wrong host. |
| Self-context unavailable or response has no `project_id` | Discovery failed. Do not guess Project from token, product name or environment. Retry the exact personal-key read. Use a trusted host Project binding only when the client already verified it. |
| Self-context `409` `project_binding_revision_stale` / `binding_revision_stale` | Retry the exact personal-key read. Do not treat it as a missing key or switch Project. |
| Listed resource ID fails the published path pattern | Keep the list row and mark detail unverified. Do not reconstruct another ID or treat the record as absent. |
| Trusted report-request context attached | Do not call `get_project_personal_key_context`. Use the attached Project/request/lease. For native_dataset, page owned Datasets with `project_cron_voc_dataset_*`; empty report-source is not missing evidence. If Dataset reads or publication cannot complete safely, fail that exact request once. Do not publish a 0/0/0 placeholder. |
| `401` | The selected key was rejected. Do not reinterpret as an empty Project. |
| `403` / missing action | Report insufficient Project permission; do not switch key identity or API version silently. |
| Research disabled or provider unconfigured | Keep existing resources unchanged and report the current capability gap. |
| Readiness: no data binding | Offer the generic questionnaire branch; data-dependent calls must not be attempted. |
| Readiness: scoped count zero | Explain the valid binding has no current eligible users; let the user revise criteria or use a generic form. |
| Readiness: query error | Stop data-dependent work. Do not convert the error into zero users or a generic fallback decision. |
| Capability field/operator absent | Correct the population using only available semantics or report onboarding need; never send raw SQL/drop conditions. |
| Ambiguous Idea/Research create | Reuse the identical request and idempotency key or exact readback. Never select an arbitrary similarly named row. |
| Research slot already bound | Continue the exact bound resource when intended; otherwise create a deliberate new Research/key. Never overwrite it. |
| Typeform payload rejected (`typeform_choices_empty`, `typeform_choice_invalid`, `typeform_payload_rejected`) | The form was not created. Correct the native body using the returned JSON-pointer `field` (no tokens or full questionnaire). Retry the same Research. Empty choice lists and `choices` on non-choice types are payload errors, not “maybe created”. Do not mint a replacement Research or form. |
| Ambiguous Typeform create/readback (`research_form_reconcile_required`, timeout/5xx) | Read status and replay only the identical key/body (or attach `source_research_id` / `form_url`) for reconciliation. Do not create another form. |
| Typeform publish failed (`typeform_publish_failed`, `typeform_publish_unconfirmed`) | The form was not confirmed public. Retry `personal_research_journey_form_publish` on the same Research. Do not create a replacement form or tell the user publish is unsupported. |
| Publish without a bound form (`research_form_binding_required`) | Create or attach the Typeform first, then publish that same Research. |
| Form attach source unbound or drifted | Stop. Use a same-Project Research that already has a matching Typeform binding, paste an owned display URL, or create a new form with `body`. |
| Pasted Typeform URL is not on the platform account | Stop. Send the display URL through Form `form_url`; do not call Typeform or invent `form_id`. Use `body` or a same-Project `source_research_id` instead. |
| Campaign reuse leftover source URL | Stop. Recovered copy must become one current-Research CTA; do not keep the source `research_id`/batch href. |
| Cross-Project or missing `source_research_id` | Stop. Reuse is same-Project only; send a native `body` or an owned display `form_url` when the source is absent. |
| Form definition drift | Stop materialization/results mapping. Re-read the exact form and start a deliberate new revision/Research when structure changed. |
| Selection stale/foreign/count mismatch | Re-read current capability and prepare a new same-Project selection. Do not coerce IDs or counts. |
| Materialization queued/running | Poll the same Research. Do not use a latest warehouse run or submit another request. |
| Materialization failed/reconcile required | Preserve the request ID and safe error. Reconcile the exact request; a new attempt needs corrected current evidence. |
| Link page missing/mismatched | Re-read exact form/batch receipt. Never build `uid` or URL locally. |
| Research Brevo target/revision changed | Refresh readiness/status and submit a new deliberate request without `destination_id`; Platform owns target selection. |
| Brevo sync partial/failed | Report aggregate added/removed/skipped and safe reasons. Do not expose contacts or create the Draft as if sync succeeded. |
| Contact ownership conflict | Preserve the other Research's URL and report conflict; never overwrite its pending invitation attribute. |
| Ambiguous Brevo sync | Reconcile the exact request/receipt. Do not replay with a new key or create a second List. |
| Campaign requested before successful sync | Stop before provider I/O. Bind the exact successful Brevo receipt for the same form/batch/List. |
| Ambiguous Campaign create | Read/reconcile the persisted campaign ID and replay only the identical key/`body` or the same `source_research_id` when the API contract allows it. Never create a replacement. |
| Form/Draft sources mixed | Stop. Form sends exactly one of `body`, `source_research_id`, or `form_url`. Campaign Draft sends exactly one of `body` or `source_research_id` and must not send `form_url`. Do not POST a second form or Draft to “fix” the 422. |
| Draft readback is not `draft`, List mismatches, or URL variable missing | Do not report completion. Correct only through the exact bound Draft operation after authoritative readback. |
| Provider URL missing/invalid | Preserve resource success if independently proven, but do not construct or display a console URL. |
| Actor search returns no suitable candidate | Report the searched channel/query as unavailable. Do not relabel another Actor or use a fixed fallback Actor. |
| Actor detail is private/deprecated or input schema is unavailable | Stop before start. Do not guess provider input fields. |
| Actor input violates schema `required`, `minimum`, `maximum` or `enum` | Stop before start. Do not shrink a minimum to collect a smaller sample. `example_input` is not an executable default. |
| Native start returns `apify_invalid_input` | The provider rejected the input and did not create a run. Correct the input from the schema and replay the same idempotency key. Do not treat this as an ambiguous paid start and do not mint another key. |
| Native start acknowledgement is ambiguous (`apify_start_outcome_ambiguous`) | Read the same Project request using its stable idempotency-derived identity. Do not start another paid run. |
| Native run is non-terminal | Poll only its exact Project request. Dataset/report work remains incomplete. |
| Native exact read `5xx` / `http_error` after a known start | Keep the `request_id`. Page native discovery for the same Idea and report the matching `voc_id` status. Do not start another paid run. Dataset/report stay incomplete until a terminal owned Dataset is proven. |
| Run or Dataset is not bound to the exact Idea/VOC | Stop before provider read. Never use an arbitrary Dataset ID from text or a provider URL. |
| Dataset page changes or pagination is incomplete | Preserve exact Dataset identity and disclose the pages/items actually read; do not claim full coverage. Do not author or publish while `has_more=true`. |
| Dataset/report attachment content type is unexpected | Reject the download and leave the destination absent. Do not print or reinterpret bytes. |
| Attachment `--output` is missing, absolute, contains `..`, or `AUDIENCE_ATTACHMENT_DIR` is unset/not a real directory | Stop before write. Use a host-injected absolute sink and a filename inside it. Do not write to cwd, home, or a caller-chosen path. |
| Native VOC report path is outside the approved output directory, or is a directory, symlink or FIFO (`invalid_native_voc_report`) | Stop before upload. Point `USER_RESEARCH_NATIVE_VOC_REPORT_PATH` at a regular Markdown file directly inside `USER_RESEARCH_NATIVE_VOC_OUTPUT_DIR`. |
| Native VOC report missing original voice text | Do not publish a report-only body when the saved Dataset items contain a citable excerpt. Write 1–6 voices to `USER_RESEARCH_NATIVE_VOC_VOICES_PATH`, a regular file inside the output directory; each `text` must be an excerpt of those items, with a theme and a faithful zh-CN translation. Omit voices only when those items have no citable excerpt. `native_voc_voices_required` stops before upload. Do not submit `source_coverage`. `not_provided` is not a recovery for missing voices. Export download is optional and not required to publish. |
| Saved Dataset items no longer match the collect hash (`native_voc_items_changed`) | Stop before publish. Re-read the owned Dataset pages. Do not author voices from a replaced items file. |
| Chinese Platform report authored in another language without an explicit Human language request | Rewrite the Markdown in Simplified Chinese and republish the same source fingerprint. Evidence language does not choose the report language. |
| Response pagination snapshot conflict (`409`) | Restart at page one and use the new watermark/snapshot token on every later page. |
| `newer_responses=true` on current Research report | Keep and download that stored Markdown. Tell the user new responses arrived after publication. Do not auto-create a report request, auto-run an LLM, or publish a replacement until the user explicitly asks. Latest counts and rows remain on aggregate/CSV. |
| Response is `unmatched` | Keep it for answer-only analysis; never infer a user/profile from content, time or link shape. |
| Attachment download `transport_error` while the same Research JSON aggregate succeeded | Retry the identical Research, operation and output path as a read-only download. Use the attachment timeout up to 120s. Do not change IDs, file shape or create a replacement resource. |
| Other `503`/transport timeout on a read | Report temporary unavailability and retry the read later. |
| Timeout/outcome unknown after a write | Do exact status/readback only. Do not issue a replacement effect. |

Pending reads may repeat. A terminal failure, changed scope, changed count or changed destination requires current
evidence and a deliberate corrected request. Keep the original idempotency key for identical replay; use a new key
only for an intentionally different operation payload.

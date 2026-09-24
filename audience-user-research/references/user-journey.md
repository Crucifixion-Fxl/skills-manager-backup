# User journey

The current Project journey is maintained in [Project Typeform Research](typeform-research.md).

```text
self-context (four legacy fields; optional research_track)
  → optional VOC
  → Idea / Research
  → Typeform form
  → materialized_audience only: readiness + optional selection / materialization / personalized links
  → materialized_audience only: optional Brevo sync / unsent Campaign Draft

Typeform response ingestion
  → aggregate / paged responses / CSV
  → matched invitation profile or unmatched answer-only analysis

provider-native VOC (default when VOC is requested)
  → Actor search + detail + input schema
  → explicit native input + one idempotent start
  → exact Project request / Idea / VOC / run readback
  → owned Dataset metadata + paged items (optional CSV/JSONL export)
  → analysis of the items actually read
  → grounded Markdown publication + exact readback (optional report download)
```

This ordering expresses dependency, not a local workflow state machine. Questionnaire creation may stop independently.
Confirm VOC collection, Typeform create, audience materialization, and Brevo sync before those writes, and invite
optional extras such as channels and keywords; do not refuse because extras are missing.
A Project enters questionnaire_only only when self-context explicitly returns that track. A four-field legacy
self-context stays on the materialized_audience path, with exactly its returned Project and grants; never infer a
track or permission from its name or token. A questionnaire_only Project may create its own form or reuse a same-Project Research form; do not attach a bare
Typeform URL, select/materialize, sync Brevo, or create a Draft. It retains native VOC and unmatched responses.
A Project without warehouse users may use a generic form and retain unmatched responses. Response readback is
independent of Brevo and Draft creation. Exact Platform status decides how a resumed journey continues.
Native collection reaching a provider terminal state proves artifact availability only. Report completion requires
paging the exact owned Dataset items, publishing original voices and Markdown together without `source_coverage`, and reading back the
same report. CSV/JSONL export is optional. Actor and channel availability come from provider discovery rather than a static application list.

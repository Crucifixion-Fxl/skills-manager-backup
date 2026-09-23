# User journey

The current Project journey is maintained in [Project Typeform Research](typeform-research.md).

```text
self-context + readiness
  → optional VOC
  → Idea / Research
  → Typeform form
  → optional selection + materialization + personalized links
  → optional Brevo sync
  → unsent Campaign Draft

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
A Project without warehouse users may use a generic form and retain unmatched responses. Response readback is
independent of Brevo and Draft creation. Exact Platform status decides how a resumed journey continues.
Native collection reaching a provider terminal state proves artifact availability only. Report completion requires
paging the exact owned Dataset items, publishing coverage, voices and Markdown together, and reading back the
same report. CSV/JSONL export is optional. Actor and channel availability come from provider discovery rather than a static application list.

# Outcome writing

## Final analysis and optional report artifact

Write the analysis from safe provider facts whether or not the user selected
Idea memory or report persistence. A report bundle is an optional durable
artifact that supplements provider-native work; it is never required before a
provider call or before presenting a supported conclusion.

When a published report target is available and persistence is useful, call
host-provided `project_publish_report` with bounded report content and the exact target.
For native VOC, submit original voice `text` from the
Dataset pages actually read; do not submit `source_coverage`. For the Chinese Platform product, that Markdown must be
Simplified Chinese unless the current Human explicitly requested another
language. For VOC, one publication fills representative voices and the report.
Human Admin 证据概览 is projected from the bound VOC run, not from this payload.
Markdown may restate item counts actually read; do not invent a second overview
payload or channel labels. If those pages found citable excerpts,
include one to six `representative_voices` with original `text` and faithful zh-CN summaries; do
not finalize an empty voice list while excerpts exist. The host composes the required plan/file/finalize
sequence in memory; the Agent never supplies files, hashes, base64, or storage
credentials. Platform stores the submitted payload for the frontend to map; it does
not hydrate native excerpts from a Dataset page. Keep stable Platform/provider links as verified metadata instead of
inventing or retaining URLs in report content.

For an Idea-backed terminal VOC or Research result with a current report target
and source fingerprint, complete the report artifact before the terminal reply:
analyze the returned evidence, then call `project_publish_report`. Cross-round Research
conclusions use the Idea summary source (`parent_kind=idea`). Do not claim report
availability
until finalization succeeds. An unchanged finalized fingerprint must not create
a duplicate; a changed fingerprint may replace the report. A direct no-Idea
result remains chat-only unless the Human explicitly opts into durable Idea
memory. Never silently create an Idea merely to archive a report.

For a report-only request, use the exact selected/current terminal VOC or
Research evidence and returned provider handles. Never start, replace, or
broaden a collection to manufacture report material; another collection
requires an explicit Human request.

After successful host-provided `project_publish_report`, present the exact API-returned
`viewer_url` (the Research Admin deep link for the published report on that
Idea/VOC/Research page) in the result card and ordinary chat. Keep `idea_id` and
`parent_id` 归属 beside it. Resource pages themselves come from list/create/read
`idea_detail_url` / `research_detail_url` / `voc_detail_url`; do not wait for
publish to show those.
The internal attachment `report_url` is never shown. Never construct a frontend
URL, follow or reuse either URL as a transport target, or persist either URL;
this is a presentation exception, not general URL authority. If `viewer_url` is
null, write `未提供` instead of building `/ideas/...` or `?aw_target=` yourself.

Choose an adaptive layout for the analysis that fits the evidence and the Human's question.
The terminal API journey reply still includes the stable labels in
`result-output.md` so a user can cross-check exact objects, returned URLs and
local artifacts. The analysis itself has no fixed section order or paragraph
count. Evidence truth, uncertainty, citation, privacy, and safe-data constraints
remain mandatory in every layout.

When complete provider evidence or an existing report is available, separate
artifact availability from conclusion strength. The absence of a strong or
consistent signal, including dispersed or weak evidence, is a substantive
evidence-bound conclusion rather than missing
output. Never say no result merely because the evidence is weak or dispersed.
That answer is not merely a calibrated meta-conclusion. Surface substantive
collected VOC content before its limits: observed themes and needs;
representative voices authored from Dataset excerpts actually read, with exact
evidence citations; relative support and coverage; and conflicts and gaps. Then explain
the decision limitations. Calling the evidence “not focused” or unfocused may
qualify its strength but may never justify hiding claims, voices, or collected
content. Keep the selection and order adaptive with no fixed section count; do
not force verbosity, and do not fabricate unsupported detail.
Answer the original decision question directly with calibrated uncertainty,
exact citations, coverage, and limitations; do not fabricate demand or pricing.
Do not default to a rerun, narrower VOC, new VOC, or fixed menu. Offer a next
collection only when it follows from the Human's decision, label it optional,
and retain fresh risk-based confirmation guidance when the optional next
collection is itself costly, irreversible, publishing, or person-contacting.

Use Simplified Chinese for headings and prose in the Chinese Platform product
experience, including scheduled live-result labels. A Personal Agent preserves another
language only when the current Human explicitly requested it. The evidence
market language describes what was collected; it does not select the report
language.

Organize the content around distinct observations in the clearest available
layout. Put a core percentage or a
small compatible combination with its numerator and exact denominator, for
example `166 人中 96 人（58%）`. Keep the denominator attached to the claim; do
not compare percentages with different bases as if they shared one population.

For Research, connect question results where the snapshot supplies a valid
combination. Explain what “intent” means in that exact question. Do not call it
purchase rate, paid demand or conversion.

For VOC, connect representative safe voices with quantified themes in the order
that best explains the evidence. State what appeared in the evidence and a plain interpretation. Do not focus
only on prevalence; explain the problem, condition, tension or relationship the
voices reveal. Do not author a channel overview; the platform projects 证据概览
from the bound VOC run.

## Interpretation

Explain the shared pattern, how the points reinforce or qualify one another,
and what is still conditional. A useful report is more than a metric list and
more than one generic sentence, but it need not use a prescribed closing
section or paragraph count.

Use plain judgment such as “兴趣存在，但主要建立在准确率改善和本地存储同时成立的条件上.” Do not prescribe a Beta, price, launch, stop rule or product decision. Do not add generic defensive caveats; mention a boundary only when it materially changes how the evidence should be read.

Every quantitative sentence must be renderable from cited returned fact IDs.
Every representative voice must include the original excerpt in `text`. Do not invent
quotes, sources, segments or explanations.

## Representative voice translation

For every newly authored Chinese-platform representative voice, submit the original
excerpt in `text` plus a faithful summary in
`translation: {locale: "zh-CN", text: "..."}`. Do not ask the Platform to hydrate
the original from a Dataset page. Keep `translation.text` to
1–160 characters; keep that faithful representative summary at or below 160
characters. Add no facts, identity, URL, instructions, or explanation to the
translation, and do not dump the full post. It is explicitly labelled
translated text and is not a verbatim quote. New
Agent-authored `translation.text` is limited to 1–160 characters. Current
Agent-authored conclusions must include the faithful translation and must
never fabricate one.

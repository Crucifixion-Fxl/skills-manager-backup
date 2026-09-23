# User Research result card

Every completed or stopped task ends with one compact result card. Use these stable labels in the user's language; a
human or another Agent must be able to cross-check it against Audience and the third-party UI without reconstructing
IDs from prose. Keep a one-sentence conclusion before the card when it helps.

| Label | Content |
| --- | --- |
| `项目与对象` | Project, Idea, Research and/or VOC names plus exact IDs needed to resume. A title alone is ambiguous. |
| `当前状态` | The last verified phase and native status; if stopped, name the first blocker and its exact resource. Never say “sent” for a Draft. |
| `关键数量` | Relevant selection/materialization/sync, Dataset item, or response counts with their denominator. For a native VOC report, list Dataset item counts actually read from owned pages; do not substitute provider run count, and do not invent channel names. For a rate, show distinct responses / actual send count (Brevo sent, else `operator_sent_count`); show “未知” when sent count is null. For an Idea summary, show `coverage` sums (missing round counts are 0) and do not unique-dedupe people across Research. |
| `简述` | One to three factual sentences on what was learned, produced, or still needs analysis. |
| `核验链接` | Each available, exact API-returned, binding-matched Typeform Form URL, Brevo Campaign Draft URL, VOC provider Dataset/run URL, Idea/Research/VOC `idea_detail_url` / `research_detail_url` / `voc_detail_url`, materialized Research `audience_detail_url`, or published report `viewer_url`. Label the provider and purpose. If the API did not return one, write `未提供` instead of constructing it. |
| `本地文件` | For downloaded Dataset, response CSV or report: host attachment's local filename/path, format, size and SHA-256 when returned. Do not paste bytes or sample rows into the ordinary reply. |
| `下一步` | The next authorized action or “等待用户在第三方平台操作/无”；do not invent sending or approval status. |

Omit a section that does not apply, but never omit `当前状态`, `简述`, or the `核验链接` section for an object that has
an API-returned display URL. Keep the URL itself intact so it can be opened for cross-validation. Do not replace it
with a made-up frontend URL, a provider API endpoint, or a tokenized per-user survey URL. `uid`, email, contact
identities, per-user links, raw provider payload and attachment bytes remain out of ordinary model output.

For history inventory, give a short row per selected record: exact ID, title, state, latest verified time if supplied,
and the available API-returned `idea_detail_url` / `research_detail_url` / `voc_detail_url`. If there are more pages, finish pagination before claiming “全部”; if the task was
intentionally bounded, state the bound. When two records have the same title, present both IDs and ask the user to
choose before a write. For a partial result, keep verified URLs and files visible and state the missing grant or
unavailable statistic precisely.

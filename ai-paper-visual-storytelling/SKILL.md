---
name: ai-paper-visual-storytelling
description: Use when turning AI paper drafts, method notes, screenshots, demo videos, UI states, or use-case descriptions into reviewable HTML/SVG figures.
---

# ai-paper-visual-storytelling

Turn AI paper ideas, drafts, screenshots, and demo recordings into factual, editable HTML/SVG figures. The output is a figure artifact, not a marketing page: every panel should help a paper reader understand the contribution, method, use case, or qualitative behavior.

Detailed rules:

- [Figure patterns](references/figure-patterns.md)
- [HTML/SVG artifact contract](references/html-svg-contract.md)
- [Media asset workflow](references/media-asset-workflow.md)
- [AutoFigure main-figure candidates](references/autofigure-backend.md)

## Description

Use this skill for AI, ML, LLM, agent, benchmark, system, UI, and HCI papers. Do not use it for biomedical mechanism drawings or raw data plots that should be generated from analysis code.

Accepted inputs include:

- Paper title, abstract, method, results, related work, or full draft.
- Short natural-language idea or use-case description.
- Screenshots, UI states, logs, traces, model outputs, or qualitative cases.
- Demo videos or screen recordings that need key-frame extraction.

Default output:

- A self-contained HTML file with inline CSS and SVG.
- A separate visual brief JSON file next to the HTML, so reviewers can inspect story spine, figure role, selected context, panel plan, caption suggestion, and open assumptions.
- Optional local raster assets only when screenshots or video frames are source evidence.

## Rules

1. No visual brief, no drawing. The brief must include exactly these seven fields: `story_spine`, `figure_role`, `selected_context`, `panels`, `caption_suggestion`, `caption_outside_figure`, and `open_assumptions`.
2. If the user prompt does not make the story spine clear, ask 1-3 Socratic questions before rendering. Clarify what the figure should help the reader understand, which paper claim it supports, and where it will appear.
3. Treat selected context as the factual boundary for every figure. It can include paper excerpts, user-provided assertions, screenshots, extracted frames, logs, traces, or agent-created evidence assets; every important panel claim should bind to selected context by ID.
4. Prefer editable structured SVG elements for diagrams. Use screenshots and video frames as evidence panels, not as a full rasterized replacement for the figure.
5. Map every important node, arrow, label, and claim to source text, screenshot evidence, video timestamp, or an explicit user-provided assumption.
6. For videos, extract a contact sheet first, then choose frames. Do not summarize unseen video content from filename or title alone.
7. Use reference figures or PDFs only when the user provides them or explicitly names the path. Do not search project folders, downloads, paper directories, or previous artifacts for visual references; stray figures can contaminate content and layout.
8. Keep the final paper caption outside the figure. Put caption suggestions in the separate visual brief; the paper manuscript owns the real caption.
9. Keep the artifact reviewable: clear panel IDs, visible labels, accessible SVG labels, no page header or hero text, avoid unnecessary external dependencies, no hidden prompt text, and no decorative complexity that weakens the paper story.
10. For main mechanism figures, ask whether to use AutoFigure for main-figure candidates after the visual brief is written. If the user says yes, run both text-to-figure and paper-to-figure from the same brief and paper file; if either input or setup is unavailable, ask for it or continue with direct HTML/SVG.

## Execution Flow

1. **Collect inputs and figure role**: use `references/figure-patterns.md` to identify the paper job and figure role, not the final layout. Decide whether the task is paper overview, method diagram, use case, screenshot annotation, video storyboard, taxonomy, comparison, or qualitative example.
2. **Clarify the story spine**: if the figure goal or takeaway is unclear, ask concise Socratic questions before drawing.
3. **Select context**: decide which paper excerpts, assertions, screenshots, frames, logs, traces, or derived assets bound the figure; record them in `selected_context` with stable IDs.
4. **Inspect source evidence**: inspect text, screenshots, videos, UI traces, logs, or reference figures supplied by the user or explicitly named by path; follow `references/media-asset-workflow.md` for media-specific handling and record selected evidence in the brief.
5. **Write the visual brief**: capture only the seven required top-level fields: story spine, figure role, selected context, panel plan, caption suggestion, caption outside the figure, and open assumptions. Put source metadata only inside `selected_context` or `panels` when it is needed to support a visible claim.
6. **AutoFigure decision for main figures**: if the role is a main mechanism figure and the story spine is confirmed, ask whether to use AutoFigure for the main figure. If the user agrees, follow `references/autofigure-backend.md` and generate both text-to-figure and paper-to-figure candidates; otherwise continue with direct HTML/SVG.
7. **Compose the layout**: return to `references/figure-patterns.md` to translate the confirmed story into claim-bearing units, relations, reading path, and macro regions.
8. **Prepare final assets**: after the layout is fixed, create only the assets required by the panel plan, such as crops, selected frames, derived evidence assets, simplified icons, mock UI fragments, or local redraws. Record only the source filenames, timestamps, evidence status, or edit notes that are needed to justify visible claims, and keep them nested under the relevant context or panel entries.
9. **Render HTML/SVG**: follow `references/html-svg-contract.md`; keep diagrams editable and screenshots traceable.
10. **Review and refine**: check factual faithfulness, visual hierarchy, caption alignment, export readiness, and whether the figure can be understood in five seconds.
11. **Validate**: run the bundled validator from this skill's installed directory: `python3 <this-skill-dir>/scripts/validate_figure_html.py <html-file> --brief <brief-json>` before delivery.

## Examples

### Bad

```text
Input: "We propose an agent for spreadsheet QA."
Output: A generic four-card flowchart: User -> AI -> Tool -> Result.
Problem: no paper claim, no use-case surface, no evidence, and no reader takeaway.
```

### Good

```text
Input: abstract + method notes + a screen recording of the spreadsheet QA workflow.
Output: HTML/SVG figure with:
  Panel A: problem setup and actor roles.
  Panel B: agent pipeline with tool calls and verification loop.
  Panel C: three-frame storyboard from the demo recording with timestamped callouts.
  Panel D: concise qualitative outcome and limitation.
```

## Related Skills

- Use `work-method-demo-writer` when the user wants a product-style method demo page rather than a paper figure.
- Use `gitlab-pages-html` only when the final HTML figure artifact must be published as a shareable internal link.

## References

- AutoFigure: <https://github.com/ResearAI/AutoFigure>
- MarginDoc HTML diagram skills guide: <https://margindoc.dev/guides/html-diagram-skills>

# HTML/SVG Artifact Contract

The artifact is a reviewable paper figure source. It should open locally in a browser and be easy to export to SVG/PDF/PNG after review.

## File Shape

Use one self-contained HTML file by default:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Figure: short descriptive title</title>
  <style>/* inline CSS */</style>
</head>
<body>
  <main class="figure-sheet" aria-label="Paper figure source">
    <figure class="paper-figure" aria-label="Short descriptive figure summary">
      <section data-figure-panel="A">...</section>
      <section data-figure-panel="B">...</section>
    </figure>
  </main>
</body>
</html>
```

Keep the visual brief in a separate JSON file next to the HTML. The HTML is the figure source, not the planning record.

## Visual Brief JSON Shape

The separate brief JSON must use exactly seven top-level fields:

```json
{
  "story_spine": "One concise sentence or short bullet list for the figure claim.",
  "figure_role": "Main mechanism figure",
  "selected_context": [
    {
      "id": "C1",
      "source": "paper excerpt, user assertion, screenshot, frame, log, or trace",
      "content": "The factual evidence this figure may use."
    }
  ],
  "panels": [
    {
      "id": "A",
      "purpose": "What this panel explains or proves.",
      "context_ids": ["C1"],
      "claims": [
        {
          "text": "Visible claim or relation in this panel.",
          "context_ids": ["C1"]
        }
      ]
    }
  ],
  "caption_suggestion": "Draft caption text for the manuscript, not rendered inside the figure.",
  "caption_outside_figure": true,
  "open_assumptions": []
}
```

Validation requirements:

- Do not add extra top-level fields.
- `selected_context` must be a non-empty list, and every item needs a stable `id`.
- `panels` must be a non-empty list. Every panel must bind to at least one selected context ID through panel-level `context_ids` or claim-level `context_ids`.
- Panel-level `context_ids` and claim-level `context_ids`, when present, must reference IDs from `selected_context`.
- `caption_outside_figure` must be `true`.
- `open_assumptions` must be a list.

## Page Chrome Rules

- Do not add page headers, hero sections, project titles, subtitles, navigation, explanatory paragraphs, or report-style intro copy around the figure.
- Use `<title>` only as browser metadata. Keep artifact intent, caption suggestions, and open assumptions inside the seven-field visual brief contract.
- Visible HTML/SVG text should belong to the figure itself: panel labels, short panel headings, axis labels, callouts, legends, source notes, or evidence identifiers.
- If the user explicitly asks for an in-figure title, make it a compact element inside the figure composition, not a page-level heading.

## Caption Rules

- Do not render the final paper caption inside SVG.
- Do not use SVG text blocks as manuscript captions.
- Panel labels, short panel headings, axis labels, and callouts are allowed inside SVG.
- Put caption drafts in the separate visual brief.
- The final caption belongs in LaTeX, Word, or the journal manuscript system.

## SVG Rules

- Use inline SVG for structured diagrams.
- Give each SVG `role="img"` and an `aria-label`.
- Use text labels as real SVG or HTML text, not baked into raster images.
- Keep panel groups named with `data-figure-panel` and visible labels: A, B, C.
- Use stable dimensions with `viewBox`; a `1000 600` or `1200 720` canvas works well for overview panels.
- Prefer a small, consistent palette. Reserve saturated colors for the main contribution path.
- Draw arrows and connectors before nodes so nodes mask line endings.
- Every arrow label needs an opaque mask rectangle behind the text.
- Put legends in a bottom strip or dedicated legend area, not floating inside the diagram area.
- Use a 4px grid for coordinates, node sizes, gaps, and font sizes where practical.
- Avoid shadows, glow effects, vertical text, pill-shaped type tags, and dark cyan/purple "AI dashboard" styling.

## Implementation Hints

These are implementation hints, not required templates:

- Use CSS variables for palette tokens so colors can be adjusted consistently.
- Define arrow markers once per SVG and reuse them.
- Use semantic group classes and `data-source` attributes for important nodes, arrows, and evidence callouts.
- Put an opaque mask behind arrow labels when lines cross text.
- Keep recurring primitives such as nodes, evidence cards, frames, and sequence markers visually consistent across panels.

## Screenshot and Frame Rules

- Store source evidence next to the HTML in an `assets/` directory unless the image is small enough to embed as a data URI.
- Label screenshot-derived panels with source filename or video timestamp in the visible figure or nearby source notes.
- Do not hide or alter UI states in a way that changes the claim. Crop, scale, blur sensitive details if needed, and record that edit in the brief.

## Export Readiness

Before delivery:

- No `TODO`, placeholder text, or lorem ipsum remains.
- The main figure remains readable at single-column and double-column paper widths.
- Captions and panel labels match the visible figure.
- `validate_figure_html.py` passes.

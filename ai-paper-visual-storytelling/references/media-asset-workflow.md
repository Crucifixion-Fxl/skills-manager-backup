# Media Asset Workflow

Use this reference when the input includes screenshots, screen recordings, demo videos, UI traces, model output logs, or user-provided reference figures.

Use it at two moments:

1. **Source inspection**, before the visual brief: inspect only user-provided or explicitly named media and record what can be used as evidence.
2. **Final asset preparation**, after the layout is fixed: create only the crops, frames, and derived assets needed by the panel plan.

## Screenshots

1. Preserve the original screenshot.
2. During source inspection, identify relevant regions without changing the original.
3. After the layout is fixed, create derived crops in `assets/derived/` only when needed.
4. When a derived asset supports a figure claim, record its source filename and edit note under `selected_context` or the relevant panel.
5. Use callouts to explain the paper claim. Do not rely on decorative overlays.

## Reference Figures

When an existing PDF or paper figure is used as a visual reference:

1. Use it only when the user provides the file or explicitly names the path.
2. Do not search local project folders, downloads, paper directories, or previous artifacts for visual references.
3. Treat it as layout inspiration unless the user explicitly asks to quote, compare, or annotate it.
4. Do not copy unsupported labels, claims, icons, or visual hierarchy into the new paper figure.
5. Record the reference filename, page, and figure number under `selected_context` when it materially influences the layout.
6. If a reference figure becomes visible evidence in the output, store it like a screenshot asset and cite its source in the brief.

## Demo Videos

1. Inspect metadata with `ffprobe` when available.
2. Generate candidate frames and a contact sheet:

```bash
python3 <this-skill-dir>/scripts/extract_video_frames.py demo.mp4 --out assets/demo-frames --every-sec 2 --contact-sheet
```

3. Select the smallest set of frames that tells the use case.
4. Record timestamps for selected frames that support visible claims.
5. Compose a storyboard with visible step numbers, callouts, and a claim tied to each frame.

## Frame Selection Heuristics

Prefer frames that show:

- The user's starting state.
- The system action or agent tool use.
- A meaningful intermediate state, error, or recovery.
- The final state that supports the figure claim.

Avoid frames that show:

- Loading spinners without meaning.
- Repeated states.
- Private data that cannot be safely redacted.
- UI states not visible in the source video.

## Logs and Traces

When logs or agent traces are source evidence:

- Quote only the minimal snippets needed for the figure.
- Convert long traces into a timeline or state-machine panel.
- Preserve identifiers needed for credibility, but redact secrets and personal data.

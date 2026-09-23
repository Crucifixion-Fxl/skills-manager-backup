# AutoFigure Main-Figure Candidates

For main mechanism figures, ask whether to use AutoFigure for the main figure after the visual brief is written. A "yes" answer means generate both text-to-figure and paper-to-figure candidates from the same brief and paper file. A "no" answer means continue with direct HTML/SVG.

Source: <https://github.com/ResearAI/AutoFigure>

## Runtime Check

The main skill flow decides when to ask about AutoFigure. When the user says yes, this reference handles setup checks and paired candidate generation.

Configured means:

- `autofigure-python` is available to future agent shells.
- `autofigure-python -c "import autofigure"` succeeds.
- The caller can provide the generation API key for the current run with `--generation-api-key-file`.

For AddX, pass these fixed generation parameters in the command:

- `--generation-base-url https://litellm.addx.live`
- `--generation-provider openrouter`
- `--generation-model gcp-a4xcloud-t/gemini-3.1-pro-preview`

Ask the user to provide the API key for the current run. Prefer `--generation-api-key-file <path>` with a token file outside the repo, and do not write the key to shell profiles, repo files, checked-in config, or the visual brief. `--generation-api-key <token>` remains available for compatibility but is not recommended because command arguments can appear in process listings or logs.

If AutoFigure is configured, run the candidate branch. If it is not configured, perform the one-time setup below as a separate user-approved environment task, then rerun the check. If setup cannot be completed, skip AutoFigure and continue with direct HTML/SVG.

## Candidate Branch

When the user says yes, generate candidates through this skill's wrapper script. Resolve `<this-skill-dir>` from the installed skill location, not from the current project directory. The wrapper always runs both text-to-figure and paper-to-figure, so `--paper` is required.

```bash
autofigure-python <this-skill-dir>/scripts/run_autofigure_drafts.py \
  --brief figure.brief.json \
  --paper paper.pdf \
  --out autofigure-drafts/ \
  --generation-api-key-file /path/to/per-run-token.txt \
  --generation-provider openrouter \
  --generation-base-url https://litellm.addx.live \
  --generation-model gcp-a4xcloud-t/gemini-3.1-pro-preview
```

This creates:

- `text-to-figure`: calls `AutoFigureAgent.generate()` with a compact constrained visual brief.
- `paper-to-figure`: calls `AutoFigureAgent.generate_from_paper()` with the real PDF or Markdown paper.
- `autofigure-drafts-manifest.json`: records generated paths, scores, and backend metadata.

Do not run AutoFigure with only one mode. If no paper file exists, ask the user for the paper path. If the paper cannot be provided, skip AutoFigure and continue with direct HTML/SVG.

Do not treat text-to-figure as ordinary one-line prompting. In this skill, text-to-figure receives a compact visual brief with `story_spine`, `figure_role`, and `selected_context`; paper-to-figure receives the real PDF or Markdown paper.

Prefer `--output-format svg`. Use `--output-format mxgraphxml` only when the user wants draw.io editing. Keep enhancement off by default because enhanced raster variants can look better while reducing editability and traceability.

The wrapper accepts these AutoFigure parameters directly:

| Parameter | Meaning |
| --- | --- |
| `--generation-api-key-file` | Path to a per-run token file outside the repo; preferred because the secret is not placed in argv |
| `--generation-api-key` | Generation API token passed to AutoFigure; compatibility path, not recommended for shared shells or logged runs |
| `--generation-base-url` | OpenAI-compatible endpoint; for AddX use `https://litellm.addx.live` |
| `--generation-provider` | AutoFigure provider name: `openrouter`, `bianxie`, or `gemini`; use `openrouter` for AddX LiteLLM |
| `--generation-model` | Generation model; for AddX use `gcp-a4xcloud-t/gemini-3.1-pro-preview` |

Do not pass `--generation-provider litellm`; AutoFigure does not define `litellm` as a provider. LiteLLM is the OpenAI-compatible endpoint behind the `openrouter` provider route.

## One-Time Setup

Do not clone or install AutoFigure during normal skill use. Setup is a separate, user-approved environment task that should be done once per machine or agent runtime. Rerun it only when the user changes the AutoFigure checkout, upgrades AutoFigure, rebuilds the venv, or changes persistent runtime configuration.

Setup logic:

1. Ask the user for the AutoFigure checkout path, or use an existing checkout they provide.
2. Derive the virtual environment from that checkout, for example `<autofigure-checkout>/.venv` or a sibling path chosen by the user.
3. Install AutoFigure into that venv according to the selected checkout's official files and the modes this skill will use:
   - Core SDK: Python 3.8+ and editable installation of the checkout.
   - Paper-to-figure: PDF dependencies such as `pymupdf` and `pdfplumber`.
   - Rendering/preview: Playwright plus Chromium.
   - Enhancement: enhancement/full dependencies only if the user explicitly enables enhancement.
   Check that checkout's `README.md`, `pyproject.toml`, and `requirements.txt` before installing; those files are the source of truth if upstream changes.
4. Create a permanent command named `autofigure-python` that launches that venv's Python.
5. Persist only `autofigure-python` on `PATH`. Keep the fixed AddX generation parameters in this reference and pass them on each command. Do not persist the API key.

After choosing the persistence mechanism, verify from a fresh agent shell:

```bash
autofigure-python <this-skill-dir>/scripts/run_autofigure_drafts.py --help
```

If AutoFigure is unavailable, skip it and continue with direct HTML/SVG.

## Candidate Selection

After AutoFigure returns:

1. Show text-to-figure and paper-to-figure candidates to the user together.
2. If the user rejects all candidates, continue with direct HTML/SVG.
3. If the user accepts one candidate, treat its SVG or mxGraph output as an input asset, similar to user-provided media.
4. Preserve the selected candidate's SVG or mxGraph path and manifest path only when they become selected context for the final figure. Keep scores and backend details in `autofigure-drafts-manifest.json`.
5. Return to the main skill flow at layout composition and final HTML/SVG rendering. The accepted candidate may influence composition, but the visual brief, selected context, and panel-level evidence binding remain the source of truth.

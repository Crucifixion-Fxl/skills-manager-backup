# Visual Demo Patterns

Use this reference when designing diagrams, mock interfaces, animations, and tool screenshots.

## Universal Rule

A demo must look like the real working environment for that method, not a generic flowchart.

If the method happens in a terminal, show realistic terminal output. If it happens in an IDE, show realistic IDE panels. If it happens in CRM, spreadsheets, dashboards, design tools, browsers, GitLab, Slack, Lark, or review tools, recreate that surface closely enough that readers understand what they would actually see and do.

## Realistic Surfaces

Choose the surface based on where the work actually happens:

| Work surface | Demo should show |
| --- | --- |
| Terminal / CLI | Prompt, command, streaming output, tool calls, status, errors |
| Claude Code / Codex | Plan updates, todo updates, read/edit/bash/search output, human prompt |
| IDE | Explorer, tabs, diff, source control, extensions, context menus |
| GitLab / GitHub | Issue, branch, MR/PR, review comments, pipeline state |
| Browser app | URL bar, page content, side panel, comment layer, loading state |
| Dashboard | Filters, metrics, charts, selected row, alert state |
| Spreadsheet | Sheet grid, selected cell, formula, side notes, data validation |
| Chat / Lark / Slack | Thread, mentions, replies, resolved/unresolved state |
| Design tool | Frames, layers, comments, selected component, inspector |

## Animation Rules

Use animation to explain state changes, not to decorate.

Good animation:

- split pane opens exactly when the shortcut appears
- a task moves from main context to isolated context
- a review comment becomes a CLI task
- a worktree opens into an IDE window
- a status moves from pending to complete

Bad animation:

- particles that do not map to a workflow decision
- abstract cards moving without real tool surfaces
- steps that do not align with the visible demo
- UI that looks like a generic SaaS dashboard when the real work happens in terminal

## Human Input

Always show what the human says or does:

- terminal prompt
- command
- form input
- right-click menu
- review comment
- button click
- “clean up session” instruction

Avoid hiding the human behind labels such as “user action”.

## AI / System Feedback

Show the response in the style of the real tool:

- Claude Code: `⏺`, `● Update Todos`, `● Read(...)`, `● Edit(...)`, `● Bash(...)`, `⎿`
- Codex CLI: concise plan/status/final lines
- GitLab: issue/MR labels, branch, pipeline, approval, comments
- IDE: diff hunks, source control, worktree list, context menu

## Step Alignment

If a visible step strip exists, each step must align with a visible object or state:

1. Step label appears near or above the relevant stage.
2. The visual object changes when the step becomes active.
3. Cleanup and review happen in the same surface where the method says they happen.
4. Do not create separate cards for actions that happen inside a terminal, issue, IDE, or browser.

## Anti-Patterns

- Pure flowchart for a practical tool workflow
- Cards labeled “AI does X” with no real output
- Fake terminal output that looks like app log lines instead of agent CLI
- A cleanup card outside the session when the cleanup is a human prompt inside that session
- Splitting work just to show more agents, without a clear independence rule

# Workflow Method Checklist

Use this checklist before delivering a work-method HTML document.

## Story

- [ ] Hero states a clear result, offer, or product-like claim.
- [ ] Motivation appears before tool mechanics.
- [ ] The hidden cost is explicit: waiting, context pollution, review cost, coordination cost, or operational risk.
- [ ] The method has one crisp decision rule.
- [ ] The page explains when not to use the method.

## Workflow

- [ ] The lifecycle is complete: input, decision, execution, review, publish/collaborate, cleanup.
- [ ] Each lifecycle step shows what the human does.
- [ ] Each lifecycle step shows what the tool/AI/system returns.
- [ ] Handoff points are visible.
- [ ] Cleanup is part of the workflow, not an afterthought.

## Demo Realism

- [ ] Every main demo uses a realistic surface from the actual work environment.
- [ ] Terminal output looks like real CLI or agent output.
- [ ] IDE, browser, GitLab, dashboard, or chat mocks show recognizable panels and states.
- [ ] Flowcharts only support the explanation and do not replace the demo.
- [ ] Animations map to workflow state changes.

## Reviewability

- [ ] The output artifact can be reviewed by a human.
- [ ] Changes are isolated enough to inspect.
- [ ] The page includes preview/review or publish/collaboration path when relevant.
- [ ] Reviewer comments have a path back into the workflow.

## Share Card

- [ ] Page has `meta name="description"`.
- [ ] Page has `og:title`, `og:description`, `og:type`, and `og:image`.
- [ ] Page has `twitter:card`, `twitter:title`, `twitter:description`, and `twitter:image`.
- [ ] OG title and description reuse the product claim and motivation.
- [ ] OG image exists at the referenced path; use an absolute URL before public publishing when required.

## HTML Quality

- [ ] Text fits on desktop and mobile.
- [ ] No overlapping UI elements.
- [ ] Step labels match visible demo states.
- [ ] Motion can be paused or is non-disruptive.
- [ ] `python3 -m html.parser` passes.
- [ ] `git diff --check` passes when editing inside a git repo.

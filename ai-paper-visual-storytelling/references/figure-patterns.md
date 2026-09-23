# Figure Patterns

Use Figure Roles early to identify the paper job. Use Story To Composition only after the visual brief has a clear story spine. Do not start by choosing a basic diagram type; first compile the paper story into a visual composition, then design local regions after the macro layout is fixed.

Core principle:

```text
story spine -> claim-bearing units -> composition move -> reading path -> macro regions -> local visual treatments -> evidence binding
```

## Figure Roles

Use the role only to understand the paper job the figure must do. The role is not the layout.

| Paper job | Typical location | What the figure must prove or explain |
| --- | --- | --- |
| Main mechanism figure | Introduction or method | What the system/method is and how the core mechanism works |
| Contribution highlight | Introduction | Why the contribution is different and worth reading |
| Qualitative or use-case evidence | Results, case study, appendix | What happens in a concrete task, trace, or example |
| Paradigm comparison | Introduction, related work, method | How the proposed framing differs from prior workflows or baselines |
| Evaluation or protocol figure | Dataset, benchmark, evaluation | How tasks, data, scoring, or experiment flow are constructed |

## Story To Composition

### 1. Frame the figure premise

Summarize the current intent as a compact figure premise. A single sentence is useful when possible, but two or three short bullets are better when the figure has multiple panels or evidence types.

```text
This figure shows that [main claim] by showing [mechanism/evidence/contrast].
```

If the premise is vague, do not draw. Ask for a sharper story spine.

### 2. Extract claim-bearing units

List only the units that must appear for the premise to be true.

| Unit role | Examples | Drawing implication |
| --- | --- | --- |
| Problem setup | user, task, input, environment, failure mode | Usually left/top context; visually lighter than the contribution |
| Proposed mechanism | module, agent, retrieval step, training/eval process | Usually central or on the main reading path |
| Evidence object | screenshot, frame, case, example output, retrieved document | Must keep source labels or timestamps traceable |
| Decision or adaptation | controller, reranker, verifier, planner, gate | Needs visible condition, loop, or turn marker |
| Contrast target | previous paradigm, baseline family, ablated path | Must share comparable axes with the proposed path |
| Boundary or constraint | sandbox, context window, modality, memory tier, evaluation scope | Often drawn as band, enclosure, tier, or label |
| Outcome | answer, score, accepted edit, recovered state, limitation | Usually right/bottom; connect to the claim-bearing evidence |

### 3. Choose the composition move and reading path

Choose the story-to-layout move that makes the paper claim visible. The move determines the dominant relation between units; the reading path determines how the reader traverses it.

| Story need | Composition move | Natural reading path | Evidence requirement |
| --- | --- | --- | --- |
| Show a new method end to end | Context -> mechanism -> evidence/output | Left to right or top to bottom | Method excerpt plus exact terms for modules and arrows |
| Show adaptive agent behavior | Controller plus numbered decisions over a concrete trace | Top to bottom trace or center outward controller | Prompt, trace, logs, screenshots, or user-provided turn sequence |
| Show multimodal or multi-source grounding | Parallel evidence lanes converging on an answer | Evidence to answer | Source labels for each modality or memory store |
| Show contribution over prior work | Matched old/new panels with one emphasized difference | Split comparison | Related-work claim or user-provided comparison basis |
| Show qualitative result | Concrete episode storyboard with callouts | Temporal trace or evidence to answer | Input/output pair, frame, screenshot, or example text |
| Show evaluation protocol | Data/task -> system -> scorer -> result artifact | Left to right or top to bottom | Dataset/task definition and scoring rule |
| Show a limitation or guardrail | Main path plus constrained boundary or failure branch | Main path with visible branch | Explicit limitation text or observed failure case |

Pick one primary reading path and enforce it with spatial order, arrows, panel labels, and visual weight.

| Reading path | Use when | Layout rule |
| --- | --- | --- |
| Left to right | Method transforms input into output | Put the contribution near the middle, not only at the end |
| Top to bottom | Protocol, stack, or turn sequence | Keep each row visually parallel |
| Center outward | One core idea coordinates surrounding evidence | Put the core mechanism in the center and make satellites lighter |
| Split comparison | The claim is a difference from prior work | Use matched inputs, outputs, and labels on both sides |
| Evidence to answer | The answer is grounded in retrieved or observed evidence | Keep evidence IDs visible until the final claim |
| Temporal trace | Events unfold over turns, frames, or cases | Keep the sequence compact, numbered, and tied to concrete evidence |
| Main path with visible branch | A normal path must show a guardrail, failure, or limitation | Keep the branch visually lighter and label the condition that triggers it |

Formal diagram grammars such as architecture, flowchart, sequence, state machine, timeline, swimlane, matrix, tree, and layer stack are available vocabulary, not a required catalog. If the whole figure truly fits one grammar, use that grammar as the macro composition rather than forcing a custom layout. For example, a full agent/tool trace can be a sequence, a benchmark category figure can be a tree, and a paradigm comparison can be a matrix. The grammar still needs the visual brief, selected context, and panel-level evidence binding.

### 4. Allocate macro regions

Before drawing details, reserve 2-4 regions with a purpose for each.

Good region purposes:

- Context: problem, input, task, or prior paradigm.
- Mechanism: the proposed method or decision process.
- Evidence: retrieved data, screenshots, examples, or qualitative cases.
- Outcome: answer, result, supported claim, or limitation.

The claim-bearing region should be visually dominant. Supporting context can be smaller, muted, or grouped.

### 5. Choose local visual treatments

After the macro layout is fixed, specify the simplest representation for each region so its relation is clear. Do not force local regions into named diagram grammars. Many paper-figure regions are custom compositions rather than sequence, swimlane, tree, or state-machine diagrams.

Use local treatments as starting points, not templates. For a local region, use a formal grammar only when the region's relation naturally fits it; otherwise use the simplest local treatment that preserves the story:

| Local relation | Example local treatments | Use a formal grammar only when |
| --- | --- | --- |
| Transformation | stage blocks, arrows, input/output cards, compact pipeline | The steps are ordered and each arrow has a supported meaning |
| Actor interaction | message arrows, numbered turns, tool-call bubbles | Actors and message order matter more than architecture |
| Ownership | lanes, grouped steps, responsibility labels | Every step has one clear owner |
| State change | status cards, lifecycle badges, transition arrows | States and transitions are explicit paper claims |
| Evidence episode | screenshot strip, frame sequence, callouts, before/after cards | Concrete frames or examples are available |
| Comparison | paired panels, shared axes, matrix cells, before/after lanes | Axes are explicit and defensible from source context |
| Hierarchy or containment | layers, tiers, nested regions, taxonomy blocks | Parent-child or level relationships are central to the claim |
| Provenance | source tags, evidence IDs, retrieval ledger, callout links | The reader must trace a claim back to specific evidence |

### Human checkpoints

Use human review at composition decisions, not only after rendering.

| Checkpoint | Ask the user to confirm | Continue when |
| --- | --- | --- |
| Before layout | figure premise, claim-bearing units, and selected context | The user agrees the intended claim is right |
| Before rendering | reading path, macro regions, local treatments, and evidence asset plan | The user agrees the composition matches the paper story |
| After first render | factual correctness, emphasis, missing evidence, and visual hierarchy | The user identifies only local edits or approves the direction |

Keep each checkpoint lightweight. Ask 1-3 concrete questions when user input would change the figure structure.

## Context Scope By Figure Role

Record the chosen context in the separate visual brief. `selected_context` is not limited to paper text; it can include user-provided assertions, screenshots, extracted frames, logs, traces, or derived assets created during the workflow. Give each item a stable ID so panels can reference it.

| Figure role | Minimum context | Expand context when |
| --- | --- | --- |
| Main mechanism figure | title, abstract, contribution list, method summary | The figure names modules, arrows, or claims not present in the summary |
| Contribution highlight | abstract, introduction motivation, contribution paragraph | The figure contrasts against prior work or claims novelty |
| Qualitative or use-case evidence | concrete example, screenshot/frame/log, adjacent claim text | The case is used to support a result or limitation |
| Paradigm comparison | related-work summary, baseline descriptions, proposed-method summary | Axes imply superiority, coverage, or novelty |
| Evaluation or protocol figure | dataset/task definition, evaluation setup, scoring rule | The figure includes metric interpretation or benchmark categories |

## Composition Checks

Run these checks before rendering:

- Can the figure premise be read from the layout without the caption?
- Which region carries the paper claim?
- Which visual element would be hard to justify from the selected context?
- Does the reading path have one dominant direction?
- Are local visual treatments serving the story instead of replacing it?
- Does every strong arrow imply only supported causality, chronology, or dependency?

## Readability Budget

Keep one panel readable in a paper column or slide crop.

| Limit | Default guideline |
| --- | --- |
| Macro regions | up to 4 |
| Claim-bearing units | up to 7 |
| Local groups per region | up to 4 |
| Nodes or items per local group | up to 9 |
| Connectors or transitions | up to 12 |
| Accent elements | up to 2 |
| Distinct visual encodings | up to 4 |
| Annotation callouts | up to 2 per evidence object |

If the visual exceeds the budget, split it into overview plus detail, or move detail into the manuscript caption.

---
name: root-cause-analysis
description: Use when triaging a problem Issue, investigating a failure or incident, debugging unexpected behavior, or making any causal or root-cause claim.
---

# Root Cause Analysis

Use one analysis methodology from initial problem framing through root-cause convergence. Do not create a second problem-analysis layer around this Skill.

## Modes

Select exactly one mode for the current attempt:

| Mode | Use when | Outcome |
|---|---|---|
| `ISSUE_TRIAGE` | A problem-class GitLab Issue needs evidence-bounded analysis before any fix or design | `problem-analysis/2.0` Artifact or `NEEDS_INPUT` |
| `ROOT_CAUSE_INVESTIGATION` | The user requests RCA, evidence collection has begun, or the output would make a causal claim | Structured investigation and gated conclusion |

For `ISSUE_TRIAGE`, read [the Problem Analysis contract](references/problem-analysis-contract.md) and [Issue routing rules](references/issue-analysis-routing.md) completely before producing a result. A managed, authenticated Issue trigger is sufficient to begin read-only triage; do not pause merely to ask whether analysis may start. Use `NEEDS_INPUT` only for one answerable fact that unlocks source, context, or admission.

Also apply [analysis value and evidence discipline](references/analysis-value.md) before collecting evidence and before finalizing an Issue result. Start from what the source already establishes and the next unresolved decision; translating or reorganizing an existing investigation is not new verification. Use the two source-only calibration cases to check output quality without claiming a live investigation.

When source or follow-up analysis calls for code inspection, apply
[code evidence execution](references/code-evidence-execution.md). Resolve the
actual read authority and execute available checks before delegating them back
to the user; a previous SOURCE_ONLY result does not freeze later authorization.
Before reading source in each repository, in every mode (interactive or managed),
verify rule files in the fixed commit's exact tree; a search miss is not absence.
Read same-tree symlink targets and applicable parent/nested rules under the
bounded safety checks in code evidence execution. Record rule paths/SHA and
blob/content proof; unresolved rules block that repository's source investigation.

Before sending any code-analysis result, reconcile the requested checks with
the evidence actually collected. Include a compact evidence appendix mapping
each material claim and requested check to fixed SHA, repository path and lines,
or to an explicit unread/blocked boundary. Finish available missing checks or
narrow the conclusion; a successful MR or test does not fill a missing check.
Keep identity layers, failure propagation and time-sensitive recovery unknowns
visible when they affect the decision. This applies to interim conclusions too,
not only a root-cause or recovery declaration.

Switch from `ISSUE_TRIAGE` to `ROOT_CAUSE_INVESTIGATION` before making any cause, root-cause, causal-chain, dominant-mechanism, or alternative-exclusion claim. This is a mode change inside the same Skill, not a second invocation or self-review. Record it as `methodology_proof.mode=ROOT_CAUSE_INVESTIGATION`.

For interactive debugging that is not a managed GitLab Issue, use `ROOT_CAUSE_INVESTIGATION` directly; the Artifact contract is optional unless a machine-readable problem Artifact is requested.

### Evidence readiness

A broad symptom summary without read implementation or incident observations supports intake and an evidence-collection plan, not hypothesis adjudication. Keep investigation branches open; do not populate later-phase hypothesis, necessary-prediction or falsifier tables merely because a methodology was selected. Before entering those tables, establish the specific mechanism, applicable conditions and observable evidence needed to distinguish it. Continue available authorized checks; if none are available, deliver the useful bounded analysis and concrete missing evidence without waiting for method approval or inventing a refutation.

## Description

The Skill prevents:

- Single-attribution bias (stopping at the first self-consistent narrative)
- Confirmation bias (only seeking supporting evidence, not disconfirming)
- Self-limiting investigation breadth (looking only at the service the alert fired on, not at causal neighbors)
- Using "no re-trigger" as a substitute for "metric converged to baseline"
- Narrative self-consistency without magnitude match (input scale insufficient to explain output scale)
- Premature final (confidently declaring "this time we got it" every round)

### Combine with domain skills

This Skill owns the analysis methodology. Select domain Skills such as `k8s-ops`, `sentry`, `grafana`, `aws-cli`, or `prometheus` only when their triggers match an actual evidence gap. Domain Skills provide tools and evidence; they do not define a competing analysis process.

## Rules

### Mode invariants

- `ISSUE_TRIAGE` never claims a cause. It may end as `TRIAGED` or `ANALYZED_NO_ROOT_CAUSE` without forcing a full RCA.
- `ROOT_CAUSE_CONFIRMED` requires `ROOT_CAUSE_INVESTIGATION` and every Phase 4 gate below.
- Facts, inferences, unknowns, uninspected sources, and blocked checks remain distinct.
- Repository paths, functions, logs, metrics, tests, and runtime behavior may be named only when actually read and recorded as evidence.
- Analysis does not authorize fixing code, running side-effecting tests, deployment, production action, Issue mutation, or notification.
- When separately authorized delivery uses a tool, verify the expected nonempty analysis in its returned content or canonical readback before claiming delivery. An acknowledgement alone is insufficient; preserve the receipt and reconcile absent, empty or uncertain content before any retry. This applies to every analysis mode and does not authorize another recipient or write.
- `addx:root-cause-analysis` is recorded once in `methodology_proof`; never generate a `skill_reviews` self-review record.

### Core Disciplines: 6 Enforced Principles

Each of the 6 principles below has a corresponding **mandatory output template** in Phase 2/3/4. Record unresolved fields explicitly with the missing information; an unknown is not a skipped check or permission to invent evidence. Incomplete checks block root-cause or recovery claims, not a bounded analysis that explains the gaps.

| # | Principle | Template | Violation symptom |
|---|---|---|---|
| 1 | Every testable hypothesis must have a valid falsifier; unscoped branches stay open | `Falsifier` column in the Phase 3 Hypothesis Tracking Table | A causal claim without a valid test, or an invented falsifier |
| 2 | At least 2 alternatives tracked in parallel; establish scope and causal layer before asserting mutual exclusion | Phase 3 Hypothesis Tracking Table must have ≥2 rows | Single hypothesis pursued serially, or coexisting mechanisms treated as mutually exclusive |
| 3 | Investigation must cover causal neighbors (not just the alert source) | Phase 2 Causal Neighbor Enumeration Table | Attention stuck on the surface symptom |
| 4 | Recovery must be declared via lagging indicator convergence to baseline | `Type` column of the Phase 4 Recovery Judgment Table | Using "no new alerts" instead of "metric back to baseline" |
| 5 | Root cause claim must pass magnitude + direction sanity check | Phase 4 Step 4.1 magnitude/direction reconciliation | Input magnitude insufficient to explain output, or expected vs actual direction contradict but the narrative is kept anyway |
| 6 | A Final Checklist must be passed before declaring "final" | Phase 4 Final Checklist | Confident "final" declaration with gaps |

**Why templates instead of "MUST" prose**: In free-form narrative, skipping a step is costless — if you don't write it, nobody notices. Structured templates turn omission into "visible empty fields", making discipline violations obvious. Both the reader and the author can see at a glance which step was skipped.

### `ROOT_CAUSE_INVESTIGATION` Phase 1: Intake

When the user describes a fault or says `/rca`:

**Extract 4 signal dimensions** from the description (ask if unclear):

| Dimension | Values |
|-----------|--------|
| Impact scope | Single service / multi-service / global |
| Fault pattern | Persistent / intermittent / recovered |
| Observability clues | Has metrics / logs / traces / none |
| Causal complexity | Single factor / multi-factor / cascading |

**Select methodology** based on signals:

| Signals | Methodology | Why |
|---------|-------------|-----|
| Production incident, rough direction known | Google SRE Troubleshooting | Systematic layer-by-layer elimination |
| Multi-factor, need to exhaust possible causes | Fault Tree Analysis | Structured causal tree with logic gates |
| Direction completely unclear, need divergence | Fishbone (Ishikawa) | Broad dimensional scan before focusing |
| Intermittent, hard to reproduce, fuzzy boundary | Kepner-Tregoe | IS/IS NOT boundary definition excels here |
| Cascading failure, multi-system interaction | STAMP/STPA | Control-theoretic view of system failures |

**Common combination paths** (suggest when signals are mixed):
- Fishbone → FTA: Diverge to find suspect dimensions, then build causal tree
- Kepner-Tregoe → SRE Troubleshooting: Define boundary first, then layer-by-layer
- SRE Troubleshooting → STAMP: When layer-by-layer reveals cross-boundary cascades
- Fishbone → Kepner-Tregoe: Multiple suspects found, use IS/IS NOT to narrow

Present your recommendation with a one-line rationale and continue within the existing authorization; methodology selection alone does not require confirmation. Ask only when missing information or an actual authorization boundary prevents the next investigation step, and continue independent authorized work where possible. A managed Issue workflow must preserve the selected method and evidence gaps in the Artifact; method selection never grants new access or write authority.

### `ROOT_CAUSE_INVESTIGATION` Phase 2: Investigation Plan

After selecting the methodology for `ROOT_CAUSE_INVESTIGATION`, read the corresponding methodology reference file from `references/` and perform the applicable steps supported by available evidence and existing authorization:
- `references/google-sre-troubleshooting.md`
- `references/fault-tree-analysis.md`
- `references/fishbone.md`
- `references/kepner-tregoe.md`
- `references/stamp-stpa.md`

#### Step 2.1 — Causal Neighbor Enumeration Table (mandatory, principle 3)

**Before collecting any evidence**, fill in the following table. The specific "neighbors" differ by system type (distributed service ≠ monolith ≠ embedded ≠ data pipeline); the investigator fills them in based on the system at hand. This skill does not ship a domain-specific checklist.

| Type | Neighbor (fill in concretely) | Sanity-checked | Abnormal signal (if any) |
|---|---|---|---|
| The entity itself | <the service / resource / component the alert fired on> | ⬜ | |
| Downstream dependencies | <what the entity calls: services / DBs / external APIs / MQs …> | ⬜ | |
| Upstream callers | <who calls the entity: upstream services / client types …> | ⬜ | |
| Shared infrastructure | <shared Redis / DB / LB / DNS / nodes / network …> | ⬜ | |
| Sensitive inputs | <recent deploys / config changes / cron jobs / traffic patterns / external events …> | ⬜ | |

These 5 categories are the **minimum coverage**. If the system has additional causal neighbors (upstream data sources for a pipeline, hardware peripherals for embedded, network environment for clients, etc.), add rows — but **never have fewer than these 5**.

**Gate to enter Phase 3**: reconcile each material neighbor with an actual sanity check or an evidence-backed NOT_APPLICABLE reason. Unknown and unread neighbors stay explicitly unchecked; they block causal convergence but do not block independent authorized evidence collection or a bounded result. Do not mark a row checked merely to complete the table.

**Why breadth first**: The classic failure mode of narrow investigation is "locking attention onto the place where the symptom surfaced and spinning there", while the real root cause often hides in a neighbor that was never even glanced at. Going broad first and then deep is far cheaper than going deep first and then backtracking for breadth — the latter typically ends with "spent two hours digging in one direction, only to find the root cause in a dependency I never looked at".

#### Step 2.2 — Investigation Plan

After filling in the neighbor table, output a numbered investigation plan following the methodology's framework. Each step includes:
- What to check
- What evidence to collect
- What a "complete" result looks like

The user can skip steps, reorder, or start executing immediately.

### `ROOT_CAUSE_INVESTIGATION` Phase 3: Step-by-step Follow-up

#### Hypothesis Tracking Table (mandatory, principles 1 + 2)

From the first step of Phase 3 onward, maintain this table and update it after every round of evidence.

| # | Hypothesis (one sentence) | Falsifier (what observation would disprove it) | Expected if true (specific metric + direction ↑/↓/flat) | Data source for verification | Actual result | Status |
|---|---|---|---|---|---|---|
| H1 | | | | | | open / confirmed / killed |
| H2 | | | | | | open / confirmed / killed |
| H3 | | | | | | open / confirmed / killed |

**Filling rules**:

- A testable hypothesis identifies a specific mechanism, its operating conditions and a necessary prediction. Broad categories are investigation branches, not yet falsifiable candidates. If those details or the required measurement coverage are unavailable, write `unresolved` in `Falsifier`, state what would make the hypothesis testable, and keep it `open`. Do not invent a falsifier to fill the table; early triage may end with all branches open.
- A falsifier must contradict a necessary prediction of the stated hypothesis under the checked conditions. Another affected case or missing telemetry is not a contradiction by itself; label weaker observations as discriminating evidence, not decisive refutation.
- Establish causal layer and event ordering before treating explanations as mutually exclusive. An upstream trigger, an intermediate failure mechanism and a downstream symptom can coexist. Observing a possible cause or consequence of a mechanism does not refute that mechanism; resolve the relevant timing or contract first. If the evidence only changes which branch to inspect next, say so and keep the hypothesis open.
- For a scoped hypothesis, `Expected if true` identifies an observable prediction (e.g. `memory_rss ↑` under a stated workload), distinguishing necessary predictions from suggestive tendencies. For an unscoped branch, write `unresolved` and the information needed to derive a prediction. Do not invent a metric, direction or invariant.
- After every round of data, update `Actual result` and `Status`.
- Mark `killed` only when an observed result contradicts a necessary prediction of the scoped hypothesis, with its operating conditions and measurement coverage verified. An expected tendency or aggregate correlation is not necessarily such a prediction. If that check is unavailable, record the result as discriminating evidence and keep the branch open; if the check is satisfied, do not preserve the disproved hypothesis to save a narrative.

**Structural hard constraints (principle 2)**:

- **Total rows ≥ 2** (prevents a single-hypothesis pursuit end-to-end without any comparison).
- **When entering Phase 3**: preserve at least two genuinely relevant competing explanations for root-cause adjudication, including any already verified historical states at the same evidence baseline. Do not reopen resolved alternatives merely to obtain two `open` rows.
- **Mid-Phase 3**: if only one explanation has been investigated, inspect plausible alternatives and their evidence. If no second testable hypothesis is supported yet, retain an unconfirmed bounded result rather than manufacture one.
- **Healthy convergence state before final**: 1 row `confirmed` + ≥1 row `killed` (competing hypotheses have been killed by evidence one by one). Only this proves that discrimination actually happened, rather than "the most plausible one was left standing".

If every candidate has been killed and no supported explanation remains, return to Phase 2 and reconsider the investigation model before adding another candidate. Killing the last open alternative while a confirmed explanation remains is normal convergence, not a reason to restart.

#### Update actions after each round of evidence

After each step, collect the next authorized read-only evidence. If no approved collector is available, ask the user for the specific observation needed. Based on the evidence:
- Update the `Actual result` and `Status` of the corresponding row in the Hypothesis Tracking Table
- If all candidates are killed and no supported explanation remains, return to Phase 2 and reconsider the model. Preserve a surviving confirmed explanation and continue its remaining convergence checks.
- Track which symptoms are explained and which are not

**Switch detection — proactively suggest changing methodology when:**
- 3+ steps completed without narrowing the suspect scope (i.e. `killed + confirmed = 0` in the tracking table, every row still `open`)
- New evidence contradicts the current methodology's assumptions
- Multi-factor interplay discovered mid-investigation (suggest layering FTA)

When suggesting a switch, explain what evidence triggered it and which methodology would handle this pattern better.

### `ROOT_CAUSE_INVESTIGATION` Phase 4: Convergence & Conclusion

#### Step 4.1 — Magnitude / Direction Sanity Check (mandatory, principle 5)

Before declaring a root cause, perform two sanity checks on the currently `confirmed` hypothesis. **If either fails, go back to Phase 3** — do not force the narrative:

- **Magnitude match**: does the scoped mechanism quantitatively explain the observed output under the checked conditions, including any evidenced amplification? Code diff size alone does not predict impact size. An unexplained mismatch blocks confirmation and requires further investigation; it kills the hypothesis only if it contradicts a necessary prediction with adequate measurement coverage.
- **Direction consistency**: compare `Expected if true` and `Actual result` under the same conditions and measurement coverage. Apply the Phase 3 kill criterion to necessary predictions; a contrary tendency alone does not disprove a broad mechanism. Do not invent an amplifier or edge case to rescue a disproved hypothesis.

**Why magnitude sanity check matters**: a self-consistent narrative can still be incomplete or wrong. An unexplained magnitude may require checking the mechanism, conditions, measurements or an additional cause. Distinguish insufficient evidence to confirm from sufficient evidence to refute; neither justifies forcing a root-cause conclusion.

#### Step 4.2 — Recovery Judgment Table (mandatory, principle 4)

Before declaring "recovered / fixed / closed", fill in the table below. **If any lagging indicator has not converged, no final is allowed.** Leading indicators may serve only as auxiliary signals:

| Metric | Incident peak | Current | Baseline / expected | Type (lagging / leading) | Converged? |
|---|---|---|---|---|---|
| <core business metric> (error rate / success rate / p99 latency) | | | | lagging | ⬜ |
| <resource metric> (CPU / memory / queue depth / replication lag) | | | | lagging | ⬜ |
| <downstream dependency health> | | | | lagging | ⬜ |
| <alert state> ("no new alerts in the last N minutes") | — | — | — | leading (auxiliary) | (not a valid final criterion) |

**Rules**:

- All `lagging` rows (core business metric back to baseline, resources in steady state, dependencies healthy) **must be ✅** before final is allowed
- `leading` signals ("no new alerts", "no new errors", "pager is quiet") are **auxiliary only** and **may not stand alone** as recovery evidence. Reason: leading signals can produce false positives due to alert `for:` hysteresis, sparse sampling, or upstream observability outages
- If a key metric cannot be externally observed → explicitly write "requires instrumentation before re-observation"; do not pretend it is ✅

#### Step 4.3 — Final Checklist (mandatory, principle 6)

Before declaring "root cause confirmed" / "final" / "closed" / "recovered" in any response, answer each item below:

- [ ] Every `confirmed` hypothesis has a valid falsification test that was actually run under the stated conditions and survived, rather than its falsifier being observed? (principle 1)
- [ ] The tracking table has ≥1 row `killed` (killed by decisive evidence, not "shelved because another one looked more plausible"), and total rows ≥ 2? (principle 2)
- [ ] Every material causal neighbor has been checked and reconciled, or is explicitly NOT_APPLICABLE with evidence? (principle 3)
- [ ] All lagging rows in the recovery table are ✅, with no declaration resting on leading signals alone? (principle 4)
- [ ] Magnitude + direction sanity checks both pass (principle 5), with no hypothesis rescued via "partially true / amplifier / edge case"?
- [ ] Open questions (unresolved sub-problems) are explicitly listed rather than hidden, each with a stated hand-off owner?

**Any ❌ / unchecked = no final; go back to the corresponding phase and patch the gap.** The cost of premature final is far higher than the cost of one more round of observation.

#### Step 4.4 — Conclusion Output

After the checklist passes, output the final conclusion. On top of passing the checklist, the root cause claim must also satisfy the following 4 quality criteria:

A root cause conclusion is only valid when it passes **all 4 criteria**:

1. **Explains all symptoms** — every known anomaly is accounted for
2. **Explains non-impact** — why unaffected components were spared
3. **Fixable and verifiable** — removing this cause should eliminate the problem
4. **Prevention-complete** — the problem will not recur from the same cause

Present the conclusion as:
- **Root cause**: one sentence
- **Causal chain**: event sequence from trigger to symptoms
- **Verification**: how to confirm this is the actual cause
- **Prevention**: what to change so it doesn't happen again
- **Open questions**: sub-problems not fully closed, each with an explicit hand-off owner (next session / owner / ticket)

### `ROOT_CAUSE_INVESTIGATION` Phase 5: Stuck — Fallback Strategy

If investigation stalls across methodology switches:
- Suggest expanding data collection (new sources, longer time range, adjacent systems)
- Suggest involving other teams or domain experts
- Mark as "insufficient data" with a clear list of what's still needed
- Never force a premature conclusion — "we don't know yet, here's what we need" is a valid and honest outcome

## Examples

### Bad

User: "service-A in prod hit OOM at 10:17, investigate."

Counter-example behavior:

1. Dive straight into investigation, looking only at service-A pod logs and heap dump
2. Notice a recent deploy whose commit message says "refactor cache"
3. Directly declare "root cause is that this refactor introduced a memory leak"
4. Recommend rollback + scale up
5. Problem does not immediately reoccur → mark as "resolved"
6. A few days later the same OOM happens again, and rolling back to the previous version still triggers it

Principles violated:

- **Violates principle 3 (causal neighbors)**: never filled out the neighbor table, never checked whether downstream Redis was slow, whether upstream call volume spiked, or whether the shared node was under memory pressure. The real root cause may live in one of those places.
- **Violates principles 1 + 2 (falsifier + parallel hypotheses)**: only tracked a single hypothesis ("refactor is the root cause"), wrote no falsifier for it, and never ran competing hypotheses in parallel (e.g. "downstream slowdown caused request pileup", "traffic spike exceeded capacity", "a cron job happened to start at 10:17"). Once the single hypothesis is disproven, there is no fallback.
- **Violates principle 5 (magnitude sanity check)**: never checked whether "the size of the refactor diff" matches "the magnitude of heap growth". Refactors usually do not cause memory-magnitude deltas; a mismatch means the hypothesis does not hold.
- **Violates principle 4 (lagging convergence)**: used "problem did not reoccur" as recovery criterion, which is a leading indicator (absence of a negative signal), not a lagging indicator (core business metric back to baseline).
- **Violates principle 6 (final checklist)**: never ran the final checklist, declared root cause directly.

Result: the same incident recurs because the real root cause was never located.

### Good

Illustrative observations below are a teaching example, not a completed live investigation.

User: "service-A hit OOM at 10:17; investigate with read-only code and metrics access."

Read the service, caller, dependency, shared-node and recent-change evidence first.
Suppose request traces show longer Redis waits during the incident, the heap
profile attributes the growth to live request buffers, and node observations
show no host-level pressure. These observations narrow the mechanism; timing
correlation alone does not establish why Redis slowed down.

Scope alternatives before adjudicating them:

| Candidate mechanism | Necessary prediction and distinguishing evidence | Current conclusion |
|---|---|---|
| A changed cache retains objects after requests finish | Under the claimed path and workload, affected cache objects must remain reachable after completion. Fixed-version code plus a complete retained-object profile attributing the excess bytes elsewhere would refute this specific retention claim. | Open until the relevant path and profile are checked; stable RSS alone does not refute every possible leak. |
| Redis waits retain enough concurrent request buffers to cause the measured excess | The affected requests must remain live while waiting and retain enough measured bytes. Profiles showing no relevant retention, or a covered upper bound below the excess without another evidenced contributor, would refute this scoped explanation. | Supported as a lead; quantify retention and test the mechanism before confirmation. |
| The host killed a process because of node memory pressure | Host OOM events and node pressure must account for this termination. A matching cgroup-limit termination plus complete host-event coverage showing no host OOM refutes this specific host-kill account. | Exclude only if those observations were actually collected. |

The mechanisms can coexist. A result against one does not prove another.
For a stable measurement interval, 1,000 requests/s multiplied by an observed
mean additional wait of 0.5 s estimates 500 additional concurrent requests.
Multiplying by a measured 64 KiB retained per request estimates about 31 MiB,
which cannot alone explain a measured 2 GiB excess. A p99 latency is not a mean,
and concurrency is not measured in requests per second. Check queue growth,
buffer attribution and other evidenced contributors; do not invent amplification.

A useful bounded result is: "Longer dependency waits and retained request buffers
were observed, but their measured contribution is insufficient to explain the
whole OOM. Root cause remains unconfirmed. Next, attribute the remaining heap
and inspect waiting-request counts over the incident window." Record the exact
read files, versions, observations and unavailable checks in the evidence appendix.

If a separately authorized intervention reduces latency, verify the affected
version and path, retained memory, error rate and dependency health over the
recovery window. A successful command, correlation or absence of a fresh alert
does not close the incident. Apply the Phase 4 gates before claiming root cause
or recovery; otherwise deliver the bounded result and its remaining uncertainty.

## References

- `references/google-sre-troubleshooting.md` — Google SRE layered top-down troubleshooting
- `references/fault-tree-analysis.md` — Fault Tree Analysis (logic gates + top-down)
- `references/fishbone.md` — Fishbone / Ishikawa diagram (6M dimensions)
- `references/kepner-tregoe.md` — Kepner-Tregoe IS/IS-NOT boundary definition
- `references/stamp-stpa.md` — STAMP/STPA control-theoretic fault analysis

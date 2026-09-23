# Analysis value and evidence discipline

This is a content rule for the existing RCA method, not a second method, Artifact
schema, permission grant, or automatic quality classifier. Apply it to initial
Issue triage and follow-up analysis. Human-facing output is Chinese.

## Start with the unresolved decision

Read the supplied Issue and its admitted evidence first. Identify what its author
already knows, what remains uncertain, and which decision this attempt can help
make. A detailed incident report needs targeted verification or a concrete gap
analysis; it does not need its own chronology and recommendations repeated.

Choose the highest-value authorized read-only check. Perform it when a verified
context and approved collector are available, rather than assigning all evidence
collection back to the owner. If unavailable, state the exact missing capability
or evidence. Never infer repository access from a project name, label, or URL;
the existing context, identity and privacy gates still apply. Do not reopen an
already evidence-resolved alternative merely to satisfy a hypothesis count.

## What counts as useful progress

The attempt should deliver at least one of these, with evidence or explicit limits:

- A new observation that confirms, contradicts or narrows an existing claim.
- A previously unaddressed contradiction, boundary, or alternative, plus the
  smallest check that distinguishes it and the decision each result enables.
- A grounded choice between candidate actions, identifying what evidence supports
  the choice and which compatibility constraints remain unresolved.

Translation, paraphrase, extra headings, a longer checklist, and repeating the
source's existing risks or action items do not satisfy this rule. It is acceptable
to report **“本轮无新增验证；以下仅为待核验缺口”**. Do not invent a finding, force a
new root cause, or manufacture competing hypotheses to avoid that statement.
When no useful distinction or check has been developed, retain `TRIAGED` rather
than suggesting that a completed transport transaction means analysis converged.
Existing status and proof rules remain authoritative; this document never makes
`BLOCKED` into `TRIAGED`, or creates a new status for lack of novelty.

## Evidence wording

Keep three claims separate in the existing evidence ledger and rendered prose:

| Basis | Wording and limit |
|---|---|
| Issue or other admitted source states X | “Issue 报告 X；本轮未独立核验。” Source retrieval verifies the text, not X itself. |
| This attempt actually observed X | State the result, exact evidence locator and revision/time, including limitations. |
| X is a possible explanation or risk | Keep it explicitly unverified. For a scoped, testable hypothesis, state a valid falsifier; for a broad branch, identify the evidence needed to scope it without inventing a refutation. |

Do not place source-reported runtime or code claims under an undifferentiated
“已确认事实” heading. A SOURCE evidence reference cannot silently become proof of
repository inspection or a fresh runtime measurement. Evidence depth and causal
status remain separate: a useful source-only analysis can exist, but cannot claim
independent code verification or root-cause confirmation.

## Choose a bounded next check

Prioritize at most three next checks in human-facing output. The complete
machine Artifact may retain all required hypotheses and open questions; the
limit is on presentation, not on investigation coverage. Each selected check
must identify:

1. The uncertainty or decision it addresses, with a source/evidence reference.
2. The authorized reader or responsible role and concrete observation to collect.
3. The expected observations for the alternatives, and how the result changes
   the next decision. A numerical threshold is required only when evidence or an
   existing acceptance contract supplies one; do not invent an SLO.

“检查日志”“补测试”“灰度验证”“指定负责人” alone are not executable probes.
Do not settle a behavior change before checking its callers and compatibility.
A proposed test expectation also specifies behavior: first establish which paths
require the result and which permit fallback, then derive the expected failure or
success contract for each. If that contract has not been read, propose a check to
discover it, not an assertion that all such requests should fail.
A possible security concern is not a confirmed vulnerability:
first inspect the existing authentication and ownership checks, then determine
whether the proposed change actually widens authority. Preserve already excluded
or unaffected paths unless new evidence contradicts their exclusion.
For each recovery action, state the exact state change it can make, its admission
prerequisites, scheduling/deduplication and persistence-failure branches, and the
observable acceptance result. Check these against the authorized source before
recommending waiting or resubmission. Closing a status is not restored business
output; an age threshold is an eligibility condition, not an execution guarantee.
If a prerequisite is unknown, make the action conditional and identify its check.

## Final comparison with the input

Before finalizing, compare the result directly with the Issue, not just with the
Artifact schema. Explain briefly what this attempt added, what it actually read,
and which decision remains blocked. Put the useful difference first; summarize
source background only when needed to understand it. Do not turn these checks
into mandatory extra headings in every user-facing comment.
For every key causal assertion, check the ordered source path, its prerequisites
and counterexamples before using universal wording. Distinguish a check that is
not mandatory from one that never runs, and a missing input from a value already
set by an earlier step. For each falsifier, ask whether observing it under the
same conditions would contradict the hypothesis, rather than support it; a
wrong-way condition cannot eliminate a hypothesis. Leave a broad explanation
open until it has a scoped necessary prediction that can actually be refuted.

Use existing `problem_frame`, `evidence_ledger`, `hypotheses`,
`verification_matrix`, `recommended_actions` and `open_questions`; add no fields.
Schema validation checks structural consistency, not whether prose is novel,
correct or useful. Keyword checks, an Agent's self-reported PASS, and successful
comment/notification delivery cannot substitute for comparing source and output.

For changes to this behavior, evaluate the two cases in
[the calibration set](../evaluations/analysis-value-cases.md), plus a new case
outside that set. Record actual model/tool execution separately from editorial
examples. Never label examples as production replay, validated fixes, or evidence
that Buzz's current route has loaded this package. Runtime acceptance also needs
the effective route, producer, pinned package and real output read back.

# Code evidence execution

Use for a code investigation or a follow-up that adds repository read authority.
This is execution guidance inside the existing RCA method, not a new status,
artifact schema, credential source, or permission grant.

## Resolve authority before choosing evidence depth

Read the current request and the applicable workflow admission. Distinguish
these cases explicitly:

- An interactive user authorizes a named repository's relevant read-only code:
  use the approved reader within that scope and applicable repository rules.
- A managed Issue workflow requires signed context/identity capabilities:
  obtain those through its existing gate. A chat message, URL or candidate cwd
  cannot substitute for that machine authority. Report the exact failed gate.
  When its manifest admits a bounded repository group, verify every admitted
  repository and use project-qualified exact-SHA locators as defined in the
  [Artifact contract](problem-analysis-contract.md). Do not extend that group
  from a caller chain or an Issue link. Preserve the existing single-repository
  path when no related repository is admitted.
- No repository authority is available: retain SOURCE_ONLY and identify the
  specific authorization or source needed. Do not infer permission from an Issue.

Do not call an available authorized check “the owner's next step” without first
trying it. Missing local Git objects, a failed convenience command, and no
repository permission are different conditions. If the approved reader supports
remote fixed-commit files, a missing local object need not block the read.
Never switch identity, weaken a gate, or search credentials to make a read work.

## Collect enough evidence to change a decision

1. Resolve the requested baseline to a full SHA using trusted project/branch
   metadata. Keep default-branch code separate from deployed code. Read applicable
   AGENTS.md before source; record absent, denied and unreadable separately.
   In both interactive and managed investigations, establish existence/absence
   from the fixed commit's exact tree entry using a successful
   `git ls-tree --full-tree <SHA> -- AGENTS.md`, or an equivalent successful,
   complete tree API read.
   `rg --files`, ignore/glob-filtered listings, missing local objects and HTTP 404
   do not prove absence. For mode `120000`, read the link and its target in the
   same tree, applying the contract's bounded rules-chain checks: stay within
   the admitted repository, reject cycles/missing targets, and allow at most
   eight nodes. Apply parent/workspace and relevant nested rules as well.
   If AGENTS.md is absent but CLAUDE.md exists, read CLAUDE.md before source.
   Record each rule read's SHA, path, resolved target and blob/content digest;
   absence requires the exact-tree proof, not a fabricated content digest.
   If any applicable rule remains unresolved, stop that repository's source
   investigation and report the failed read; do not claim the rule is absent.
   For an environment-specific report, inspect the relevant available release or
   environment branch in every affected repository, not just each default branch.
   Absence in a different version does not refute the reported path. An unknown
   deployed SHA remains a runtime gap; it does not block reading relevant code.
2. Use search only to discover paths. Read the relevant full method and adjacent
   branches at the fixed SHA before citing it. A ref argument is not proof that
   an API honored it; verify exact-commit file content. List the searched scope,
   pagination or truncation limits before claiming complete caller coverage.
3. Trace the contested input through caller, validation/lookup, transformation
   and failure handling. Inspect existing overloads and access checks before
   prescribing new ones. For identity-sensitive paths, distinguish caller
   authentication, authority to represent a user, and that user's resource access.
4. Challenge absolute source claims such as “always”, “never” and “all”. Read
   ordering and early-return conditions. Report a counterexample as a correction
   to the source, with its exact condition; do not replace one absolute with another.
   Before publishing each key causal claim, map its ordered calls and branch
   conditions back to the fixed source. Retain earlier assignments and checks,
   including authentication, and search for a counterexample on another admitted
   path. A later optional check does not erase an earlier check or assignment;
   one absent input does not establish an absent effective value on every path.
5. Follow the failure to its observable contract: exception mapping, HTTP status,
   business envelope and caller handling where authorized. Stop at an unread
   cross-repository boundary rather than assuming consumer behavior.
6. If a conclusion depends on background work or stored status, read the actual
   callback and update helper, including missing dependencies, returned or swallowed
   errors, affected-row checks, partial side effects and retry/reconciliation paths.
   Distinguish failure detection, a write attempt, durable state and business completion.
   A log, metric, successful API envelope or call to an update method does not prove
   persistence. Verify output completeness separately from a success status; when
   runtime readback is outside authority, keep those outcomes unverified. Determine
   whether recovery retries the same work, creates new work or leaves historical
   failures untouched, and preserve any source-retention deadline.
   Before proposing a recovery action, trace its admission and deduplication
   branches against the persisted state, then its scheduler and write failures.
   Identify the timestamp/event from which any threshold is measured; distinguish
   eligibility, actual invocation and durable effect. State whether the action
   merely closes/releases state or executes work, and what separate readback
   would establish the requested business result. Never imply waiting or a new
   request guarantees execution when a dependency, deduplication or failed write
   can leave the original state unchanged.

For each material finding retain SHA, path, line, observed branch, and its effect
on the pending decision. Where a workflow demands a digest or signed proof, use
its canonical generator; a self-defined tree hash is not the same attestation.

## Converge without repeated operator steering

Choose a bounded first pass around the unresolved decision. When it establishes
the relevant code facts, deliver them with explicit remaining coverage instead
of delaying for an exhaustive repository audit. Expand only for a new evidence
gap that could change the decision. If a command blocks, give a checkpoint with
the actual failure and next permitted action, rather than a promise of progress.

Use concise prose for the user-facing decision, with detailed evidence/tables in
the analysis artifact or linked appendix where appropriate. Do not repeat the
same finding under evidence, recommendation and “new value” headings. Preserve
time-sensitive business consequences from the input, including recovery windows.
Required machine-artifact fields and causal gates remain unchanged.

Conclude using the existing outcome vocabulary. A supported code mechanism may
be described in prose, but do not invent a new machine status for it. Separate
what was observed in code from deployment verification, reproduction and business
recovery. Passing transport/schema checks is never semantic acceptance.

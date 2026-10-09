# Worktime Filing Eval Input Contract

`prepare_cases.py` is the adapter for these evals. A case defines exactly one input form:

- `prompt` for a single user turn; the adapter converts it to one user message.
- `messages` for an authoritative multi-turn transcript; a runner must pass the sequence unchanged and must not flatten it into a user prompt.

Tool results in a transcript are non-user-visible fixture state. Case 13 uses this channel for a fixed fake `confirmation_token`, allowing the confirmation turn to prove exact token reuse without exposing it in the preceding assistant message.

Run the contract checks with:

```bash
uv run --extra dev pytest -q skills/collaboration/worktime-filing/evals/test_eval_contract.py
uv run python skills/collaboration/worktime-filing/evals/prepare_cases.py >/dev/null
```

The mandatory merge-request security lane runs `uv run python scripts/validate.py --security`. Its generic eval validator enforces canonical input shape, contiguous IDs, assertion structure, valid tool-call sequencing, and non-disclosure of token values returned by tools without executing skill-owned code.

For this protected suite, the repository-owned `EVAL_CONTRACT_POLICIES` entry in `scripts/validate.py` is the source of truth for the fixed 54-case range and the critical invariants in cases 10, 13 and 20–54. Cases 22–43 cover cross-week preview isolation, exact-list confirmation (positive and runtime negative variants), serial execution, partial-failure stopping and single/multi-week recovery, explicit rate-limit and queue failures, unassigned evidence, initial/preflight validation failures, safe batch partitioning, re-authentication, ambiguous asynchronous outcomes, capability fail-closed behavior (missing, version mismatch, and disabled), and durable operation-key replay/conflict handling. Repository-owned case digests bind each protected case exactly, so extra contradictory text or weakened assertions in `evals.json` are rejected.

Cases 44–54 cover personal week-correction preview and exact confirmation, capability denial, stale previews, missing original submission time, incomplete readback/statistics, re-authentication identity changes, invalid target weeks, and confirmed actual hours above calendar recommendations. Case 21 now checks recommendation disclosure and confirmation instead of rejecting actual hours. The protected digests for unrelated batch and assignment cases are unchanged. These files define eval inputs; adapter/contract checks do not constitute an executed model eval.

Cases 53–54 retain the original submission time when correcting retroactive records back to their submission week or to another earlier week. Target confirmation binds the other-record total, row count and calendar recommendation, not the contents of every other row.

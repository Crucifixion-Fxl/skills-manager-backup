# Worktime Filing Eval Input Contract

`prepare_cases.py` is the adapter for these evals. A case defines exactly one input form:

- `prompt` for a single user turn; the adapter converts it to one user message.
- `messages` for an authoritative multi-turn transcript; a runner must pass the sequence unchanged and must not flatten it into a user prompt.

Tool results in a transcript are non-user-visible fixture state. Case 13 uses this channel for a fixed fake `confirmation_token`, allowing the confirmation turn to prove exact token reuse without exposing it in the preceding assistant message.

Run the contract checks with:

```bash
uv run --extra dev pytest -q skills/worktime-filing/evals/test_eval_contract.py
uv run python skills/worktime-filing/evals/prepare_cases.py >/dev/null
```

The mandatory merge-request security lane runs `uv run python scripts/validate.py --security`. Its generic eval validator enforces canonical input shape, contiguous IDs, assertion structure, valid tool-call sequencing, and non-disclosure of token values returned by tools without executing skill-owned code.

For this protected suite, the repository-owned `EVAL_CONTRACT_POLICIES` entry in `scripts/validate.py` is the source of truth for the fixed 43-case range and the critical invariants in cases 13 and 20–43. Cases 22–43 cover cross-week preview isolation, exact-list confirmation (positive and runtime negative variants), serial execution, partial-failure stopping and single/multi-week recovery, explicit rate-limit and queue failures, unassigned evidence, initial/preflight validation failures, safe batch partitioning, re-authentication, ambiguous asynchronous outcomes, capability fail-closed behavior (missing, version mismatch, and disabled), and durable operation-key replay/conflict handling. Repository-owned case digests bind each protected case exactly, so extra contradictory text or weakened assertions in `evals.json` are rejected.

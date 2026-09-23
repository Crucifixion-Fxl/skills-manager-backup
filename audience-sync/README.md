# Audience Sync Skill

The canonical Skill and fixed Python client for production Project audience
selection, preview, materialization and exact-run membership sync.

Start with [SKILL.md](SKILL.md). Use the host's named operations when available.
The Skill root is the directory containing this `SKILL.md`. A complete installed
`skills/audience-sync` directory or full repository clone supports the bundled
adapter with Python 3.9+, without runtime package installation or a separate Git
clone. Keep `scripts/`, `src/` and `contracts/` together under that Skill root:

```bash
python3 /absolute/path/to/skills/audience-sync/scripts/api.py summarize_project_keys
```

The host securely injects key(s). The adapter defaults to
`https://audience-workflow-api-prod-us.addx.live`.
[Host binding](references/host-configuration.md) covers authenticated Project
and permission discovery, multi-key selection and diagnostics.

The client registers 13 Project operations: the 10 query execution operations
listed in [Project queries](references/project-query.md#project-execution-contract),
plus `list_project_audience_assets`, `list_project_syncs` and `get_project_sync`.
Own-key discovery (`get_project_personal_key_context`) is independent of those 13.
Existing audience discovery includes the selected owner's native and Query Plan assets;
sync task list/detail reads cover all owners within the authenticated Project.
See [the operation reference](references/platform-api.md). The contract snapshot pins
the exact Audience !317 profile-root user aggregate candidate
`d40108fe69eb5eeaa6879ca4e9aee7f513aa6e2e`; this is not evidence of merge or deployment.

## Repository layout

- `SKILL.md`, `agents/`, `references/`: agent-facing operational guidance.
- `scripts/api.py`, `scripts/preflight.py`, `src/audience_sync/`: bundled client,
  offline configuration checks, fixed transport and response validation.
- `contracts/`: frozen schemas, operation inventories and source provenance.
- `tests/`, `scripts/tdd_*.py`: regression and journey fixtures.
- `docs/plans/`: engineering design and release history.

## Distribution and verification

The repository ships both guidance and the optional client. A semantic-only
host import contains guidance and uses its host's native tools; it does not
contain the Python scripts. The wheel contains client code and contracts only.
`contracts/source.json` records source lineage and declared file hashes;
`contracts/semantic-owner.json` supplies the explicit canonical owner metadata.
Historical compatibility code/contracts remain internal and are covered by
regression tests; production guidance uses only the Project API.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/check_secrets.py
python3 scripts/update_operation_registry.py --check
python3 scripts/update_source_lock.py --check
python3 -m ruff check .
```

## Publishing and cold-start evaluation

The semantic guide consists of `SKILL.md`, `agents/openai.yaml` and the eight
existing references. Native hosts provide its executable tool schemas. A complete
Skill installation or full clone is another supported delivery: it also carries
`scripts/`, `src/` and `contracts/` for the fixed Python client. Neither delivery asks the Agent to
write another client or fetch missing private schemas.

`evals/evals.json` defines evaluation inputs and checks, not recorded results.
Its version 1 `contract` declares the contiguous integer ID range, case count,
canonical input guard, required assertions and hidden tool tokens. Each case
retains its stable `slug`, prompt, mode and setup. `assertions[].check` is the
single canonical source of detailed expectations; all original outcomes live
there verbatim. `expected_output` describes the reply and effect boundary,
without maintaining a second copy of the detailed checks. The evaluation harness
must provision each case's `setup` before supplying its unchanged canonical
`prompt`; setup is fixture/environment input, not an additional user instruction.
A harness that cannot provide that setup must skip/report the case as unsupported,
not ignore it or turn fixture descriptions into permission for live effects. Run with/without-Skill
comparisons only for read-only cases using equivalent isolated host inputs.
Run the authorized production effect case once with the Skill, recording exact
new request/run evidence; do not duplicate writes for a comparison baseline.
See [the delivery plan](docs/plans/2026-09-09-publishing-cold-start-delivery.md).

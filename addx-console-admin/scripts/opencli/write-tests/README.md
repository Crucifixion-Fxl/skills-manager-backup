# Battery write adapters

Two non-browser OpenCLI adapters retain Console's native token authentication:

- `battery-cell-write`: explicit `create` or `edit`; edit preserves the model displayed read-only in the UI and requires an existing ID.
- `battery-pack-write`: explicit configured `edit` or unconfigured `initialize`; the backend endpoint named `edit` actually upserts. A positive integer update timestamp distinguishes configured rows; changed/unknown response shapes reject.

The default mode is `dry-run`. It reads identity/current records, returns the before/proposed values and a SHA256 plan hash, and never submits a mutation. Cell scanning is bounded to 100 pages of 20 with stable total and unique IDs; factory IDs must already occur in verified cell records. Pack reads use an exact model filter and reject incomplete/ambiguous results.

Future authorized submissions require `--mode submit`, `--approved-plan <hash>`, `--deployment-proof <reviewed evidence identifier>`, private `CONSOLE_ALLOW_WRITE=1`, and private `CONSOLE_EXPECTED_EMAIL`. The operator must verify current deployment/action permission and obtain authorization covering the actual mutation before enabling these gates. A supplied evidence identifier is not independently verified by the adapter. Fresh state is read again before submission. The backend has no atomic compare-and-swap, so a race remains between checking and submitting; use external coordination for concurrent modification risk.

The return status after a submission is deliberately `SUBMITTED_NOT_READBACK_VERIFIED`. Separate readback and UI comparison are required before any future write acceptance. Never submit unchanged values as a read-only test: server updates operator/time fields.

`pack-factory-write` accepts a complete replacement JSON document for one existing factory: `complete: true`, `packFactoryId`, and the full `batteryCellFactoryList` of `{batteryCellModelFactoryId, batteryCellModels}`. Partial/append payloads are rejected. The adapter reads all existing associations and availability for proposed factories, exposes exact additions/deletions, and includes the deletion scope in the plan hash. Submission with removals additionally requires `--approved-deletions <deletionHash>`. A fully empty replacement requires `--clear-factory <exact factory ID>`. Empty model sublists are rejected: the source service skips them rather than clearing that cell factory; omit that factory from the complete desired set to remove its associations. Deployed validation may reject full clearing; no real clearing has been tested.

Offline tests use synthetic responses and checkout modules only. They run from an empty temporary HOME and do not import private installed adapters. VM loaders mock OpenCLI registration/errors and transport; they test the actual checkout contracts, not a real installed CLI or live SaaS acceptance. The minimum-firmware template test explicitly reports this boundary.

Run every standalone Node and Python entry, including tests outside the `*.test.mjs` naming pattern:

```sh
python3 skills/business-operations/addx-console-admin/scripts/opencli/run-offline-tests.py
```

The runner disables default network transports and strips inherited credentials. It also runs the Graylog and Zendesk identity consumer regression tests. This is the same entry used by `console-opencli:unit` CI. Hardware parameter output tests require unknown and sensitive saved/display values to be hidden; only reviewed public boolean parameter codes may expose values.

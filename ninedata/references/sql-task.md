# SQL Task Tools

Use SQL Task tools for non-DQL SQL and governed SQL changes that need rule review, approval, scheduling, execution control, or audit evidence.

Default to SQL Task when the SQL contains:

- DML: `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `MERGE`
- DDL: `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME`
- DCL: `GRANT`, `REVOKE`
- TCL: `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`
- `CALL` or vendor-specific procedure commands unless they are proven read-only
- any mixed batch containing one non-DQL statement

Do not require the user to explicitly say "use SQL Task" for these statements. After showing the full SQL and receiving confirmation, submit the task directly.

## Workflow

1. Show the full SQL to the user.
2. Get explicit confirmation for generated, modified, or non-DQL SQL.
3. For DDL or mixed (DDL+DML) batches, satisfy DDL Change Traceability (see section below) when composing `--reason`, and provide rollback SQL.
4. Submit the task with `scripts/sql-task.sh submit`.
5. Query `scripts/sql-task.sh detail --task-id <taskId>` until the platform status is clear.
6. If rule review fails, explain the message and update with `scripts/sql-task.sh update` after user confirmation.
7. When `canSubmitApproval=true`, call `scripts/sql-task.sh submit-approval`.
8. After `submit-approval` or `approve`, query detail again. If status is still `toApprove`, the workflow needs another approval node. If the user has explicitly authorized approving this task and `approvalCandidates` contains one current node, call `scripts/sql-task.sh approve` again. Repeat until `canExecute=true`, approval is rejected, or node selection is ambiguous.
9. When `canExecute=true`, ask the user to confirm execution and call `scripts/sql-task.sh execute`.
10. Use `scripts/sql-task.sh execution-detail`, `scripts/sql-task.sh subtask-list`, or `scripts/sql-task.sh subtask-detail` to diagnose failed or unclear execution states.
11. Use `scripts/sql-task.sh suspend`, `scripts/sql-task.sh resume`, `scripts/sql-task.sh stop`, `scripts/sql-task.sh cancel`, or `scripts/sql-task.sh cancel-execute` only when requested or clearly required.
12. Use approval operation scripts only when the user explicitly confirms the exact approval action.

## DDL Change Traceability

A downstream AI approval node auto-**rejects** DDL and mixed (DDL+DML) tasks that lack traceability. When the SQL contains any `ALTER`, `CREATE`, `DROP`, `TRUNCATE`, `RENAME`, or mixes DDL with DML, the `--reason` (task comment) MUST include either:

- A **related** GitLab MR link (`https://gitlab.addx.ai/<group>/<repo>/-/merge_requests/<iid>`) that actually corresponds to this change — an unrelated MR is detected and rejected on review; or
- A sufficient change explanation: why, affected scope, and rollback plan. A reason shorter than ~30 characters or missing these elements is treated as insufficient and rejected.

Also expected for DDL: a real rollback SQL (`--rollback-sql` / `--rollback-sql-file`); online DDL (`ALGORITHM=INSTANT, LOCK=NONE`, or gh-ost / pt-online-schema-change for large/hot tables) instead of a bare `ALTER`; and keeping the change consistent with the owning service's code (update migrations/`init.sql`/ORM models in the same MR). Proactively ask the user for the MR link or a complete rationale before submitting, and warn that the task will otherwise be auto-rejected.

## Commands

Submit:

```bash
scripts/sql-task.sh submit \
  --sql-file /tmp/change.sql \
  --rollback-sql-file /tmp/rollback.sql \
  --reason "Apply user-approved change" \
  --source-client codex \
  --client-confirmed
```

Detail:

```bash
scripts/sql-task.sh detail --task-id st_xxx
```

List:

```bash
scripts/sql-task.sh list --keyword customer --page-size 20
```

Update:

```bash
scripts/sql-task.sh update \
  --task-id st_xxx \
  --sql-file /tmp/fixed.sql \
  --reason "Fix rule review issue" \
  --source-client codex \
  --client-confirmed
```

Submit approval:

```bash
scripts/sql-task.sh submit-approval \
  --task-id st_xxx \
  --reason "Rule review passed" \
  --source-client codex \
  --client-confirmed
```

Execute:

```bash
scripts/sql-task.sh execute \
  --task-id st_xxx \
  --execute-type now \
  --exec-failed-action stop \
  --backup-failed-action stop \
  --reason "User approved execution" \
  --source-client codex \
  --client-confirmed
```

Pause, resume, stop, or cancel execution:

```bash
scripts/sql-task.sh suspend --task-id st_xxx --reason "User requested pause" --source-client codex --client-confirmed
scripts/sql-task.sh resume --task-id st_xxx --reason "User requested resume" --source-client codex --client-confirmed
scripts/sql-task.sh stop --task-id st_xxx --reason "User requested stop" --source-client codex --client-confirmed
scripts/sql-task.sh cancel --task-id st_xxx --reason "User requested workflow cancellation" --source-client codex --client-confirmed
scripts/sql-task.sh cancel-execute --task-id st_xxx --reason "User requested cancel execution" --source-client codex --client-confirmed
```

Execution diagnostics:

```bash
scripts/sql-task.sh execution-detail --task-id st_xxx
scripts/sql-task.sh subtask-list --task-id st_xxx --status failed --page-size 20
scripts/sql-task.sh subtask-detail --task-id st_xxx --subtask-id sub_xxx
```

Approval operations:

```bash
scripts/sql-task.sh approve --task-id st_xxx --reason "User explicitly approved this task" --source-client codex --client-confirmed
scripts/sql-task.sh reject --task-id st_xxx --reason "User explicitly rejected this task" --source-client codex --client-confirmed
scripts/sql-task.sh transfer-approval --task-id st_xxx --transfer-account-id acc_xxx --reason "User explicitly requested transfer" --source-client codex --client-confirmed
```

If `--node-id` is omitted for approval operations, the script queries `workflow/nodeInfo` and proceeds only when it can infer exactly one approval node. If multiple candidates exist, pass `--node-id` explicitly.

## Multi-step Approval

Some SQL Task workflows require more than one approval node. A workflow log entry such as `Approved` only means one node passed; it does not mean the task is executable.

Use this loop:

1. Call `scripts/sql-task.sh detail --task-id st_xxx`.
2. If `status=toApprove`, inspect `approvalCandidates`, `approvalRound`, and `nextApprovalNodeId`.
3. If the user has explicitly approved the task and exactly one pending approval node is returned, call:

```bash
scripts/sql-task.sh approve \
  --task-id st_xxx \
  --node-id <nextApprovalNodeId> \
  --reason "User explicitly approved the next workflow approval node" \
  --source-client codex \
  --client-confirmed
```

4. Query detail again. Repeat the loop while `status=toApprove`.
5. Execute only after detail returns `canExecute=true`.

Do not stop at the first approval when `canExecute=false`. Do not ask the user to open the console just because one approval passed and the task is still `toApprove`; use `detail` or `execution-detail` to find the current approval node first.

## Parameters

- `--task-id`: SQL Task workflow id.
- `--datasource-id`: overrides `config.defaultDsId`.
- `--database-name`: overrides `config.defaultDbName`.
- `--schema-name`: overrides `config.defaultSchemaName`.
- `--sql` or `--sql-file`: SQL text; mutually exclusive.
- For `sql-task.sh update`, omit `--sql` and `--sql-file` only when you intentionally keep the current SQL from `sql-task.sh detail` and update metadata or rollback SQL.
- `--rollback-sql` or `--rollback-sql-file`: optional rollback SQL; mutually exclusive.
- `--estimated-affected-rows`: optional estimate, default `0`.
- `--executor-type`: SQL Task executor policy, default `creator`.
- `--execute-type`: `now` or `schedule`; the script maps `now` to NineData workflow `immediately` internally.
- `--execute-time`: required when `--execute-type schedule`.
- `--exec-failed-action`: `stop`, `ignore`, or `rollback`; default `stop`.
- `--backup-failed-action`: `stop`, `ignore`, or `rollback`; default `stop`.
- `--node-id`: optional approval or workflow node id for approval and diagnostic tools.
- `--subtask-id`: optional subtask id for subtask detail.
- `--include-sql`: include full SQL text in subtask detail. Omit by default.
- `--transfer-account-id`: target account id for approval transfer.
- `--reason`: required for side-effect operations.
- `--source-client`: required for side-effect operations; any non-empty client name is accepted.
- `--client-confirmed`: required for side-effect operations after user confirmation.

## Output

Successful SQL Task command output uses a stable envelope:

```json
{
  "ok": true,
  "requestId": "request id from the detail request",
  "operationRequestId": "request id from the operation request",
  "taskId": "st_xxx",
  "workflowId": "st_xxx",
  "operation": "submit",
  "status": "ruleChecking",
  "statusDesc": "Rule checking",
  "datasourceId": "ds_xxx",
  "databaseName": "app_db",
  "schemaName": "public",
  "ruleCheckSummary": {
    "errorCount": 0,
    "warningCount": 0,
    "permissionCount": 0,
    "syntaxCount": 0
  },
  "reviewReady": false,
  "canSubmitApproval": false,
  "canExecute": false,
  "approvalCandidates": [],
  "approvalRound": 0,
  "nextAction": "Wait for rule review and query SQL Task detail again.",
  "consoleUrl": "https://your-ninedata-domain.example.com/dataQuery/task/detail/st_xxx"
}
```

When `consoleUrl` is returned, include it in the final user-facing response as the SQL Task jump link.

Failure output is stable JSON:

```json
{
  "ok": false,
  "error": {
    "code": "NINEDATA_ERROR",
    "message": "Readable platform message",
    "detail": {
      "operation": "sql-task-submit",
      "requestId": "request id when available"
    }
  }
}
```

## Boundaries

- These tools call workflow OpenAPI internally with `module=sqlTask`.
- They do not directly connect to databases.
- They do not bypass rule review, approval, permission checks, workflow state, or audit logs.
- `canSubmitApproval` and `canExecute` are best-effort hints; operation responses are authoritative.
- Approval operations are never automatic. The user must explicitly say to approve, reject, or transfer before the script is called.
- A single user instruction to approve a SQL Task may cover repeated approval calls for the same task only while the workflow keeps returning exactly one pending approval node. If multiple nodes are returned or the action changes, ask the user.
- Subtask detail does not return full SQL unless `--include-sql` is explicitly used.
- Do not expose AK/SK, signatures, database credentials, or complete internal stack traces.

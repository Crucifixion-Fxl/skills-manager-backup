# NineData Skill Tools

## list-datasource

Lists NineData datasources visible to the configured OpenAPI credential.

Common command:

```bash
scripts/list-datasource.sh --datasource-type MySQL --keyword test --page-size 20
```

Use when:

- the user did not provide `datasourceId`
- `config.defaultDsId` is not configured or needs to be changed
- a datasource needs to be found by name or type
- the target environment must be confirmed before SQL execution

## sql-execute

Executes read-only DQL or metadata-check SQL through the NineData controlled OpenAPI.

Common command:

```bash
scripts/sql-execute.sh \
  --database-name app_db \
  --sql-file /tmp/query.sql \
  --reason "Verify order count" \
  --source-client codex \
  --client-confirmed
```

When context arguments are omitted, the script uses `defaultDsId`, `defaultDbName`, and `defaultSchemaName` from the external user configuration. Pass `--datasource-id ds_xxx`, `--db-type mysql`, `--database-name app_db`, `--schema-name public`, or `--cluster-name cluster_a` when the user explicitly chooses context for this run.

`sql-execute` sends `useSessionPersistence=false` by default. Add `--use-session-persistence` only for workflows that intentionally rely on SQL session state.

For short one-line SQL, `--sql "select 1"` is also supported. `--sql` and `--sql-file` are mutually exclusive.

Use when:

- running read-only DQL queries such as `SELECT` or `WITH ... SELECT`
- running read-only metadata checks such as `SHOW`, `DESCRIBE`, `DESC`, or `EXPLAIN`
- checking database state before or after a SQL Task

Do not use for:

- direct database connections
- unconfirmed agent-generated SQL
- ordinary DML/DDL/DCL/TCL business changes
- mixed SQL batches where any statement is non-DQL
- stored procedure calls or vendor-specific commands that may change state
- bypassing NineData permissions, rules, approvals, or audit logs

If SQL type is ambiguous, ask the user or use SQL Task. Do not send ambiguous SQL to `sql-execute` by default.

## SQL Task Tools

Use SQL Task tools for non-DQL SQL and governed SQL changes that need rule review, approval, scheduling, execution control, or durable workflow evidence.

Common submit command:

```bash
scripts/sql-task.sh submit \
  --sql-file /tmp/change.sql \
  --reason "Apply approved schema/data change" \
  --source-client codex \
  --client-confirmed
```

Common lifecycle commands:

```bash
scripts/sql-task.sh detail --task-id st_xxx
scripts/sql-task.sh update --task-id st_xxx --sql-file /tmp/fixed.sql --reason "Fix review issue" --source-client codex --client-confirmed
scripts/sql-task.sh submit-approval --task-id st_xxx --reason "Rule review passed" --source-client codex --client-confirmed
scripts/sql-task.sh execute --task-id st_xxx --execute-type now --exec-failed-action stop --backup-failed-action stop --reason "User approved execution" --source-client codex --client-confirmed
scripts/sql-task.sh execution-detail --task-id st_xxx
scripts/sql-task.sh subtask-list --task-id st_xxx --page-size 20
scripts/sql-task.sh subtask-detail --task-id st_xxx --subtask-id sub_xxx
```

SQL Task tool boundaries:

- `sql-execute` is for read-only DQL and metadata checks.
- `sql-task.sh <action>` is for workflow-backed SQL changes with review, approval, execution control, and operation logs.
- Submit DML, DDL, DCL, TCL, state-changing procedure calls, and mixed batches through `sql-task.sh submit` after explicit user confirmation.
- `sql-task.sh submit` supports `--sql` or `--sql-file`. `sql-task.sh update` supports them too, but may omit them when the current SQL from task detail should be kept. Optional `--rollback-sql` and `--rollback-sql-file` are mutually exclusive.
- `sql-task.sh execute` allows only `--exec-failed-action stop|ignore|rollback` and `--backup-failed-action stop|ignore|rollback`; both default to `stop`.
- `canSubmitApproval` and `canExecute` are best-effort hints from detail status. The platform operation response is authoritative.
- `sql-task.sh cancel` cancels the whole workflow.
- `sql-task.sh stop` terminates the workflow.
- `sql-task.sh cancel-execute` cancels a scheduled or pending execution request.
- `sql-task.sh execution-detail`, `sql-task.sh subtask-list`, and `sql-task.sh subtask-detail` are read-only diagnostic tools for failed or unclear executions.
- `sql-task.sh approve`, `sql-task.sh reject`, and `sql-task.sh transfer-approval` are high-risk approval actions. Never call them unless the user explicitly confirms the exact action.

Do not use SQL Task tools for:

- direct database connections
- unconfirmed DML/DDL generated or modified by the agent
- bypassing NineData workflow status, rule checks, approval, execution permissions, or audit logs

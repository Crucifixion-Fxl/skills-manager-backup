---
name: ninedata
description: Use this skill when the user needs to work with the NineData platform from an agent, including listing NineData datasources, running read-only SQL or metadata checks through sql-execute, routing DML/DDL/DCL/TCL and mixed SQL batches to governed SQL Task workflows, checking database state, or validating development data. This skill relies on NineData authentication, permission checks, SQL rule checks, masking, throttling, workflow approval, and audit logs. Do not use it for direct database connections, database credential handling, or unconfirmed SQL execution.
---

# NineData Skill

## Description

This skill lets an agent operate the NineData platform safely through the NineData OpenAPI instead of connecting to databases directly. It covers listing datasources, running read-only DQL and metadata checks through controlled SQL execution, and routing DML/DDL/DCL/TCL or mixed SQL batches into governed SQL Task workflows with rule review, approval, execution control, and audit logs. All execution goes through NineData authentication, permission checks, SQL rule checks, masking, throttling, and workflow approval; the agent never bypasses a platform decision.

## Capabilities

This skill provides a system-level NineData toolset. Current tools:

- `list-datasource`: list NineData datasources visible to the configured OpenAPI credential.
- `sql-execute`: execute read-only DQL or metadata-check SQL through the NineData controlled SQL execution OpenAPI and return the platform decision, execution evidence, and result preview.
- `sql-task`: operate governed SQL Task workflows through one command entrypoint. Supported actions include `submit`, `detail`, `list`, `log`, `update`, `submit-approval`, `execute`, `suspend`, `resume`, `stop`, `cancel`, `cancel-execute`, `execution-detail`, `subtask-list`, `subtask-detail`, `approve`, `reject`, and `transfer-approval`.

Future NineData tools should be added under the `ninedata` skill instead of creating separate skills.

## Script Layout

- `scripts/ninedata.sh`: shared command dispatcher.
- `scripts/ninedata_openapi.py`: shared OpenAPI client, signing, request shaping, and JSON output implementation.
- `scripts/validate-config.sh`: configuration validation entrypoint.
- `scripts/list-datasource.sh`: datasource discovery entrypoint.
- `scripts/sql-execute.sh`: SQL Execute entrypoint for read-only SQL and metadata checks.
- `scripts/sql-task.sh`: SQL Task workflow entrypoint with action subcommands.

## Rules

- Do not connect directly to databases.
- Do not ask the user to paste database usernames, database passwords, AccessKey secrets, cookies, or tokens into chat.
- Do not treat this skill as an automatic natural-language-to-SQL execution tool.
- If SQL is generated or modified by the agent, show the full SQL to the user and get confirmation before execution.
- Use `sql-execute` only for read-only DQL or metadata checks. Use SQL Task tools for DML, DDL, DCL, TCL, stored procedure calls that may change state, or any mixed SQL batch containing a non-DQL statement.
- For non-DQL SQL, clearly warn the user and get confirmation before submitting a SQL Task workflow.
- If NineData rejects, blocks, requires approval, or requires a SQL task, do not bypass the platform decision.
- SQL Task tools must not bypass NineData workflow state, rule review, approval, execution permissions, or audit logs.
- Do not automatically approve, reject, or transfer an approval. Use approval tools only when the user explicitly says to approve, reject, or transfer the task.

## Tool Selection Rules

Default to this routing before calling any script:

- Use `scripts/sql-execute.sh` for read-only DQL and metadata checks: `SELECT`, `WITH ... SELECT`, `SHOW`, `DESCRIBE`, `DESC`, and `EXPLAIN` for a read-only query.
- Use `scripts/sql-task.sh submit` for non-DQL or potentially state-changing SQL: `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `MERGE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME`, `GRANT`, `REVOKE`, `CALL`, transaction control, or any batch where one statement is non-DQL.
- If SQL type is ambiguous, ask a clarification or choose SQL Task. Do not default ambiguous SQL to `sql-execute`.
- Do not use `sql-execute` for ordinary business DML/DDL just to see whether NineData returns `NEED_SQL_TASK`; create the SQL Task directly after user confirmation.
- Use `sql-execute` for non-DQL only when the user's explicit goal is to test the `/openapi/v1/sql/execute` interface itself, not to perform a business data or schema change.

## Required Context

Before using `sql-execute`, make sure these values are known:

- `datasourceId`, or `defaultDsId` configured in the external user configuration
- optional `dbType`, when the datasource type is known and the user wants to pass it explicitly
- `databaseName`, or `defaultDbName` configured in the external user configuration, when required by the database type
- `schemaName`, or `defaultSchemaName` configured in the external user configuration, when required by the database type
- optional `clusterName`, for datasource types that require a cluster selection
- explicit SQL text
- execution reason `reason`
- source client `sourceClient`; any non-empty string is accepted, such as `codex`, `claude-code`, `cursor`, `open-claw`, `hermes-agent`, `qoder`, `trae`, or `open-code`

If `datasourceId` is unknown and `defaultDsId` is not configured, use `scripts/list-datasource.sh` first. Use `--keyword <keyword>` for keyword search.

For SQL Task operations, also make sure the user has confirmed any generated or modified SQL and the operation reason is clear. Use `--client-confirmed` only after that confirmation.

## Standard Workflow

1. Validate configuration: `scripts/validate-config.sh`
2. List datasources when datasource context is missing: `scripts/list-datasource.sh --keyword <keyword> --page-size 20`
3. Prepare or confirm the SQL.
4. Classify the SQL with the Tool Selection Rules.
5. For read-only DQL or metadata checks, execute SQL: `scripts/sql-execute.sh --database-name <db> --schema-name <schema> --sql-file <file> --reason <reason> --source-client <client> --client-confirmed`
6. Read the JSON output and judge the result from `success`, `requestId`, and `data.decision`.
7. Summarize in natural language and include structured execution evidence.

For non-DQL SQL and governed SQL changes that need rule review, approval, scheduling, or execution control:

1. Show the full SQL to the user.
2. Get explicit confirmation.
3. For DDL or mixed (DDL+DML) batches, enforce the DDL Change Traceability rule below when composing `--reason`.
4. Submit the task: `scripts/sql-task.sh submit --sql-file <file> --reason <reason> --source-client <client> --client-confirmed`
5. Inspect status: `scripts/sql-task.sh detail --task-id <taskId>`
6. If review fails, explain the issue and update with `scripts/sql-task.sh update` after confirmation.
7. Submit approval only when `canSubmitApproval=true`.
8. If status is still `toApprove` after an approval operation, do not treat the task as stuck. Query `detail` or `execution-detail`. If `approvalCandidates` contains one current approval node and the user has explicitly authorized approval, call `scripts/sql-task.sh approve` again for the next node. Repeat until `canExecute=true`, approval is rejected, or no unique approval node can be inferred.
9. Execute only when `canExecute=true` and the user has confirmed execution.
10. Use execution-detail or subtask tools to diagnose failed or unclear execution states.
11. Use suspend, resume, stop, cancel, or cancel-execute only when the user requests that operation.

### DDL Change Traceability (required for DDL / mixed batches)

DDL and mixed (DDL+DML) SQL Tasks are high-risk and often irreversible. A downstream AI approval node enforces traceability and will **reject** any DDL/mixed task that lacks it. To avoid auto-rejection, when the SQL contains any `ALTER`, `CREATE`, `DROP`, `TRUNCATE`, or `RENAME` (or a batch mixing DDL with DML), the submission `--reason` (which becomes the task comment) MUST include at least one of:

- A related GitLab merge request link, e.g. `https://gitlab.addx.ai/<group>/<repo>/-/merge_requests/<iid>`. The MR should actually correspond to this change — an unrelated MR does not satisfy the rule and will be rejected on review.
- Or a sufficient change explanation covering: why the change is needed, the affected scope, and the rollback plan. A near-empty or boilerplate reason (shorter than ~30 characters, or with none of these elements) is treated as insufficient and rejected.

Additional expectations the AI approver checks for DDL:
- Provide a real rollback SQL (`--rollback-sql` / `--rollback-sql-file`); an empty rollback on a destructive change is a strong rejection signal.
- Prefer online DDL for large or hot tables (`ALGORITHM=INSTANT, LOCK=NONE` when applicable, otherwise gh-ost / pt-online-schema-change) instead of a bare `ALTER`.
- Keep the SQL consistent with the owning service's code (schema-as-code: also update the repo's migration/`init.sql` and ORM models in the related MR).

When composing the task for the user, proactively ask for the MR link or a complete change rationale before submitting a DDL/mixed task, and warn that it will be auto-rejected otherwise.

Pass `--datasource-id <id>`, `--database-name <db>`, or `--schema-name <schema>` when the user explicitly chooses context for this run. Omit them only when the external user configuration defaults should be used.

Prefer `--sql-file` for multiline SQL. Use `--sql` only for short one-line SQL. Do not pass both.

The script sends `useSessionPersistence=false` by default. Use `--use-session-persistence` only when the user explicitly asks to keep SQL window session state for this run.

The script sends `ignoreTips=true` to NineData after the agent has collected the required prompt-level confirmation. This does not make `sql-execute` the normal path for DML/DDL. Ordinary non-DQL business changes must use SQL Task.

SQL Task lifecycle terms:

- `cancel` means cancel the whole SQL Task workflow before it should continue.
- `stop` means terminate the task through the workflow terminate operation.
- `cancel-execute` means cancel a scheduled or pending execution request, not the whole task.
- `execution-detail`, `subtask-list`, and `subtask-detail` are read-only diagnostic tools.
- `approve`, `reject`, and `transfer-approval` are high-risk approval actions and require explicit user confirmation plus `--client-confirmed`.
- Multi-step approvals are common. One `Approved` log entry does not guarantee the task is executable. Trust `status`, `canExecute`, and `approvalCandidates`: while status remains `toApprove`, continue the approval loop only when the user has authorized approving the task.

## Response Requirements

Always include these fields when reporting the result:

- `requestId`
- `executionId`, when returned by NineData
- `decision`: `EXECUTED`, `REJECTED`, `NEED_CONFIRMATION`, `NEED_SQL_TASK`, or `FAILED`
- datasource, database, and schema context
- SQL type and risk level, when returned
- preflight summary
- affected rows or result preview
- masking and truncation status
- next action when SQL was not executed

For SQL Task tools, always include:

- `requestId`
- `taskId` and `workflowId`
- `status` and `statusDesc`
- `ruleCheckSummary`
- `canSubmitApproval` and `canExecute` as best-effort hints
- `approvalCandidates`, `nextApprovalNodeId`, and `approvalRound`, when returned
- `nextAction`
- `consoleUrl`
- node id and subtask error summary when using execution diagnostic tools

When `consoleUrl` is returned by any SQL Task command, the final user-facing response must include that jump link so the user can open the SQL Task detail page.

## Examples

### ❌ Bad

User: "Delete the rows in `orders` where status = 'test'."

```bash
# Wrong: pushing a state-changing DELETE through read-only sql-execute,
# auto-confirming, and bypassing the governed SQL Task workflow.
scripts/sql-execute.sh --sql "DELETE FROM orders WHERE status='test'" \
  --reason cleanup --source-client claude-code --client-confirmed
```

Why this is wrong: `DELETE` is non-DQL and may change state. It must go through a SQL Task workflow with rule review and approval, after the agent shows the full SQL and the user confirms. `sql-execute` is only for read-only DQL and metadata checks.

### ✅ Good

User: "Delete the rows in `orders` where status = 'test'."

```bash
# 1. Show the full SQL to the user and get explicit confirmation first.
# 2. Route the non-DQL change to a governed SQL Task workflow.
scripts/sql-task.sh submit --sql "DELETE FROM orders WHERE status='test'" \
  --reason "remove test orders" --source-client claude-code --client-confirmed

# 3. Inspect status, follow rule review / approval, and execute only when canExecute=true.
scripts/sql-task.sh detail --task-id <taskId>
```

Why this is right: state-changing SQL is confirmed with the user, then submitted as a SQL Task so NineData performs rule checks, approval, execution control, and audit logging. The agent never bypasses the platform decision.

## References

- Tool list: `references/tools.md`
- SQL execution: `references/sql-execute.md`
- SQL Task workflow tools: `references/sql-task.md`
- OpenAPI authentication: `references/openapi-auth.md`
- Safety boundary: `references/safety-boundary.md`
- Client installation: `references/client-install.md`

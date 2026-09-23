# Safety Boundary

## Trust Model

The agent is not a database principal, and the local scripts are not a policy engine. NineData platform policy is the final authority for whether SQL may execute.

## Mandatory Rules

- Do not connect directly to databases.
- Do not store database usernames or passwords in the skill.
- Do not expose AK/SK values in chat, logs, or command output.
- If SQL was generated or modified by the agent, or if SQL is not a query, get explicit user confirmation first.
- Route read-only DQL and metadata checks to `sql-execute`; route DML, DDL, DCL, TCL, state-changing procedure calls, and mixed SQL batches to SQL Task.
- If SQL type is ambiguous, ask for clarification or use SQL Task. Do not default ambiguous SQL to `sql-execute`.
- The script sends `ignoreTips=true`; treat this only as a transport of the agent-collected confirmation, not as permission to skip confirmation.
- SQL Task `submit`, `update`, `submit-approval`, `execute`, `suspend`, `resume`, `stop`, `cancel`, `cancel-execute`, `approve`, `reject`, and `transfer-approval` actions are side-effect operations. Call them only through `scripts/sql-task.sh <action>` after explicit user confirmation and pass `--client-confirmed`.
- Approval operations are high-risk: never approve, reject, or transfer based on inference. The user must explicitly request the exact action.
- Use execution-detail and subtask tools for diagnosis before proposing retries or workflow operations when execution fails.
- Do not rewrite and retry SQL to bypass a rejection.
- Do not split high-risk SQL into smaller statements to bypass rule checks.
- Do not call `sql-task.sh update` to implicitly cancel or reset a workflow. It only updates the task and lets NineData workflow decide whether the state transition is valid.

## Platform-Enforced Policy

The platform must enforce:

- SQL parsing and statement splitting
- SQL type detection
- permission checks
- rule preflight checks
- environment and datasource boundary checks
- query throttling
- timeout, row count, and byte-size limits
- sensitive data masking
- audit logging

## Agent Response

If `decision=EXECUTED`, summarize the execution result and evidence.

If `decision=REJECTED`, explain the platform rejection and stop.

If `decision=NEED_SQL_TASK`, tell the user that this operation must go through a NineData SQL task or approval workflow.

If `decision=NEED_CONFIRMATION`, ask the user to confirm the full SQL before execution.

If `success=false`, report the requestId and error message without exposing secrets.

For SQL Task output:

- Treat `canSubmitApproval` and `canExecute` as best-effort hints.
- Submit approval or execute only after checking the latest detail and user intent.
- Approve, reject, or transfer approval only when the user explicitly says to do so. If node selection is unclear, ask for clarification or pass an explicit `--node-id`.
- If an operation returns `ok=false`, report `error.message` and `error.detail.requestId` when present.
- Do not print full SQL from local files in error logs; show SQL to the user only as part of the confirmation step.

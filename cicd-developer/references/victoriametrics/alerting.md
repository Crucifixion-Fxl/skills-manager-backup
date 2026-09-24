# VM alerting through the existing application

Path: application overlay -> existing Argo Application -> business-namespace
VMRule -> vmalert -> Alertmanager -> existing PagerDuty Service. No Grafana hop.

Developers own VMServiceScrape and VMRule in their existing repository and overlay.
Add new rule files to that overlay's Kustomization. Platform owns evaluator
selection and notification routes/secrets. An existing Application does not need
re-registration, a separate alert directory, or a second Application just to add
rules. Do not use the standalone observability-platform onboarding compiler as a
mandatory prerequisite for this workflow.

## Actual readiness, not a global assumption

Read the actual Application repository/revision/path, destination, automated sync
and AppProject permission. Read VMAlert namespace/rule selectors and Operator watch
scope. Require exactly one evaluator. Check the loaded notification route and
verified PagerDuty Service identity, not merely ESO Ready. These checks are per
cluster and namespace; a working CN evaluator does not prove US is configured.

The application context JSON contains exactly: cluster, id, repository, revision,
path, namespace, environment, application, project, notification_service. It is a
local snapshot of independently verified facts, not a central registry or an
authorization token. `id` identifies the alert set, while notification_service
identifies the approved notification route and may be shared across A/B alert sets.
The developer does not need Vault paths, secret names or integration key values.

The checker validates rendered alert VMRule identity/shape only. It does not
prove the context's authenticity, query scope, expression correctness, discovery,
or notification delivery. Do not fabricate context to bypass a failed readiness
gate. The shared evaluator is a trusted GitOps model, not hard tenant isolation.

## Rule and notification contract

Resource and alert-label namespace both equal the application namespace.
Use metadata label monitoring.addx.io/alert-set for attribution and alert labels
notification_service, namespace, environment and severity for routing. Preserve
existing Kustomize labels; evaluator-profile is reserved to platform owners.

Platform must explicitly confirm whether critical and warning route to the app
service. Existing generic warning-discard/default routes are not permission to
assume a business route is usable. Application rules never change shared routing.
Group notifications by alertname, severity, environment, namespace and stack;
retain pod details without splitting every pod into a separate phone incident.

Use JSON-quoted expression and annotation slots so apostrophes/newlines remain
valid YAML. Do not copy Grafana noDataState/execErrState fields into VMRule.
Expressions must return only violating series, preserve pod identity and be
unit-tested for threshold, pending, missing and restored data.

A/B rules remain included when workloads scale to zero. Deleting the Application
or namespace deletes its rules, so that lifecycle requires separate platform
review. Do not promise rule persistence across deletion. Accept the VM route and
coordinate retirement of legacy Grafana notifications to avoid duplicate paging.

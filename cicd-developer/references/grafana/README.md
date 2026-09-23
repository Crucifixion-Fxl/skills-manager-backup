# Grafana Dashboard source management

Load this topic only for the `manage-grafana-dashboard` workflow. The local
skill edits pure Dashboard JSON in the reviewed Dashboard-as-Code repository;
protected GitLab CI is the only component that communicates with Grafana.

- Read [repository contract](repository-contract.md) before selecting an
  environment, cluster, VictoriaMetrics service, namespace, or data source.
- Read [workspace isolation](workspace-isolation.md) before cloning, committing,
  pushing, or opening a Merge Request.
- Read [security and deletion policy](security-and-deletion-policy.md) before
  any source removal or action that could cross the local-source boundary.

When a developer supplies PromQL, pass it through verbatim. `cluster` remains
data-source ownership metadata only, never an injected or mandatory query label.
The source validation is intentionally basic and does not query VictoriaMetrics.

After a successful source-changing operation, the workflow submits only the
isolated Dashboard checkout to the fixed Dashboard repository and returns its
MR URL. Tell the requester to send that URL to Ops for review, then return the
expected public Dashboard URI from the stable UID and approved static template.
State that the URI is expected only after merge plus successful deploy/verify;
the local skill does not merge it or contact Grafana.

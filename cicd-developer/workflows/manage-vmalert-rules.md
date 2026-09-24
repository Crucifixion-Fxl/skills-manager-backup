---
name: manage-vmalert-rules
description: 在现有业务部署目录维护 VM 告警规则，复用 ArgoCD 自动同步和已确认的 PagerDuty 路由。
---

# Workflow: application-owned VM alert rules

Read `references/victoriametrics/alerting.md`,
`references/data/stop-conditions.yaml` and `references/data/permission-boundaries.yaml`.
Write only the caller's existing application overlay, focused rule tests and the
existing CI configuration needed for those tests. Do not create an Application,
AppProject, central alert registry, platform route, Secret or Grafana resource.
A scrape request additionally uses the existing scrape workflow.

## Step 1. verify the existing application and notification route

[precondition]
An existing application requests business alert rules.

[action]
- Read the live Argo Application and its Git-owned source. Match the caller origin,
  targetRevision, path, cluster and destination namespace. Confirm automated sync
  is already enabled and AppProject permits VMRule in that business namespace.
  Do not create a second Application or enable auto-sync merely for an alert rule.
- Check the actual VMAlert namespace/rule selectors and Operator watch scope.
  Require exactly one intended evaluator to select the candidate VMRule. A running
  VM stack or an Argo Synced status does not prove discovery. Do not assume the
  same selector behavior across CN and US.
- Require platform evidence that notification_service + namespace + environment
  matches a loaded Alertmanager route to the intended existing PagerDuty Service.
  Verify severity policy and precedence: a generic warning discard or default
  receiver is not an app-specific route. Check the secret reference's identity
  and ESO Ready through authorized platform staff; never read the key value.
- Record observed application context as JSON: `cluster`, `id`, `repository`,
  `revision`, `path`, `namespace`, `environment`, `application`, `project`,
  `notification_service`. `id` is a local alert-set slug (at most 40 characters),
  not a platform registration ID; `notification_service` is the verified route
  identity. Retain Git revision and live evidence references alongside the JSON.
  Do not invent a platform registration or require the old standalone compiler.
- Missing discovery, permission, route identity or access evidence: STOP before
  writes with a bounded Ops Todo. Missing rules themselves are the output of this
  workflow, not a reason to demand a separately registered alert application.

[validate]
Existing source/destination, single-evaluator discovery and notification routing
are verified. A fabricated context JSON cannot replace these checks.

[output]
Observed application context and readiness evidence, or the exact missing gate.

## Step 2. add rules to the existing application overlay

[precondition]
Step 1 proves the application can deploy, evaluate and route these rules.

[action]
- Confirm expressions, thresholds, severity, pending duration, no-data behavior
  and region/A/B scope. Preserve business meaning; do not guess a translation of
  Grafana Reduce/Threshold. Return only violating series, not a comparison with
  `bool`; zero-valued series still activate alerts.
- Use `recipes/victoriametrics/vm-alert-rule.yaml.tmpl` in the observed overlay.
  Add it to that overlay's `kustomization.yaml` resources. Preserve existing
  VMServiceScrape and other resources. Do not create a separate raw directory.
- VMRule metadata namespace and alert label namespace both identify the business
  namespace. Resource/group names use the local alert-set ID; resource attribution
  is `monitoring.addx.io/alert-set`. Preserve existing Kustomize resource labels,
  but never add an evaluator-profile label or change platform selectors.
- Encode expression_json, summary_json and description_json with `json.dumps`;
  never interpolate raw quotes/newlines or do a second template pass. Set interval
  1m, explicit `for`, notification_service/environment/namespace/severity and
  summary/description. Pod alerts retain `{{ $labels.pod }}` in the summary.
- Optional static labels: stack, region, project, alert_kind, resource, scope.
  Optional annotations: dashboard_url/runbook_url. Keep A/B rules present when
  workloads scale to zero; do not condition inclusion on replica count or metric
  presence. If shutdown deletes the Application/namespace, STOP for lifecycle
  review rather than claim that the same overlay preserves rules after deletion.

[validate]
Rendered rules remain in the observed business namespace, retain exact route
labels and are included for both active and scaled-to-zero stacks.

[output]
Application VMRule and Kustomization changes, focused tests and necessary existing
CI changes. No platform resources, credentials or Grafana YAML.

## Step 3. validate and submit

[precondition]
The candidate and confirmed expression behavior are recorded.

[action]
- On the dedicated build host or CI, render the actual overlay with the repository's
  pinned Kustomize version. Run applicable application validators. Extract the
  alert VMRules from that render for `$skill_root/validators/check_vmalert_rules.py
  --context <absolute-context-json> <absolute-rendered-alert-rules.yaml>`.
  Check all alert rules in this alert set, not only changed rules. Preserve and
  separately validate pre-existing recording rules through their own workflow.
- This validator only compares shape and supplied context. PASS does not prove
  context authenticity, routing authorization, expression scope or runtime state.
  Recheck Step 1 source and routing evidence before submission.
- Extract `spec.groups` into a standard rule file. Use the deployed vmalert
  version's dry-run/unit-test tools, or promtool for PromQL-compatible expressions.
  Test below/equal/above threshold, pending time, absent/restored series, and pod
  label preservation. Missing tools or failed checks is STOP. Do not run tests on
  this workstation or copy credentials/.env to the build host.
- Make the existing application CI run contract/expression checks for rule edits.
  Submit the normal repository feature-branch MR, with observed context, test
  results and activation plan. Human merges; never approve/merge on their behalf.

[validate]
Rendered manifests, expression tests and application checks pass. Context/routing
are independently verified; static PASS is not an authorization receipt.

[output]
Application MR and evidence. Until merge and live checks pass, report pending.

## Step 4. verify automatic effect

[precondition]
The user has merged the application MR; authorized runtime evidence is available.

[action]
- Confirm the existing Argo Application synchronized the exact commit and the
  intended evaluator loaded the rule groups. Observe two successful 1m evaluation
  cycles. Synced alone does not prove evaluation.
- Verify active/inactive instances, labels and no-data behavior. A scaled-to-zero
  side retains rules and resumes evaluation when metrics return. An empty result
  differs from a datasource error; verify evaluator-error monitoring separately.
- Confirm notification evidence for the exact PagerDuty Service. A real incident
  test needs an agreed on-call window/authorization; record receipt/resolve URL
  and accepting person, never the key. Routine edits may reuse a still-valid
  platform receipt; do not place a test phone call on every rule update.
- During migration, accept the new route before retiring old Grafana alerts;
  coordinate cutover so the same condition does not page twice. Rollback uses an
  application Git revert, not deletion of the PagerDuty Service or integration.

[validate]
Exact commit synchronized, rules evaluated and intended notification receipt valid.

[output]
Deployed commit and evaluation/routing evidence, or a precise pending gate.
Grafana is not a notification hop.

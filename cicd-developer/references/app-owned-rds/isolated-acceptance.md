# Platform-only isolated PostgreSQL acceptance preparation

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/257
Root Issue: https://gitlab.addx.ai/issues/sre/dev/-/issues/168

This is an explicit platform acceptance preparation branch of `add-rds`, selected only
when the user is working on DEV/k8s#57 / Root#168. It is not selected by an application's
`staging` environment, and does not change shared database routing or the production live gate.
The Encyclopedia exception remains separate. Only AWS 390709477306/us-east-1, PostgreSQL 16
and a reserved `rds-e2e-*` app on its derived dedicated cluster are supported.

## Prepare without activating

Use the merged [acceptance kit](https://gitlab.addx.ai/DEV/k8s/-/tree/3de28b04cb379dd9a177e7e17004eaf3c608afe5/docs/requirements/57/acceptance-kit).
All owner identities, network and privilege inputs remain platform-owned. This helper accepts
exactly six fields, derives the namespace/app, and exposes no admin, username, password, grants,
ProviderConfig, Vault path, public-access or arbitrary namespace field.

Create a JSON input containing `account`, `region`, `cluster`, `run_id`, `engine_version`
and `db_security_group`. The cluster must equal `us-eks-<run_id>-staging`. The SG and
engine minor are platform-reviewed candidates; syntactic validity does not prove existence,
private ingress, version availability or authorization. If the new SG is not yet known,
keep a clearly labelled local test fixture; never use its output as a bound deployment.

```sh
python3 validators/prepare_rds_acceptance.py --inputs /absolute/inputs.json --output /absolute/new/handoff
```

The output is `database.yaml.tmpl` plus a `PREPARED_NOT_AUTHORIZED` manifest. It does
not create an Application, edit a kustomization, call AWS/Vault/Kubernetes, or supply
application credentials. Keep these files outside active roots. Existing output directories
are rejected. Do not rename/apply the template merely because the command succeeded.

## Before a real request

Complete the exact merged kit runbook A–D: owner repository revisions, new bounded cost
approval, new cluster/issuer/network readback, safe provider/functions/CRD readiness,
Vault four-identity positive/negative checks, app admission/RBAC rejection and no collisions.
The old cluster/OIDC and expired authorization cannot be reused. Preserve cumulative
$45 stop-new/$60 review/$75 cap and old charges; approval remains separate from this helper.

Use the same inputs to generate the owner-separated bound kit. Parse the template as JSON/YAML
and require exact object equality with its `request/database.yaml`; any drift stops activation.
Review the exact materialized commit and its separate request Application before registering it.
Only then, under the separately approved activation commands, may the platform owner promote
this template to the request source. No raw Instance or manual SQL can substitute for this path.

The consumer remains the kit's `consumer/` source, using its exact app-only reader Store and
Access output. It must never read the admin/password source keys. Run all 25 real cases in the
kit, including SQL/TLS, identity denial, no-adopt, rotation/recovery and deletion protection.
A prepared template, a green CI or this helper's manifest never proves live acceptance.
The new isolated path does not enable the normal production route; that still requires its
own exact-target evidence and approval.

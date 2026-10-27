# One approved StreamPark staging pull set

This is a fixed instance authorized directly by the human user on 2026-10-17,
not a new application onboarding route or a caller-selected policy waiver.
Owning correction: [engineering/skills #309](https://gitlab.addx.ai/engineering/skills/-/issues/309).
Root: [DATA/streampark #1](https://gitlab.addx.ai/DATA/streampark/-/issues/1).
Deployment record: [Task #3 approval receipt](https://gitlab.addx.ai/DATA/streampark/-/issues/3#note_740984).
The receipt tracks the direct approval; a source-authored note is not a generic
authorization mechanism.

Approved scope ID: `DATA-streampark-task3-stage-job-pull-v1`.
Approved immutable scope SHA-256:
`d8afc427cd2c88f677a89583588ca20ed7f48ec8f40488401bacb29459b5f6e6`.
The human approved exactly five additional resources and associated permissions,
a 90-day project pull-only robot and a single-path Vault reader. The earlier
production eight-resource exception is separate and is not inherited here.

The only admitted source is `DEV/k8s` in explicit `--repo-context k8s`, relative path
`clusters/aws-390709477306-us-staging/streampark-access/registry-pull.yaml`.
The [non-secret fixed manifest](streampark-stage-pull.yaml) has complete-file SHA-256
`63431c97819e39e6e52fdb119797a4245c962f11d1515f0bae5442457b8844b7`.
The validator's constant binds those exact bytes, including all five documents,
metadata, RBAC rules/subjects, Store spec and ExternalSecret template. No approval
flag, metadata label, user-supplied hash or external contract file can override it.
Any content or formatting change fails closed and requires a fresh reviewed rule
update with authorization for any changed scope. Symlinks do not qualify.
The Vault path validator recognizes the one canonical key only while checking
these complete approved bytes at the exact tracked source path in a verified
DEV/k8s checkout, or this authoritative skill fixture. The added per-file pattern
never carries over to a neighbouring manifest. It does not add docker-builder to
a wildcard platform enum or change the generic Vault rules inventory. The reference
inventory explicitly validates this one five-document asset and its digest; all
other reference YAML inventories remain single-document and unchanged.

All objects are in `staging-streampark-jobs`:

| Kind | Name |
|---|---|
| ServiceAccount | eso-vault-reader-streampark-job-pull |
| Role | eso-vault-reader-streampark-job-pull-tokenrequest |
| RoleBinding | eso-vault-reader-streampark-job-pull-tokenrequest |
| SecretStore | vault-streampark-job-pull-reader |
| ExternalSecret | streampark-job-pull |

The Role permits TokenRequest only for that reader ServiceAccount and is bound
only to `external-secrets/external-secrets`. The Store uses the fixed Ops Vault
server, `jwt-eks-us-staging`, role `eso-platform-streampark-job-pull-reader`, audience
`https://vault-us-new.addx.live` and 600-second TokenRequest. The reader consumes
only `prod/docker-builder/application/streampark/registry-pull/us-staging` in KV v2.
The ExternalSecret targets only the owned `streampark-job-pull` dockerconfigjson.
The dedicated Harbor identity is project pull-only for 90 days; no push credential,
shared registry identity or operator secret is supplied to jobs. The namespace is
a trusted shared execution boundary: a Pod creator can access this dedicated pull
credential through a Pod; this is not a tenant credential confidentiality claim.

The existing central platform-control-plane Application owns the fixed set.
app-runtime gains no RBAC/SecretStore privileges. Generic default rejection,
PostgreSQL rules and all unrelated clusters, paths and objects remain unchanged.
This reference supplies review evidence only. It does not authorize a deployment,
grant live permissions, amend a Vault policy or certify runtime readiness.
Deployment still requires the actual enforce fence before namespace creation,
reader JWT positive/negative checks, dedicated robot and Vault delivery, ESO
refresh, private image pulling and the rest of Task #3 acceptance.

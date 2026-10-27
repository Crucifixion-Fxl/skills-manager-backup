# Fixed CN staging shared PostgreSQL platform credential consumer

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/320
Root Issue: https://gitlab.addx.ai/infra/mlflow/-/issues/1
Deployment Task: https://gitlab.addx.ai/infra/mlflow/-/work_items/2

This is a closed **validation contract**, pending reviewed merge and plugin
installation. It grants no runtime permission, generic custom-mount override,
application path exception, deployment approval or production access.

## Authority and fixed source

The human user authorized the Task2 operator to complete CN staging operational
prerequisites through reviewed MRs, merge passed changes and verify staging;
production remains excluded. The platform isolated Vault mount/reader/Store
were accepted before credential consumption. Platform ownership and credential
isolation are recorded in DEV/k8s Work141 and its merged !3194/!3197/!3199
packets, not inferred from annotations in an incoming manifest.

- Target: Tencent `cls-riukakjb`, namespace `crossplane-system`, in-cluster Vault
  cluster ID `b7c16def-5f5e-398a-2211-d06ae2cf61b6`.
- Original platform role OID97112 and escrow v1 were recovered and cleaned once;
  [Task receipt763297](https://gitlab.addx.ai/infra/mlflow/-/work_items/2#note_763297).
  Never replay bootstrap/recovery or reset the credential.
- Existing SecretStore `shared-postgres-platform` uses the isolated KVv2 mount
  `shared-db`, existing exact reader `shared-postgres-platform-reader` and SA
  `shared-postgres-vault-reader`. ESO controller trust is not reduced by this
  validation contract. No new policy, auth identity, RBAC or mount is created.
- Consumer candidate is DEV/k8s commit
  `eccaf09830c0262df9e95c18015a8b843e3f9da1`, not yet merged or live accepted.
  Its only credential manifest is the tracked regular source
  `clusters/tencent-100052802231-cn-staging/cicd/crossplane/post-install/shared-postgres-platform-connection.yaml`,
  complete-file SHA256
  `c8c040f6ab710190ccdf6e23114eb85aa9d590cd72defd529ffd437f201f1125`.
  The identical fixture beside this document is authoritative validation input,
  not a second deployment source.

The document is exactly one ExternalSecret, target of the same name
`shared-postgres-platform-connection`, using the existing Store and exactly
endpoint/port/username/password from `platform/cn-staging/postgres`, version1.
Its guarded v2 templates, private endpoint/user/port literals, four target keys,
Owner/Retain and Argo retention annotations are part of the complete hash.
No password or token appears in source or fixture.

## Validation boundary

The generic validator previously normalized every relative reference into
`secret/...`; it failed four references as `secret/platform/cn-staging/postgres`
without resolving this already accepted non-default Store mount. Preserve that
failed result. Do not change the source to the default mount to make it pass.

Only the complete fixture in this installed skill, or the exact tracked source
path in a verified `gitlab.addx.ai/DEV/k8s` checkout, qualifies. File and parent
symlinks, untracked source, wrong repository, adjacent paths, production paths,
format changes, extra documents or any field change do not inherit it. At both the
reserved trusted source slot and the authoritative fixture, changed/empty
content fails closed even when it would match ordinary canonical Vault rules.
A missing authoritative fixture fails validation of its containing scope;
lexical or symlink scan aliases cannot hide reserved content from the guard.
This includes separate aliases to the same external target. An unresolved
reserved path cannot be assumed outside the scan; its integrity is checked
conservatively for rejection only. Neither physical resolution nor recovered
lexical Git context can grant positive source approval. The original source must be
reviewed again and this contract changed through MR for any future rotation.

For this exact source only, the internal legacy `secret/` normalization is
resolved to `shared-db/` and matched against exactly
`shared-db/platform/cn-staging/postgres`. No generic regex, enum, command-line
flag or manifest annotation enables arbitrary custom mounts. Default app path
rules and the existing StreamPark contract remain unchanged. Git origin and
tracking checks prevent accidental use from other checkouts; they do not
protect against an administrator who controls local Git configuration or the
validator process itself.

Before source activation, Task2 must still recheck accepted immutable recovery,
actual Store/mount/reader identity, fixed escrow version1 and absent same-name
ExternalSecret/Secret. After GitOps publication, verify ESO Ready and exact
Secret owner/type/four-key equality with version1 in memory without logging
values. This validator cannot prove those live conditions, provider login,
tenant ACLs, COS, application authentication, large artifacts or recovery.

## Regression and delivery

Public fake/local Git tests prove the original failures, exact-source/fixture
acceptance, byte/field mutations, neighboring/production/foreign/untracked
sources, symlink and fallback rejection. The existing required offline-e2e job
runs the new test module while preserving every previous module and gate.
Independent review/current-head CI/Driver/Auditor precede merge. Install via the
managed `addx@addx` plugin route and rerun the consumer's complete applicable
specialty validator before it can pass. A manual explanation never replaces a
nonzero required validator. Keep Task2 open until real staging acceptance.

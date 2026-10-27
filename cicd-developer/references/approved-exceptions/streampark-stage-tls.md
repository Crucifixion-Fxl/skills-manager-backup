# StreamPark staging TLS readers — source candidate

This batch implements the source-only exception approved by the human user on
2026-10-20. Scope SHA-256:
`20f75d98325844e169b67de011b8c6761f48e1e1c4f8b2866a889738edc18523`.
Approval and current dependency evidence:
[Task #3](https://gitlab.addx.ai/DATA/streampark/-/work_items/3#note_766969).
The approval receipt is linked from Task #3. A green check is not deployment approval.

The two source files contain eight objects, four in each namespace. Each Role
only creates a TokenRequest for its own authentication-only ServiceAccount;
only external-secrets/external-secrets is bound. ServiceAccounts disable token
mounting. Stores use Ops US Vault, secret KV2, the existing jwt-eks-us-staging
mount, the exact separate reader role/subject and a 600-second audience-bound token.
app-runtime receives no RBAC or SecretStore permission.

| Side | Namespace | Vault role/policy | Exact data paths |
| --- | --- | --- | --- |
| server | staging-streampark-staging-builder | eso-platform-streampark-staging-builder-server-reader | secret/data/staging/docker-builder/service/streampark-builder/server-tls |
| client | staging-streampark-staging | eso-platform-streampark-staging-builder-client-reader | secret/data/staging/docker-builder/application/streampark/client-tls; secret/data/prod/streampark/targets/us-staging/access |

The role/policy descriptions below refer to the separate, unmerged
[DEV/vault-policies candidate at 3cc875ac](https://gitlab.addx.ai/DEV/vault-policies/-/tree/3cc875acf0af295e5dcb13819455a3123e7f6f66).
They are not published by this skills MR; no live Vault permission is verified here.

That candidate allows read on those paths and token self lookup/revoke/capabilities,
not list, wildcard, registry writer or business-secret access. TTL, max TTL and
explicit max TTL are 600 seconds; default policy is disabled. Existing issuer,
mount, access publisher, production readers and jobs pull identity are unchanged.
The access path intentionally reuses the existing machine publisher; it is not a
new writer or a personal kubeconfig. Cert keys will be ca.pem/cert.pem/key.pem;
access key will be kubeconfig. This batch contains no credential values.

## Runtime boundary

No merge, deployment, certificate issuance, Vault publication, Namespace,
Application, admission policy, privileged Pod, node capacity or Harbor identity
is authorized by this batch. No ExternalSecret or placeholder Secret is added.
The proposed Docker address remains
`tcp://streampark-builder.staging-streampark-staging-builder.svc.cluster.local:2376`
with TLS_VERIFY=1; that service is not yet deployed. The platform still needs real
staging TLS/access material, protected HTTPS/login and full JDBC/JAR/Flink/state
acceptance before developer handoff. Production certificate material is not reused.

Future activation requires separate exact owner/registration and admission
review, issuer/rotation ownership, protected Vault bootstrap capability, real
reader JWT positive/negative tests, ESO Ready, valid/invalid mTLS, ARM64
build/push/cold-pull and actual runtime evidence. Existing shared CNI gates remain;
this batch does not activate CNI. Keep the original node do-not-disrupt protection.
No Jenkins, nat-egress-arm64, PodENI or business Secret.

## Validation

The fixed two-file hashes close all eight objects and reject changed objects,
cardinality, namespace, Role/TokenRequest subject, endpoint, role, audience,
TTL, annotations and symlink traversal. Missing companions also fail closed.
This is an offline source check, not Kubernetes admission or ESO acceptance.
The exact path rules do not broaden the generic Vault consumer path inventory.

Root Issue: https://gitlab.addx.ai/DATA/streampark/-/issues/1

## Fixed complete files

- `clusters/aws-390709477306-us-staging/streampark-builder/readers/server.yaml` — SHA-256 `4c1bb30f989b21829e5a6f9ae5fb4df283c5e2e2fb6bb5c52617e136dde220bf`; fixture [streampark-stage-tls-server.yaml](streampark-stage-tls-server.yaml).
- `clusters/aws-390709477306-us-staging/streampark-builder/readers/client.yaml` — SHA-256 `f1728dd006012c454c821d980c173febcc21da7ef2684b8376a41749496d45e1`; fixture [streampark-stage-tls-client.yaml](streampark-stage-tls-client.yaml).

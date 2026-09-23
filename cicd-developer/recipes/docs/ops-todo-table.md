# Ops Todo table — emitted at workflow end when there are items the workflow
# could not complete itself.
#
# Required slots: each row needs four columns.
#
# Workflow MUST:
#   1. Use this exact column order (resource / why / input / acceptance).
#   2. Emit a row for EVERY external dependency the workflow detected and
#      cannot resolve itself: DNS, ArgoCD Application registration on a
#      cluster the workflow does not self-manage, account-level IAM trust,
#      cross-VPC peering routes, third-party console actions (Sentry org,
#      Casdoor SSO config, ...), Kafka topic ACL by ops, etc.
#   3. If $gitlab-issue-sop is available, also delegate each row to a
#      GitLab issue (one issue per row, not a single mega-issue).
#
# DO NOT use this for items the workflow CAN handle (those go into the
# workflow's [action] block). The Ops Todo is the externalization boundary,
# not a "TODO later" list.
#
# v2 当前**已知未覆盖的设计议题**（workflow 命中这些场景必须出 Ops Todo 行，**不要** 凭印象现编 workflow）：
#
# - **TKE 集群引用 AWS / AWS CN 资源**（TKE pod 写 AWS S3 / Aurora / etc）
#     add-irsa-role / add-s3-bucket / add-rds 等 workflow 在 cloud=tencent 时 STOP，
#     必须的 Ops Todo 行：
#       "TKE 集群跨云访问 AWS [resource_type]：v2 当前无对应 self-serve workflow。
#        业务确认（a）暂不需要，或（b）改用腾讯云 COS / CDB（等同资源），
#        或（c）走 AK/SK 长效凭证 + Vault path 规划（运维 case-by-case 决策）"
#     待业务真实需求出现 + 频率 > 1 次时考虑加 workflow `add-tencent-cos.md` 或
#     `add-aws-cross-cloud-resource.md`。
#
# - **GCP 集群的 Sentry 自助接入**
#     add-sentry workflow 在 cloud=gcp 时 STOP；GCP 集群 Sentry 接入 v2 当前无路径。
#     Ops Todo："GCP cluster <name> Sentry 自助 v2 未覆盖；当前仅 EKS + TKE。
#                 业务确认 (a) GCP 应用是否真需要 Sentry / (b) 运维评估增加 GCP variant 必要性"
#
# - **kustomize labels block 推到 Rollout CRD（StatefulSet 是内置资源）**
#     v2 默认 patches 处理，不要给 Rollout 用 kustomize 顶层 `labels:` block。
#     如果用户偏好 labels block + 业务用 Rollout 必须自己加 patches 兜底。

## Ops Todo

| # | Resource | Why ops handles it | Input the user must provide | Acceptance criteria |
|---|---|---|---|---|
| 1 | (e.g. DNS A record `api.example.addx.live` -> ALB) | Aliyun DNS not in GitOps; ops account | ALB DNS name from Application sync output | `dig api.example.addx.live` resolves to the ALB hostname |
| 2 | (e.g. Cross-account IAM trust for app role assumption) | Account-level trust outside any single cluster's Crossplane scope | Source/Target account IDs, role names | `aws sts assume-role` succeeds from the source role |

If no ops items: workflow MUST still print the heading "## Ops Todo" followed by "(none)".

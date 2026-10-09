# K8s Context

CN 当前全部在腾讯云。下表集群标识来自 GitOps 目录，使用前以 `kubectl config get-contexts` 核实本地别名；不得因旧 AWS CN context 仍存在就使用它。详见 [CN 清单](../../../k8s-ops/references/cn-tencent-inventory.md)。

### AWS EKS

| Context | 账户 | 环境 |
|---------|------|------|
| aws-302571458622-us-prod | aws-302571458622 | us-prod |
| aws-002497567426-us-tech-service | aws-002497567426 | us-tech-service |
| aws-740315635167-eu-prod | aws-740315635167 | eu-prod |
| aws-010840394398-eu-tech-service | aws-010840394398 | eu-tech-service |
| aws-769494896000-us-data | aws-769494896000 | us-data |
| aws-769494896000-eu-data | aws-769494896000 | eu-data |
| aws-125710977284-sg-devops | aws-125710977284 | sg-devops |
| aws-343938550037-kr-dev | aws-343938550037 | kr-dev |

### GCP GKE

| Context | 项目 | 环境 |
|---------|------|------|
| gcp-a4xcloud-p-us-us-prod | gcp-a4xcloud-p-us | us-prod |
| gcp-a4xcloud-p-us-prod | gcp-a4xcloud-p | us-prod (old) |
| gcp-a4xcloud-tech-service-us-us-tech-service | gcp-a4xcloud-tech-service-us | us-tech-service |
| gcp-a4xcloud-tech-service-us-tech-service | gcp-a4xcloud-tech-service | us-tech-service (old) |

### 腾讯云 TKE

| Context | 账户 | 环境 |
|---------|------|------|
| tencent-100014919455-cn-main | tencent-100014919455 | cn-prod / prod-cn（cn-main） |
| tencent-100052802231-cn-staging | tencent-100052802231 | cn-staging / staging-cn |
| tencent-100052802231-cn-tech-service | tencent-100052802231 | cn-tech-service |

旧 AWS CN（`cn-eks-dev`、`cn-eks-staging` 等）、`tencent-100050722703-cn-staging`（旧腾讯 `100014919455` 的 staging-cn 集群 `cls-i860hdh9`）、`tencent-100014919455-cn-tech-service` 只可用于历史迁移记录，不能作为当前默认目标。CN staging 告警 / 排障一律落到 `cls-riukakjb`；两套 staging 的 VPC CIDR 都是 `172.17.0.0/16`，不能按 Pod/Node IP 段判断归属，以 cluster ID 和 CLB 归属账号为准。

### 其他

| Context | 环境 |
|---------|------|
| office-hangzhou | 杭州办公室 |

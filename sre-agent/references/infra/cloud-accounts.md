# 公有云账户与 VPC CIDR

### 海外 AWS

| 账户 ID | 环境 | Region | VPC CIDR | Profile |
|---------|------|--------|----------|---------|
| 302571458622 | us-prod | us-east-1 | 10.227.0.0/16 | aws-302571458622-us-prod |
| 002497567426 | us-tech-service | us-east-1 | 10.120.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | eu-central-1 | 10.110.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | ap-southeast-1 | 10.130.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | ap-northeast-1 | 10.201.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | ap-northeast-2 | 10.140.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | sa-east-1 | 10.200.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | eu-west-2 | 10.202.0.0/16 | aws-002497567426-us-tech-service |
| 002497567426 | us-tech-service | ap-southeast-2 | 10.180.0.0/16 | aws-002497567426-us-tech-service |
| 769494896000 | us-data | us-east-1 | 10.210.0.0/16 | aws-769494896000-us-data |
| 740315635167 | eu-prod | eu-central-1 | 10.220.0.0/16 | aws-740315635167-eu-prod |
| 010840394398 | eu-tech-service | eu-central-1 | 10.222.0.0/16 | aws-010840394398-eu-tech-service |
| 125710977284 | sg-devops | ap-southeast-1 | 10.223.0.0/16 | aws-125710977284-sg-devops |
| 343938550037 | kr-dev | ap-northeast-2 | 10.234.0.0/16 | aws-343938550037-kr-dev |

### GCP 项目

| 账户/项目 | 环境 | Region | VPC CIDR |
|---------|------|--------|----------|
| gcp-a4xcloud-p-us | us-prod | us-east4 | 10.230.0.0/16 |
| gcp-a4xcloud-tech-service-us | us-tech-service | us-central1 | 10.231.0.0/16 |
| gcp-a4xcloud-p | us-prod (old) | multi-region | 10.224.0.0/16 |
| gcp-a4xcloud-tech-service | us-tech-service (old) | multi-region | 10.225.0.0/16 |
| gcp-a4xcloud-p-eu | eu-prod | europe-west1 | - |
| gcp-a4xcloud-tech-service-eu | eu-tech-service | europe-west1 | - |
| gcp-a4xcloud-t | test | multi-region | 10.10.0.0/20 |
| gcp-a4xcloud-diversion | gemini-us | us-central1 | N/A |
| gcp-a4xcloud-diversion-eu | gemini-eu | europe-west1 | N/A |
| gcp-a4xcloud-p-gemini | gemini-us (old) | us-central1 | N/A |
| gcp-a4xcloud-p-eu-gemini | gemini-eu (old) | europe-west1 | N/A |

### 腾讯云（当前全部 CN 集群）

截至 2026-09-28，AWS CN 集群已弃用。CN 的默认排障与部署必须选腾讯云；不能因 Git 中保留旧目录或告警带 `cn-prod` 就选 AWS。完整来源见 [CN 清单](../../../k8s-ops/references/cn-tencent-inventory.md)。

| 账户 ID | 环境 | Region | GitOps 集群目录 |
|---------|------|--------|----------------|
| 100014919455 | CN prod（cn-main） | ap-beijing | tencent-100014919455-cn-main |
| 100052802231 | CN staging | ap-beijing | tencent-100052802231-cn-staging |
| 100052802231 | CN tech-service | ap-beijing | tencent-100052802231-cn-tech-service |

目录名不是 tccli profile 的保证。按 `tencent-cloud-cli` skill 枚举本地已有 profile、核实 UIN 后选择；新账户不能沿用 prod 凭据。VPC/CIDR 以该账户实时查询及 `argocd-apps`/`k8s` 对应目录为准，不把旧 AWS CIDR 套到新 TKE。

### AWS CN 历史识别（已弃用）

以下仅用于解释旧告警/迁移记录，不能作为当前部署或排障目标；没有当前 `cn-dev` 集群的证据时，不猜测它映射到 staging。

| 旧账户 | 历史环境 | 历史 VPC CIDR |
|--------|----------|---------------|
| 741924744516 | cn-prod | 10.236.0.0/16 |
| 589899215075 | cn-tech-service | 10.228.0.0/16 |
| 801447536674 | cn-dev / cn-staging | 以历史记录为准；cn-dev 曾为 10.237.0.0/16 |

### Azure

| 订阅 ID | 环境 | 租户 | VPC CIDR | Profile |
|---------|------|------|----------|---------|
| 5b72041d-c45d-421b-8563-e857e4f5017e | prod | PHOTON SAIL TECHNOLOGIES PTE. LTD. (jjkj01.onmicrosoft.com) | - | `az account set -s 5b72041d-c45d-421b-8563-e857e4f5017e` |

### IP → 环境映射

通过 IP 段辅助定位环境；IP/CIDR 可重叠，必须与账户、cluster label 和实时资源记录交叉核对：
- `10.227.x.x` → us-prod (aws-302571458622)
- `10.120.x.x` → us-tech-service (aws-002497567426, us-east-1)
- `10.110.x.x` → us-tech-service (aws-002497567426, eu-central-1)
- `10.130.x.x` → us-tech-service (aws-002497567426, ap-southeast-1)
- `10.201.x.x` → us-tech-service (aws-002497567426, ap-northeast-1)
- `10.140.x.x` → us-tech-service (aws-002497567426, ap-northeast-2)
- `10.200.x.x` → us-tech-service (aws-002497567426, sa-east-1)
- `10.202.x.x` → us-tech-service (aws-002497567426, eu-west-2)
- `10.180.x.x` → us-tech-service (aws-002497567426, ap-southeast-2)
- `10.210.x.x` → us-data (aws-769494896000)
- `10.220.x.x` → eu-prod (aws-740315635167)
- `10.222.x.x` → eu-tech-service (aws-010840394398)
- `10.223.x.x` → sg-devops (aws-125710977284)
- `10.234.x.x` → kr-dev (aws-343938550037)
- `10.224.x.x` → us-prod (old) (gcp-a4xcloud-p)
- `10.225.x.x` → us-tech-service (old) (gcp-a4xcloud-tech-service)
- `10.230.x.x` → us-prod (gcp-a4xcloud-p-us)
- `10.231.x.x` → us-tech-service (gcp-a4xcloud-tech-service-us)
- `10.0.x.x` / `172.20.x.x` → cn-main (tencent-100014919455)

> 注意：部分服务（如 kiss）虽然 job 名包含 "prod"，但实际 EC2 运行在 us-tech-service 账号的 VPC 中。不能只靠 job 名或 IP 段判断账户。旧 AWS CN 网段只作历史识别，不用于路由当前 CN 调查。

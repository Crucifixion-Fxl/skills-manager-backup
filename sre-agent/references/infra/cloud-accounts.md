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

### 中国 AWS

| 账户 ID | 环境 | Region | VPC CIDR | Profile |
|---------|------|--------|----------|---------|
| 741924744516 | cn-prod | cn-north-1 | 10.236.0.0/16 | aws-741924744516-cn-prod |
| 589899215075 | cn-tech-service | cn-north-1 | 10.228.0.0/16 | aws-589899215075-cn-tech-service |
| 801447536674 | cn-dev | cn-north-1 | 10.237.0.0/16 | aws-801447536674-cn-dev |

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

### 腾讯云

| 账户 ID | 环境 | Region | VPC CIDR | Profile |
|---------|------|--------|----------|---------|
| 100014919455 | cn-main | ap-beijing | 10.0.0.0/16 + 172.20.0.0/17 | tencent-100014919455-cn-main |

### Azure

| 订阅 ID | 环境 | 租户 | VPC CIDR | Profile |
|---------|------|------|----------|---------|
| 5b72041d-c45d-421b-8563-e857e4f5017e | prod | PHOTON SAIL TECHNOLOGIES PTE. LTD. (jjkj01.onmicrosoft.com) | - | `az account set -s 5b72041d-c45d-421b-8563-e857e4f5017e` |

### IP → 环境映射

通过 IP 段快速定位环境：
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
- `10.236.x.x` → cn-prod (aws-741924744516)
- `10.228.x.x` → cn-tech-service (aws-589899215075)
- `10.237.x.x` → cn-dev (aws-801447536674)
- `10.224.x.x` → us-prod (old) (gcp-a4xcloud-p)
- `10.225.x.x` → us-tech-service (old) (gcp-a4xcloud-tech-service)
- `10.230.x.x` → us-prod (gcp-a4xcloud-p-us)
- `10.231.x.x` → us-tech-service (gcp-a4xcloud-tech-service-us)
- `10.0.x.x` / `172.20.x.x` → cn-main (tencent-100014919455)

> 注意：部分服务（如 kiss）虽然 job 名包含 "prod"，但实际 EC2 运行在 us-tech-service 账号的 VPC 中。以 IP 段为准。

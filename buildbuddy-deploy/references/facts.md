# 当前部署事实表

> 来源：DEV/buildbuddy、DEV/crossplane-infra、DEV/argocd-apps 仓库实际配置

易变配置集中在此文件，其他 reference 文件使用参数化占位符。修改 BuildBuddy 仓库后需同步更新本文件。

## 集群与命名空间

| 项 | 当前值 | 来源 |
|----|--------|------|
| 集群 | sg-devops | DEV/buildbuddy/k8s/overlays/sg-devops/ |
| 命名空间 | buildbuddy | DEV/buildbuddy/k8s/overlays/sg-devops/kustomization.yaml |
| AWS 账户 | 125710977284 | DEV/crossplane-infra/aws-125710977284-sg-devops/ |
| Region | ap-southeast-1 | DEV/buildbuddy/k8s/base/deployment.yaml |

## 镜像

| 项 | 当前值 | 来源 |
|----|--------|------|
| 上游镜像 | gcr.io/flame-public/buildbuddy-app-onprem | DEV/buildbuddy/.gitlab-ci.yml |
| Harbor 路径 | harbor-12571-sg-devops.addx.live/cicd/sg-devops/buildbuddy | DEV/buildbuddy/k8s/overlays/sg-devops/kustomization.yaml |
| 当前版本 | v2.252.0 | DEV/buildbuddy/.gitlab-ci.yml + kustomization.yaml |

## CI Runner

| 项 | 当前值 | 来源 |
|----|--------|------|
| Runner tag | sg-amd64 | DEV/buildbuddy/.gitlab-ci.yml |

## 域名

| 域名 | 角色 | 来源 |
|------|------|------|
| buildbuddy-sg-ui.addx.live | UI 查看 | DEV/buildbuddy/k8s/base/ingress-ui.yaml |
| buildbuddy-sg-ci.addx.live | CI 写缓存 | DEV/buildbuddy/k8s/base/ingress-ci.yaml |
| buildbuddy-sg-dev.addx.live | 开发者只读 | DEV/buildbuddy/k8s/base/ingress-dev.yaml |

## Ingress

| Ingress 名称 | 安全组标签 | 协议 | 来源 |
|--------------|-----------|------|------|
| buildbuddy-ui | office | HTTP1 | k8s/base/ingress-ui.yaml |
| buildbuddy-ci | internal | GRPC | k8s/base/ingress-ci.yaml |
| buildbuddy-dev | office | GRPC | k8s/base/ingress-dev.yaml |

## WAF

| WAF 名称 | 用途 | 来源 |
|----------|------|------|
| buildbuddy-ci-auth-sg-devops | CI 入口 header gate | DEV/crossplane-infra/aws-125710977284-sg-devops/buildbuddy-waf-ci-auth.yaml |
| buildbuddy-readonly-sg-devops | Dev 入口只读拦截 | DEV/crossplane-infra/aws-125710977284-sg-devops/buildbuddy-waf-readonly.yaml |

## ServiceAccount / IRSA

| 项 | 当前值 | 来源 |
|----|--------|------|
| ServiceAccount | buildbuddy | k8s/overlays/sg-devops/serviceaccount.yaml |
| IRSA Role | crossplane-app-buildbuddy-irsa | DEV/crossplane-infra/aws-125710977284-sg-devops/buildbuddy-irsa.yaml |

---
name: buildbuddy-deploy
description: 部署和运维 BuildBuddy onprem（Bazel 远程缓存服务端）。覆盖 K8s 部署、Ingress 三域名模型、WAF 安全策略、Crossplane 基础设施、GitLab CI 镜像同步、ArgoCD GitOps 全流程。当用户需要部署 BuildBuddy、修改 BuildBuddy 配置、排查 BuildBuddy 部署问题、管理 Ingress/WAF/DNS 时使用。即使用户只说"部署远程缓存服务"、"BuildBuddy 服务有问题"也应触发。
---

# buildbuddy-deploy

协助部署和运维 BuildBuddy onprem（社区版）作为 Bazel 远程缓存服务端。基于 sg-devops 集群的实战经验。

> **适用范围：** BuildBuddy onprem（社区版）+ 本地磁盘缓存。企业版（S3 缓存、API Key 鉴权、read-only Key）不在本 skill 范围内。

## Description

### 三域名入口模型

按角色命名，不按网络形态命名：

| 角色 | 协议 | 安全组 | WAF | 后端端口 |
|------|------|--------|-----|----------|
| UI / 结果查看 | HTTP | office | 无 | 8080 |
| CI 写缓存 | gRPC | internal | header gate | 1985 |
| 开发者只读缓存 | gRPC | office | readonly | 1985 |

三个域名共用同一个 BuildBuddy Deployment，缓存共享。当前域名、Ingress 名称、WAF 名称等具体值见 [facts.md](references/facts.md)。

### 涉及的仓库

| 仓库 | 用途 |
|------|------|
| `DEV/buildbuddy` | K8s 配置 + CI |
| `DEV/crossplane-infra` | WAF 等 Crossplane 资源 |
| `DEV/argocd-apps` | ArgoCD Application 注册 |

## Rules

1. **Crossplane 资源不能放在应用仓库**。应用集群没有 Crossplane CRD，放在应用仓库会导致 ArgoCD SyncFailed。WAF 等资源必须放在 `crossplane-infra` 仓库。

2. **onprem 版不支持 `auth.enable_anonymous_usage` 和 `cache.s3`**。这些是企业版功能，配置中使用会导致启动崩溃。缓存只能用 `cache.disk`。

3. **CI 入口用 internal，Dev 入口用 office**。CI Runner 在 VPC 内网，开发者在办公网络。搞反了会导致连接超时。

4. **Ingress 安全组只能通过标签注入**。使用 `ingress.addx.io/sg: office` 或 `internal`。禁止手动写 `alb.ingress.kubernetes.io/security-groups`（违反 ING-06）。

5. **readonly WAF 不要拦截 FindMissingBlobs**。它是存在性检查（读操作），拦截会影响缓存查询。只拦截：ByteStream/Write、UpdateActionResult、BatchUpdateBlobs。

6. **ci-gate WAF 只做入口门禁**。仅检查 header 是否存在，不校验值。onprem 版没有应用层鉴权能力。

更多实现细节（健康检查参数、crane 镜像选择、镜像版本验证、缓存持久化等）见 [troubleshooting.md](references/troubleshooting.md)。

## 部署顺序

```
1. crossplane-infra: WAF 资源（MR 合入）
2. DEV/buildbuddy: 推送 main + 手动触发镜像同步
3. argocd-apps: 注册 Application（MR 合入）+ 运维注册仓库权限
4. 运维创建 DNS CNAME（从 Ingress status 获取 ALB 地址）
5. 验证：UI 可访问、CI 可写缓存、Dev 可读缓存
```

## 话题索引

| 需求 | 参考文档 | 关键点 |
|------|----------|--------|
| 当前部署具体值 | [facts.md](references/facts.md) | 版本、域名、Runner tag、WAF 名称等易变配置 |
| K8s 配置模板 | [k8s-templates.md](references/k8s-templates.md) | Deployment、Service、ConfigMap、Ingress |
| Crossplane WAF 模板 | [crossplane-templates.md](references/crossplane-templates.md) | readonly WAF + ci-gate WAF |
| GitLab CI 镜像同步 | [gitlab-ci-template.md](references/gitlab-ci-template.md) | crane:debug、docker config 拷贝 |
| ArgoCD Application | [argocd-template.md](references/argocd-template.md) | Image Updater、仓库权限 |
| 部署踩坑排查 | [troubleshooting.md](references/troubleshooting.md) | 9 个已知问题及解决方案 |

## Examples

### ❌ Bad

```yaml
# WAF 放在应用仓库 — ArgoCD 报 CRD not found
apiVersion: wafv2.aws.m.upbound.io/v1beta1
kind: WebACL
```

```yaml
# 手动写安全组 — 违反 ING-06
annotations:
  alb.ingress.kubernetes.io/security-groups: sg-xxx
```

### ✅ Good

```yaml
# WAF 放在 crossplane-infra 仓库
# crossplane-infra/aws-<account>-<cluster>/buildbuddy-waf-readonly.yaml
apiVersion: wafv2.aws.m.upbound.io/v1beta1
kind: WebACL
```

```yaml
# 用标签让 Kyverno 注入安全组
labels:
  ingress.addx.io/sg: office
```

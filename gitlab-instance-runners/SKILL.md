---
name: gitlab-instance-runners
description: Guide for using company-managed GitLab CI Instance Runners. Use when developers ask about Runner tags, Runner selection, Job pending/stuck issues, enabling instance runners for a project, or choosing the right Runner for CI jobs.
---

# GitLab CI Instance Runners 使用指南

## 描述

指导研发人员正确使用公司统一维护的 GitLab CI Instance Runners：选择合适的 Runner tag、排查 Job 调度问题、理解 Runner 资源规格。

**适用场景：**

- 为项目启用 Instance Runners
- 选择合适的 Runner tag 写入 `.gitlab-ci.yml`
- 排查 Job Pending / Stuck / Waiting for Pod 问题
- 查询 Runner 资源规格与可用性

## 规则

### 核心概念

**tags 是"选择 Runner 能力"的开关。**

匹配规则：`Job tags ⊆ Runner tags`（AND 关系）——Job 写了哪些 tags，Runner 必须**全部具备**才能接单。

> 不写 tags 的 Job 不会被调度，因为当前 Instance Runners 均未启用 "Run untagged jobs"。

### 启用步骤

#### 1. 启用 Instance Runners

- **角色要求**：Owner / Maintainer
- **路径**：Project → Settings → CI/CD → Runners
- 确认 **Enable instance runners for this project** 开关已打开

#### 2. 查看可用 Runner 与 tags

- **路径**：Project → Settings → CI/CD → Runners
- 确认 Runner 状态为 **Online**（绿色圆点）
- 记录需要的 **Tags**

#### 3. 写入 `.gitlab-ci.yml`

推荐只写 **1 个能力类 tag**，仅在明确需求时叠加区域/规格 tag：

```yaml
# 通用构建
build:
  tags: [runner]
  script:
    - echo "hello instance runners"

# Sonar 扫描（推荐）
sonar:
  tags: [sonar-scanner]
  script:
    - echo "run sonar scan"

# 新加坡区域（同 VPC，Clone 更快）
sonar_sg:
  tags: [sonar-scanner-sg]
  script:
    - echo "run sonar in sg"
```

#### 4. 验证

- 提交代码或手动 Run Pipeline
- 确认 Job **不是 Pending**
- Job 详情页显示的 Runner 与预期一致

### Instance Runners 清单

以 GitLab 页面（Project → Settings → CI/CD → Runners）显示为准；不要维护固定 Runner 编号表。K8s runner tags 的 GitOps 源头在：

```bash
rg --no-filename '^\s*tags:\s*"' ~/Project/A4x/k8s/clusters/*/cicd/gitlab-runner/values-override*.yaml \
  | sed -E 's/.*tags: "([^"]+)"/\1/' | tr ',' '\n' | sort -u
```

当前常见 tags 包括区域/架构 tags（`us-tech-amd64`、`eu-staging-amd64`、`cn-staging-arm64`、`sg-amd64`）、能力 tags（`runner`、`sonar-scanner`、`runner-sg`、`runner-sg-nat`、`runner-sg-toolchain-cache`）和 GPU tags（`us-tech-gpu-l4`、`us-tech-gpu-t4`）。

### Tag 选择速查

| 需求 | 推荐 Tag |
|------|----------|
| 常规构建/测试 | `runner` |
| Sonar 代码扫描 | `sonar-scanner` |
| 新加坡区域（快速 Clone） | `runner-sg`、`sg-amd64` 或 `sonar-scanner-sg` |
| 需要固定 NAT 出口的 SG Job | `runner-sg-nat` |
| 需要共享 toolchain cache | `runner-sg-toolchain-cache` |
| US tech GPU build | `us-tech-gpu-l4` 或 `us-tech-gpu-t4` |
| 指定 staging 构建环境 | `us-staging-amd64`、`eu-staging-amd64`、`cn-staging-amd64` |
| 杭州内网打包 | `hz-client` |
| 欧洲区域 / Vault | `eu-vault-runner` |

### SG Runner 资源分档（sg-devops 集群）

sg-devops 集群的 CI Runner 按资源需求分为 4 档。**所有项目默认使用 standard（原有 tag 不变）。**

| 档位 | CPU Req/Limit | Mem Req/Limit | Tag 规则：原 tag 加后缀 |
|------|---------------|---------------|------------------------|
| **standard** (默认) | 0.5c / 1.5c | 0.5Gi / 1.5Gi | `sg-amd64`、`sonar-scanner-sg`、`runner-sg`（不变） |
| **high** | 1.5c / 3.5c | 2Gi / 4Gi | 原 tag + `-high`，如 `sg-amd64-high`、`sonar-scanner-sg-high` |
| **xhigh** | 2.5c / 5c | 4Gi / 8Gi | 原 tag + `-xhigh`，如 `sg-amd64-xhigh`、`sonar-scanner-sg-xhigh` |
| **ultra** | 3.5c / 7.5c | 6Gi / 24Gi | 原 tag + `-ultra`，如 `sg-amd64-ultra`、`sonar-scanner-sg-ultra` |

**核心原则：默认用 standard，只有构建失败或资源不足时才升档。**

#### 什么时候升档

仅在以下情况出现时升档：

1. Job 被 OOMKilled（exit code 137，日志出现 `Killed`） → 内存不够，升一档
2. 构建时间异常长（同样代码以前 2 分钟现在 10 分钟） → CPU 被 throttle，升一档
3. 连续打包失败且排除代码问题后 → 可能是资源限制导致，升一档

**不要预判升档**——先用 standard 跑，失败了再升。

#### 升档方法

在 `.gitlab-ci.yml` 的 tag 后加对应后缀：

```yaml
# 默认 standard —— 所有新项目从这里开始
default:
  tags:
    - sonar-scanner-sg

# 升到 high（出现 OOM 或 throttle 后）
default:
  tags:
    - sonar-scanner-sg-high

# 升到 ultra（Android/大型 Gradle 项目）
default:
  tags:
    - sonar-scanner-sg-ultra
```

#### 自动判断已有项目该用哪个档

如果项目已运行一段时间，查询 Prometheus 获取实际资源使用峰值：

```bash
# 替换 <PROJECT_ID> 为 GitLab 项目 ID
# CPU 峰值
curl -sL 'https://victoria-metrics-sg-devops.addx.live/select/0/prometheus/api/v1/query' \
  --data-urlencode 'query=max(max_over_time(rate(container_cpu_usage_seconds_total{cluster="sg-devops",namespace="gitlab-runner",container="build",pod=~".*project-<PROJECT_ID>.*"}[10m])[7d:2h]))'

# Memory 峰值
curl -sL 'https://victoria-metrics-sg-devops.addx.live/select/0/prometheus/api/v1/query' \
  --data-urlencode 'query=max(max_over_time(container_memory_working_set_bytes{cluster="sg-devops",namespace="gitlab-runner",container="build",pod=~".*project-<PROJECT_ID>.*"}[7d:2h]))'
```

按峰值匹配：

| 条件 | 档位 |
|------|------|
| CPU ≤ 1.5c 且 Mem ≤ 1.5Gi | standard |
| CPU ≤ 3.5c 且 Mem ≤ 4Gi | high |
| CPU ≤ 5c 且 Mem ≤ 8Gi | xhigh |
| CPU > 5c 或 Mem > 8Gi | ultra |

#### 新项目（无历史数据）

**一律从 standard 开始。** 首次构建失败或连续失败后再按以下经验升档：

- Go / Node / Python 后端单模块 → 大概率 standard 就够
- Go monorepo 多模块 / Java Spring Boot → 可能需要 high
- Flutter 大型应用 / Bazel → 可能需要 xhigh
- Android Gradle / 大型 Java 多模块 → 可能需要 ultra

**原则：先跑再说，OOM 了再升。不要预设高档位浪费资源。**

### FAQ

#### Q1：不写 tags 会怎样？

Job 不会被调度执行。当前所有 Instance Runners 均未设置 "Run untagged jobs"。

#### Q2：Job 一直 Pending 怎么办？

排查步骤：

1. Settings → CI/CD → Runners → 确认 **Enable instance runners** 开关已打开
2. 确认有 Runner **Online** 且包含该 tag
3. 多个 tags 时，确认同一个 Runner 同时拥有所有 tags

#### Q3：Job 显示 Waiting for Pod？

K8s 集群正在动态扩缩容，等待即可。

#### Q4：需要新增 Runner / tags？

联系 GitLab 平台/DevOps 团队，提供：使用场景、资源需求、是否有区域/合规要求。

### 命名规范

Runner 描述应包含：
- **执行环境**：如 `kubernetes-runner`
- **区域/隔离**（如有）：如 `*-sg`、`*-eu`
- **资源规格**（如有）：如 `*-large`
- **特殊能力**（如有）：如 `vault`

Tags 设计原则：
- 单一职责：一个 tag 只表达一个维度（能力/区域/规格）
- 研发侧优先只写 1 个能力类 tag

## 示例

### ❌ Bad

#### 1. 不写 tags 导致 Job 永久 Pending

```yaml
build:
  script:
    - echo "hello"
```

**问题**：当前所有 Instance Runners 均未启用 "Run untagged jobs"，不写 tags 的 Job 不会被任何 Runner 接单，永远 Pending。

#### 2. 写多个不在同一 Runner 上的 tags

```yaml
build:
  tags: [runner, eu-vault-runner]
  script:
    - echo "build in eu"
```

**问题**：tags 是 AND 关系，Runner 必须同时具备 `runner` 和 `eu-vault-runner`，但没有任何 Runner 同时拥有这两个 tag，Job 会 stuck。

#### 3. Instance Runners 开关未打开

```
项目未启用 Instance Runners（Settings → CI/CD → Runners 开关关闭），
写了正确的 tags 但 Job 仍然 Pending，提示 "This job is stuck because you don't have any active runners"。
```

**问题**：需要 Owner/Maintainer 先在 Settings → CI/CD → Runners 中打开 **Enable instance runners for this project** 开关。

### ✅ Good

#### 1. 只写 1 个能力类 tag（推荐）

```yaml
sonar:
  tags: [sonar-scanner]
  script:
    - sonar-scanner
```

**优点**：单 tag 最简洁，调度器在北京和新加坡 Runner 中自动选择可用的，无需关心区域。

#### 2. 明确指定区域 tag（大仓库推荐）

```yaml
build:
  tags: [runner-sg]
  script:
    - make build
```

**优点**：新加坡 Runner 与 GitLab 在同一 VPC，Clone 大仓库更快。适合代码量大、Clone 耗时明显的项目。

#### 3. GPU 构建指定 GPU tag

```yaml
build_tensorrt:
  tags: [us-tech-gpu-l4]
  script:
    - make build-engine
```

**优点**：明确调度到 GPU CI runner。只有真实需要 GPU 的任务才写 GPU tag；普通大仓库构建优先用 `runner-sg` 或区域 amd64 tag。

## References

- [GitLab Runner 官方文档](https://docs.gitlab.com/ee/ci/runners/)
- [飞书原文](https://a4x-paas.feishu.cn/wiki/DEo3w5AnCinWerkJSNrct0r3noD)

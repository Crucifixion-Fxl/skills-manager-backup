---
name: gitlab-ci
description: Create and review .gitlab-ci.yml files based on GitLab CI best practices and company standards. Use when creating CI/CD pipelines for new projects, reviewing existing .gitlab-ci.yml, or optimizing pipeline configuration.
---

# GitLab CI

## 描述

创建和审查 `.gitlab-ci.yml` 文件。基于 GitLab CI 官方最佳实践、DRY/SSOT 设计模式和公司标准。

**适用场景：**

- 为新项目创建 `.gitlab-ci.yml`
- 审查已有的 `.gitlab-ci.yml`
- 优化 CI/CD 流水线性能与可维护性

**创建模式**：确认项目技术栈和部署方式 → 基于下方规则生成配置 → 自查是否符合所有规则。

**审查模式**：读取 `.gitlab-ci.yml` 及 `include:` 引用的文件 → 按规则逐项检查（标注 ID）→ 输出报告：

- 🔴 **必须修复**：违反强制规则
- 🟡 **建议改进**：不符合最佳实践
- 🟢 **可选优化**：进一步优化空间

## 公司标准

### Runner Tag 规范

| Tag | 用途 |
|-----|------|
| `sonar-scanner-sg` | 通用 CI 任务（新加坡区域） |

使用 `default.tags` 统一设置：

```yaml
default:
  tags:
    - sonar-scanner-sg
```

### Docker 镜像规范

通过顶层 `variables:` 统一管理镜像版本，禁止在 job 中硬编码：

```yaml
variables:
  PYTHON_IMAGE: python:3.11-slim
  NODE_IMAGE: node:20-slim
  GO_IMAGE: golang:1.22-alpine
```

### ArgoCD GitOps 集成

CI 只负责 Build & Test & Publish，部署通过 ArgoCD GitOps 触发：

- CI 构建镜像 → 推送 Registry → 更新 GitOps 仓库的 image tag
- ArgoCD 监听 GitOps 仓库变更 → 自动同步到集群
- 预览环境：MR 分支自动创建临时 namespace，合并后自动销毁

**红线**：禁止在 CI 中直接 `kubectl apply` / `helm install` 到生产环境。

## 规则

### DRY (Don't Repeat Yourself)

**D1: `default:` 统一公共配置**

`tags`、`image`、`interruptible`、`retry` 等跨 job 公共配置必须放在 `default:` 中，禁止每个 job 重复声明。

**D2: `extends:` 消除 job 重复**

多个 job 共享相同配置时，抽取隐藏 job（`.job-name`）作为基类，子 job 通过 `extends:` 继承。

**D3: YAML 锚点复用数据块**

纯数据（如 `rules:` 条件列表）的重复使用 YAML 锚点（`&anchor` / `*anchor`）。

**D4: `!reference` 选择性复用**

只需复用 job 的某个属性（而非整个 job）时，使用 `!reference [.job, attribute]`。

### SSOT (Single Source of Truth)

**S1: `include:` 引用共享模板**

公司级 CI 模板必须通过 `include:project:` 从 `engineering/ci-templates` 引用，禁止复制粘贴：

```yaml
include:
  - project: engineering/ci-templates
    ref: main
    file:
      - /.gitlab-ci/jobs/<shared-job>.yml  # 例如 sonar-scan.yml
```

**S2: `variables:` 集中管理可变值**

镜像版本、服务名称、区域等可变值必须定义为顶层 `variables:`，禁止在 `script:` 中硬编码。

**S3: `workflow:rules:` 统一流水线触发策略**

在文件顶部使用 `workflow:rules:` 定义何时创建流水线，避免每个 job 各自定义重复的触发条件。

### 结构与现代语法

| ID | 规则 | 严重性 |
|----|------|--------|
| R1 | 使用 `rules:` 代替 `only:/except:`（已废弃） | 🔴 必须 |
| R2 | 使用 `needs:` 构建 DAG 减少流水线耗时 | 🟡 建议 |
| R3 | 定义 `workflow:rules:` 避免重复流水线 | 🟡 建议 |
| R4 | `stages:` 顺序合理（lint → build → test → publish → deploy） | 🟡 建议 |
| R4b | **`stages:` 必须包含 `test`** —— 本实例大多数仓的 `ci_config_path` 指向 `engineering/ci-templates` 的 `entrypoint.yml`，它强制注入 `ci:init` 在 `test` stage。缺 `test` → MR pipeline **创建即 failed，0 job，`yaml_errors: null`，CI Lint 还显示 valid**，极难诊断 | 🔴 强制 |

> **注意：审查 `.gitlab-ci.yml` 时它可能不是真正的入口。** 先查 `ci_config_path`；指向 `engineering/ci-templates` 的仓，其 pipeline 里还有一批**删不掉的注入 job**（`.pre` 的凭据扫描与仓边界检查、`test` 的 `ci:init`、`.post` 的 AI code review）。
> 清单、触发条件与 `global:code-review` 的三档，以 `addx:api-synthetic-monitoring` 的 [`references/ci-templates-contract.md`](../api-synthetic-monitoring/references/ci-templates-contract.md) 为准——**不要在本 skill 里另写一份**。

### 可靠性

| ID | 规则 | 严重性 |
|----|------|--------|
| R5 | 所有 job 设置合理的 `timeout:` | 🟡 建议 |
| R6 | 网络依赖型 job 配置 `retry:` + `when:` 条件 | 🟡 建议 |
| R7 | 非部署 job 设置 `interruptible: true` | 🟡 建议 |
| R8 | 需要资源清理的 job 使用 `after_script:` | 🟢 可选 |

### 安全

| ID | 规则 | 严重性 |
|----|------|--------|
| R9 | 禁止在 yml 中硬编码密钥/凭证 | 🔴 必须 |
| R10 | 敏感变量使用 CI/CD Variables（masked + protected） | 🔴 必须 |
| R11 | 生产部署 job 限制 `protected` 分支/标签 | 🔴 必须 |

### 性能

| ID | 规则 | 严重性 |
|----|------|--------|
| R12 | `artifacts:` 设置 `expire_in:` 避免存储膨胀 | 🟡 建议 |
| R13 | `cache:` 配置正确的 `key:` 和 `policy:` | 🟡 建议 |
| R14 | 大型测试套件使用 `parallel:` 分片 | 🟢 可选 |
| R15 | 部署 job 使用 `resource_group:` 防止并发冲突 | 🟡 建议 |

## 示例

### ❌ Bad

#### 1. 违反 DRY — 大量重复配置

```yaml
build-frontend:
  stage: build
  image: node:20-slim
  tags:
    - sonar-scanner-sg
  script:
    - npm ci
    - npm run build
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

build-backend:
  stage: build
  image: golang:1.22-alpine
  tags:
    - sonar-scanner-sg
  script:
    - go build ./...
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

test-frontend:
  stage: test
  image: node:20-slim
  tags:
    - sonar-scanner-sg
  script:
    - npm test
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

**问题**：`tags` 重复 3 次（违反 D1），`rules` 重复 3 次（违反 D3），镜像硬编码（违反 S2）。

#### 2. 废弃语法 + 直接部署

```yaml
deploy:
  stage: deploy
  only:
    - main
  script:
    - kubectl apply -f k8s/ --kubeconfig /tmp/kubeconfig
  variables:
    IMAGE: registry.addx.ai/my-service:latest
```

**问题**：使用 `only:` 废弃语法（R1），直接 `kubectl apply` 到集群（ArgoCD 红线），`latest` tag 不可追溯（S2）。

#### 3. 复制粘贴模板

```yaml
# 从其他项目复制的 lint 配置
lint:
  stage: lint
  image: golangci/golangci-lint:v1.55
  script:
    - golangci-lint run
  tags:
    - sonar-scanner-sg
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  allow_failure: true
  # 30 行完全相同的配置在 5 个项目中重复...
```

**问题**：应抽取到 `engineering/ci-templates`（违反 S1），跨项目维护成本高，修改需改 5 处。

### ✅ Good

#### 1. DRY + SSOT 标准写法

```yaml
variables:
  NODE_IMAGE: node:20-slim
  GO_IMAGE: golang:1.22-alpine

default:
  tags:
    - sonar-scanner-sg
  interruptible: true
  retry:
    max: 2
    when:
      - runner_system_failure

workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

.mr-only:
  rules: &mr-rules
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

include:
  - project: engineering/ci-templates
    ref: main
    file:
      - /.gitlab-ci/jobs/<shared-job>.yml

stages:
  - build
  - test
  - publish

build-frontend:
  stage: build
  image: $NODE_IMAGE
  rules: *mr-rules
  script:
    - npm ci
    - npm run build
  artifacts:
    paths: [dist/]
    expire_in: 1 day

build-backend:
  stage: build
  image: $GO_IMAGE
  rules: *mr-rules
  script:
    - go build ./...

test-frontend:
  stage: test
  image: $NODE_IMAGE
  rules: *mr-rules
  needs: [build-frontend]
  script:
    - npm test
  coverage: '/coverage: \d+\.\d+%/'
```

**优点**：

- `default:` 统一 tags/retry/interruptible（D1）
- YAML 锚点 `&mr-rules` 复用触发条件（D3）
- `variables:` 管理镜像版本（S2）
- `include:` 引用公司模板（S1）
- `workflow:rules:` 统一触发策略（S3）
- `needs:` 加速 DAG（R2）
- `artifacts:expire_in:` 控制存储（R12）

#### 2. ArgoCD GitOps 集成

```yaml
publish:
  stage: publish
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

gitops-update:
  stage: deploy
  image: alpine/git:latest
  script:
    - git clone https://gitlab-ci-token:${GITOPS_TOKEN}@gitlab.addx.ai/engineering/gitops.git
    - cd gitops/apps/my-service
    - "sed -i 's|image:.*|image: ${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHA}|' values.yaml"
    - git commit -am "chore: update my-service to ${CI_COMMIT_SHA}"
    - git push
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  resource_group: production
```

**优点**：CI 只构建推送镜像，通过更新 GitOps 仓库触发 ArgoCD 部署（职责分离），`$CI_COMMIT_SHA` 确保镜像可追溯，`resource_group:` 防止并发部署（R15）。

## 豁免

| 场景 | 条件 |
|------|------|
| 极简项目 | 只有 lint/test 无部署需求，可省略 ArgoCD 集成 |
| 遗留项目迁移 | `only:/except:` 迁移期间可暂时保留，需制定迁移计划 |
| 一次性脚本 | 临时 CI job 可简化，需标注 `# TODO: cleanup` |

## References

- [GitLab CI/CD YAML syntax reference](https://docs.gitlab.com/ee/ci/yaml/)
- [GitLab CI/CD pipeline efficiency](https://docs.gitlab.com/ee/ci/pipelines/pipeline_efficiency.html)
- [GitLab CI/CD `rules:` keyword](https://docs.gitlab.com/ee/ci/yaml/#rules)
- [GitLab CI/CD `needs:` keyword](https://docs.gitlab.com/ee/ci/yaml/#needs)
- [GitLab CI/CD `include:` keyword](https://docs.gitlab.com/ee/ci/yaml/#include)

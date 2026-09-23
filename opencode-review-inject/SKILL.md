---
name: opencode-review-inject
description: Use when injecting AI-powered code review into a GitLab CI pipeline using opencode. Covers Docker image setup, skill mounting, LLM provider configuration, runner selection, and artifact collection for MR-triggered review jobs.
---

# OpenCode Review Inject

Inject AI-powered code review into any GitLab CI pipeline using the shared opencode audit template. The template handles skill loading, LLM provider configuration, changed-directory detection, and artifact collection — consumer projects only need a few lines of YAML.

## 描述

将 opencode AI 代码审查注入 GitLab CI 流水线。通过 `include` + `extends` 复用共享模板，消费方只需声明使用哪个 skill、扫描什么文件、以及审计 prompt。

适用场景：

- 为项目添加 MR 触发的 AI 代码审查
- 复用 `engineering/skills` 仓库中的 review/audit skill
- 需要将审计报告保存为 CI artifact

## 执行要点

### 前置条件

1. **Docker 镜像**：`registry-harbor-sg.addx.live/cicd/opencode:latest`（基于 `node:22-slim`，含 git + opencode-ai）
2. **Skills 仓库**：`engineering/skills`（通过 `CI_JOB_TOKEN` 跨项目 clone）
3. **LLM API**：Admin Area 实例级变量 `LLM_API_KEY` + `LLM_API_URL` 已预配置，所有项目自动可用，无需额外设置
4. **Runner**：需同时访问 Harbor 镜像仓库和 GitLab（tag: `sonar-scanner-sg`）

### 使用共享模板

消费方 `.gitlab-ci.yml` 只需三部分——引用模板、声明变量、设触发条件。`SKILL_NAME` / `SCAN_PATTERN` / `AUDIT_PROMPT` 决定加载哪个 skill、扫描什么文件：

```yaml
# 1. 引用模板
include:
  - project: 'engineering/skills'
    ref: main
    file: '/templates/opencode-audit.gitlab-ci.yml'

# 2. 定义 job，extends 模板
my-review:
  extends: .opencode-audit
  variables:
    SKILL_NAME: "<skill-name>"          # skills/ 下的目录名
    SCAN_PATTERN: "<ext>"               # 文件后缀（如 tf、py、yaml）
    LLM_MODEL: "<model>"                # 模型名称（如 gpt-5-mini、gpt-5.4）
    AUDIT_PROMPT: "Use the <skill-name> skill to audit all .<ext> files in the current directory. Generate the full audit report and save it as audit-report.md."

  # 3. 触发条件
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes:
        - "**/*.<ext>"
```

### 可配置变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SKILL_NAME` | `terraform-audit` | 加载的 skill 名称（对应 `skills/` 下的目录名，如 `terraform-audit`、`security-compliance-review`、`code-review`） |
| `SCAN_PATTERN` | `*.tf` | 文件后缀，用于 `git diff` 检测变更目录（如 `tf`、`py`、`yaml`、`go`） |
| `LLM_MODEL` | `""` | 模型名称，当前最高支持 `gpt-5.4`（如 `gpt-5-mini`、`gpt-5.4`） |
| `AUDIT_PROMPT` | 见模板 | 传给 `opencode run` 的完整 prompt |
| `SKILLS_REF` | `main` | skills 仓库分支（开发调试时可改为 feature 分支） |

### LLM 配置

模板使用 Admin Area 预配置的实例级变量：

- **`LLM_API_KEY`**：OpenAI 兼容 API 密钥（`Authorization: Bearer` 认证）
- **`LLM_API_URL`**：OpenAI 兼容 base URL（如 `https://xxx/v1`）

模板自动将 `LLM_API_KEY` 设为 `OPENAI_API_KEY`，并在检测到 `LLM_API_URL` 时生成 `opencode.json` 配置（含 `baseURL` 和 `useCompletionUrls: true`）。

消费方无需关心认证细节，只需指定 `LLM_MODEL` 选择模型即可。

### 变更目录检测

模板通过 `git diff` 检测 MR 中变更的目录，逐目录执行审计：

```bash
CHANGED_DIRS=$(git diff --name-only "$CI_MERGE_REQUEST_DIFF_BASE_SHA"..."$CI_COMMIT_SHA" \
  | grep "\.$SCAN_PATTERN$" \
  | xargs -I{} dirname {} \
  | sort -u)
```

每个变更目录独立执行一次 `opencode run`，报告保存在对应目录下。

### Artifact 收集

模板自动收集所有 `audit-report.md` 作为 CI artifact（30 天过期）：

```yaml
artifacts:
  paths:
    - "**/audit-report.md"
  when: always
  expire_in: 30 days
```

### 认证机制

- **Skills 仓库 clone**：使用 `CI_JOB_TOKEN`，无需额外配置（需确保目标项目允许跨项目 job token 访问）
- **Harbor 镜像拉取**：Runner 需有 Harbor 访问权限
- **LLM API**：Admin Area 实例级变量自动注入，无需项目级配置

## 示例

### ❌ Bad

```yaml
# 硬编码 skill 路径，手动 clone，runner 选错
terraform-review:
  stage: test
  image: registry-harbor-sg.addx.live/cicd/opencode:latest
  tags:
    - docker  # 没有 Harbor 访问权限的 runner
  script:
    - git clone https://<your-gitlab>/engineering/skills.git /tmp/skills  # 无认证，会失败
    - cp -r /tmp/skills/terraform-audit /root/.opencode/skills/  # 路径错误：缺少 skills/ 前缀
    - opencode run "audit the terraform files"  # 没有设置 LLM API key
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'  # 应该在 MR 触发，不是 main push
```

**问题**：
- 没使用 `CI_JOB_TOKEN` 认证，clone 会 403
- Skill 路径错误：应该是 `skills/terraform-audit`（`skills/` 子目录）
- 没有设置 `OPENAI_API_KEY` 环境变量
- Runner tag 无 Harbor 访问权限
- 在 main push 触发，应该是 MR 事件

### ✅ Good — Terraform 审计

```yaml
include:
  - project: 'engineering/skills'
    ref: main
    file: '/templates/opencode-audit.gitlab-ci.yml'

terraform-audit:
  extends: .opencode-audit
  variables:
    SKILL_NAME: "terraform-audit"
    SCAN_PATTERN: "tf"
    LLM_MODEL: "gpt-5-mini"
    AUDIT_PROMPT: >-
      Use the terraform-audit skill to audit all .tf files
      in the current directory. Generate the full audit report
      and save it as audit-report.md.
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes:
        - "**/*.tf"
```

### ✅ Good — Python 代码审查

```yaml
include:
  - project: 'engineering/skills'
    ref: main
    file: '/templates/opencode-audit.gitlab-ci.yml'

python-review:
  extends: .opencode-audit
  variables:
    SKILL_NAME: "code-review"
    SCAN_PATTERN: "py"
    LLM_MODEL: "gpt-5.4"
    AUDIT_PROMPT: >-
      Use the code-review skill to review all .py files
      in the current directory. Generate the full audit report
      and save it as audit-report.md.
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes:
        - "**/*.py"
```

**优点**：
- 复用共享模板，无需重复 clone/认证/检测逻辑
- `extends: .opencode-audit` 继承完整的 before_script、script、artifacts 配置
- 只需替换 `SKILL_NAME`、`SCAN_PATTERN`、`LLM_MODEL`、`AUDIT_PROMPT` 即可适配任意语言/skill
- LLM 密钥从 Admin Area 自动获取，零项目级配置
- MR 事件 + 文件变更双重条件，避免无关 pipeline

## Common Pitfalls

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 镜像拉取失败 | Runner 无 Harbor 访问权限 | 使用 `sonar-scanner-sg` tag 的 runner |
| `exec format error` | 镜像架构不匹配（arm64 vs amd64） | 确保 Docker 镜像构建为 `linux/amd64`（CI runner 通常是 x86_64） |
| opencode 启动报 `node: not found` | 基础镜像缺少 Node.js | 使用 `node:22-slim` 作为 Dockerfile 基础镜像（opencode 是 npm 包） |
| Skills clone 403 | `CI_JOB_TOKEN` 无跨项目权限 | 在 skills 仓库 Settings > CI/CD > Token Access 中添加消费方项目 |
| `SKILL.md not found` | Skill 路径不含 `skills/` 前缀 | 正确路径：`/tmp/skills/skills/$SKILL_NAME`（仓库根目录下有 `skills/` 子目录） |
| `Access denied` / 认证失败 | `LLM_API_KEY` 设为 Protected，MR 源分支不是 protected branch | CI Variable **不勾选 Protected**（masked 即可） |
| opencode `Resource not found` (404) | opencode 默认使用 Responses API，部分端点不支持 | 确保模板配置了 `useCompletionUrls: true`（强制 Chat Completions API） |
| `fatal: Invalid symmetric difference expression` | GitLab 默认 shallow clone（depth 20）无法计算 MR diff | 模板已包含 `git fetch --unshallow \|\| true` |
| 审计报告为空 | `SCAN_PATTERN` 不匹配变更文件 | 检查 `SCAN_PATTERN` 值（不含 `*` 和 `.`，只写后缀如 `tf`） |
| Job 超时 | LLM 响应慢或文件过多 | 增加 `timeout` 或限制 `AUDIT_PROMPT` scope |

## References

- 共享 CI 模板：`engineering/skills` 仓库 `templates/opencode-audit.gitlab-ci.yml`
- Docker 镜像源码：`terraform-skills/docker/Dockerfile`
- 消费方示例：`engineering/terraform-infra/.gitlab-ci.yml`

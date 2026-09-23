---
name: embed-ci-setup
description: 为嵌入式 C/C++ 仓库快速创建或补充 GitLab CI Pipeline 与 SonarQube 增量扫描。当用户说"给这个仓库加 Pipeline"、"创建 CI 配置"、"接入 SonarQube"、"嵌入式项目需要 Pipeline"，或仓库没有 .gitlab-ci.yml 无法合入 MR 时触发。
---

# embed-ci-setup

为嵌入式仓库创建基础 GitLab CI Pipeline，并接入 MR 触发的 SonarQube 增量扫描。

## Description

嵌入式团队规则：没有 Pipeline 的仓库不允许合入 MR。本 Skill 帮助嵌入式 C/C++ 仓库创建最小可用 CI，包含：

- MR 触发的 SonarQube 增量扫描，仅扫描本次变更的 C/C++ 源码和头文件
- Quality Gate 检查，失败时 `allow_failure: true`，提示风险但不阻断 MR
- 动态生成 `sonar-project.properties`
- 保持仓库既有 runner、image、变量和 job 配置

适用场景：

- 仓库没有 `.gitlab-ci.yml`，需要快速创建基础 Pipeline
- 仓库已有 `.gitlab-ci.yml`，需要补充 SonarQube 增量扫描
- 以 C/C++ 为主的嵌入式项目

不适用：

- 非 C/C++ 项目的通用 SonarQube 模板
- 编译、打包、发布、Nexus 上传等制品化流程

## Rules

### Rule 1 — 基础约束

| 约束 | 说明 |
| --- | --- |
| CI 入口 | 由 `engineering/ci-templates` 的 `entrypoint.yml` 统一管理，会自动 include 业务仓库的 `.gitlab-ci.yml` |
| 禁止自定义 stages | 不要在 `.gitlab-ci.yml` 中声明 `stages:`，使用 GitLab 默认 stages |
| include 方式 | 必须使用 `include: project`，不要使用 `include: local` |
| Sonar token | 使用 GitLab CI/CD Variable `SONAR_AUTH_TOKEN` |
| Sonar host | 使用 GitLab CI/CD Variable `SONAR_HOST_URL` |
| runner/image | 不强制固定；优先继承仓库默认 CI 环境 |
| sonar-scanner | 最终执行 `sonar-analysis` 的环境必须包含 `sonar-scanner` |
| YAML heredoc | 在 `script: |` block 中不要使用 `<<EOF ... EOF`，用 `echo` 逐行写入 |

推荐兜底值，仅在用户确认或仓库已有模式需要时使用：

```yaml
image: registry-harbor-sg.addx.live/firmware/embed-quality:v1.2.0
tags:
  - sonar-scanner-sg
```

不要把上述 image/tag 当作强制模板。新仓库没有默认 CI 环境时，不要自动注入 `default.image` 或 `default.tags`；完成回复中必须提醒项目 owner 确认 runner/image 是否包含 `sonar-scanner`，否则 MR Sonar job 可能失败。

### Rule 2 — 检查仓库状态

先检查：

```bash
ls .gitlab-ci.yml 2>/dev/null
ls ci/sonar-analysis-embed.yml 2>/dev/null
grep "sonar-project.properties" .gitignore 2>/dev/null
```

按状态处理：

| `.gitlab-ci.yml` | `ci/sonar-analysis-embed.yml` | 操作 |
| --- | --- | --- |
| 不存在 | 不存在 | 创建 `.gitlab-ci.yml` 和 `ci/sonar-analysis-embed.yml` |
| 已存在 | 不存在 | 创建 `ci/sonar-analysis-embed.yml`，并在 `.gitlab-ci.yml` 中追加 include |
| 已存在 | 已存在 | 检查变量、host、token、rules 是否符合本 Skill；只修正缺失或错误项 |

如果 `.gitignore` 没有 `/sonar-project.properties`，追加该条目。

### Rule 3 — 创建 `.gitlab-ci.yml`

仓库没有 `.gitlab-ci.yml` 时，生成最小 baseline：

```yaml
include:
  - project: $CI_PROJECT_PATH
    ref: $CI_COMMIT_REF_NAME
    file: ci/sonar-analysis-embed.yml

pass:
  stage: test
  script:
    - echo "CI pass"
```

关键点：

- 不声明 `stages:`
- 不强制写 `default.image` 或 `default.tags`
- `pass` job 使用默认 `test` stage，保证非 MR 场景也有 job
- 如果用户明确提供 runner/image，或仓库模板已有默认环境，可以按仓库模式补充

### Rule 4 — 创建 `ci/sonar-analysis-embed.yml`

生成内容：

```yaml
# ============================================================================
# SonarQube Incremental Analysis Job
# Triggered on Merge Request events, scans only changed C/C++ files
#
# Requires: SONAR_AUTH_TOKEN and SONAR_HOST_URL configured in GitLab CI/CD Variables
# ============================================================================

sonar-analysis:
  stage: test
  variables:
    GIT_DEPTH: "0"
    GIT_STRATEGY: fetch
  before_script:
    - git config --global --add safe.directory "${CI_PROJECT_DIR}"
  script:
    - |
      echo "======================================"
      echo "SonarQube Incremental Analysis"
      echo "  Source: ${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}"
      echo "  Target: ${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
      echo "======================================"

      if [ -z "${SONAR_AUTH_TOKEN}" ]; then
        echo "SONAR_AUTH_TOKEN is required." >&2
        exit 1
      fi

      if [ -z "${SONAR_HOST_URL}" ]; then
        echo "SONAR_HOST_URL is required." >&2
        exit 1
      fi

      TARGET_BRANCH="origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
      SOURCE_BRANCH="origin/${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}"
      CHANGED_FILES=$(git diff --name-only --diff-filter=d "${TARGET_BRANCH}...${SOURCE_BRANCH}" -- '*.c' '*.h' '*.cc' '*.cpp' '*.cxx' '*.hh' '*.hpp' '*.hxx' | paste -sd ',' -)

      if [ -z "${CHANGED_FILES}" ]; then
        echo "No C/C++ files changed in this MR, skipping SonarQube analysis."
        exit 0
      fi

      echo "Changed C/C++ files:"
      echo "${CHANGED_FILES}" | tr ',' '\n' | sed 's/^/  - /'

      PROJECT_KEY=$(echo "${CI_PROJECT_PATH}" | tr '/' '-')
      echo "Project Key: ${PROJECT_KEY}"

      PROPS="${CI_PROJECT_DIR}/sonar-project.properties"
      {
        echo "sonar.projectKey=${PROJECT_KEY}"
        echo "sonar.projectName=${PROJECT_KEY}"
        echo "sonar.projectVersion=1.0"
        echo "sonar.sources=."
        echo "sonar.sourceEncoding=UTF-8"
        echo "sonar.language=c"
        echo "sonar.inclusions=${CHANGED_FILES}"
        echo "sonar.scm.provider=git"
        echo "sonar.host.url=${SONAR_HOST_URL}"
      } > "${PROPS}"

      echo "======================================"
      echo "Generated sonar-project.properties:"
      cat "${CI_PROJECT_DIR}/sonar-project.properties"
      echo "======================================"

      sonar-scanner -Dsonar.token="${SONAR_AUTH_TOKEN}" -Dsonar.qualitygate.wait=true
  allow_failure: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - when: never
```

如果仓库默认环境没有 `sonar-scanner`，不要擅自猜测 runner/image。先让用户确认；确认后只给 `sonar-analysis` job 增加对应 `image` / `tags`，不要污染全局默认配置。

### Rule 5 — 更新已有 `.gitlab-ci.yml`

已有 `.gitlab-ci.yml` 时，只做最小 patch：

- 如果已有 `include:`，追加：

```yaml
  - project: $CI_PROJECT_PATH
    ref: $CI_COMMIT_REF_NAME
    file: ci/sonar-analysis-embed.yml
```

- 如果没有 `include:`，在文件顶部添加：

```yaml
include:
  - project: $CI_PROJECT_PATH
    ref: $CI_COMMIT_REF_NAME
    file: ci/sonar-analysis-embed.yml
```

不要覆盖已有 `stages`、`default`、`variables`、`workflow` 或业务 job。优先用人工 patch / 最小文本编辑，避免 YAML serializer 全量重写导致注释、锚点、格式和顺序丢失。

### Rule 6 — 完成提示

完成后告诉用户：

```text
文件已生成或更新：
  - .gitlab-ci.yml
  - ci/sonar-analysis-embed.yml
  - .gitignore（如已补充 /sonar-project.properties）

使用前请确认：
  1. GitLab 项目 Settings -> CI/CD -> Variables 已配置 SONAR_AUTH_TOKEN
  2. GitLab 项目 Settings -> CI/CD -> Variables 已配置 SONAR_HOST_URL
  3. runner/image 中存在 sonar-scanner；如果没有确认默认 CI 环境，项目 owner 必须确认这一点，否则 MR Sonar job 可能失败
```

## Examples

### Bad

```yaml
stages:
  - build

include:
  - local: ci/sonar-analysis-embed.yml

sonar-analysis:
  stage: test
  tags:
    - sonar-scanner-sg
  image: registry-harbor-sg.addx.live/firmware/embed-quality:v1.2.0
  script:
    - |
      cat > sonar-project.properties <<EOF
      sonar.host.url=https://example.invalid
      EOF
```

问题：

- 自定义 `stages:` 缺少 `test`，可能导致 pipeline 创建失败
- `include: local` 在外部 CI 入口下解析位置错误
- 固定 runner/image，不尊重仓库默认环境
- 固定 Sonar host
- 使用 heredoc

### Good

```yaml
include:
  - project: $CI_PROJECT_PATH
    ref: $CI_COMMIT_REF_NAME
    file: ci/sonar-analysis-embed.yml

pass:
  stage: test
  script:
    - echo "CI pass"
```

```yaml
sonar-analysis:
  stage: test
  variables:
    GIT_DEPTH: "0"
    GIT_STRATEGY: fetch
  script:
    - |
      if [ -z "${SONAR_AUTH_TOKEN}" ]; then
        echo "SONAR_AUTH_TOKEN is required." >&2
        exit 1
      fi
      if [ -z "${SONAR_HOST_URL}" ]; then
        echo "SONAR_HOST_URL is required." >&2
        exit 1
      fi
      echo "sonar.host.url=${SONAR_HOST_URL}" > sonar-project.properties
      sonar-scanner -Dsonar.token="${SONAR_AUTH_TOKEN}" -Dsonar.qualitygate.wait=true
  allow_failure: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - when: never
```

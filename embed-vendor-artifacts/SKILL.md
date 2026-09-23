---
name: embed-vendor-artifacts
description: Use when a user asks to initialize vendor SDK artifactization, add vendor SDK artifact CI, add auto_pack.sh, publish vendor SDK artifacts to Nexus, add GitLab artifacts packaging for a vendor SDK, or prepare vendor artifact handoff files.
---

# embed-vendor-artifacts

为 vendor SDK 仓库初始化制品化 CI 契约。这个 Skill 只负责 CI 流程、占位 mock、产物出口和发布脚本，不负责真实 vendor SDK 制品制作。

## Description

本 Skill 用于把 vendor SDK 仓库接入标准制品化流程：

- GitLab Web 手动流水线生成临时 artifacts
- Web tag 流水线发布制品到 Nexus
- 根目录 `auto_pack.sh` 作为后续真实制品制作的唯一替换入口
- `ci/package_mock/` 提供最小占位文件，验证打包链路
- 对完全没有 CI job 的仓库，先用 `embed-ci-setup` 初始化基础 CI

适用场景：

- vendor SDK 仓库还没有制品化能力
- 需要初始化 `auto_pack.sh`
- 需要添加 `package_artifacts` / `publish_to_nexus` GitLab jobs
- 需要准备 vendor artifact handoff 占位文件
- 需要把 release tag 产物发布到 Nexus

不适用：

- 实现真实 vendor SDK 编译、签名、打包逻辑
- 修改 SDK 原有 build、kernel、uboot、driver、media、resource 等业务构建脚本
- 为非 vendor SDK 仓库添加通用 CI

## Rules

### Rule 1 — 先判断 CI 基线

先检查：

```bash
ls .gitlab-ci.yml 2>/dev/null
ls ci/sonar-analysis-embed.yml 2>/dev/null
```

"完全没有 CI job" 定义为：

- `.gitlab-ci.yml` 不存在
- 或 `.gitlab-ci.yml` 存在，但没有任何有效 job。`include`、`workflow`、`default`、`variables`、`stages` 和注释不算 job
- 如果 `.gitlab-ci.yml` 只有 `include`，且 include 指向外部或共享 CI 模板，先检查或询问该 include 是否已经提供有效 job；不能直接判定为完全没有 CI job

处理规则：

| 仓库状态 | 操作 |
| --- | --- |
| 没有 `.gitlab-ci.yml` | 先使用 `embed-ci-setup` 初始化基础 CI，再添加制品化 jobs |
| 有 `.gitlab-ci.yml` 但没有有效 job | 视为无 CI job：先补基础 CI，再添加制品化 jobs；保留已有顶层配置 |
| 有 `.gitlab-ci.yml` 且已有 job，但没有 `ci/sonar-analysis-embed.yml` | 保留现有 CI。只有用户明确要求基础 MR CI，或仓库政策要求时，才按 `embed-ci-setup` 补 Sonar include |
| 有 CI job 但没有 artifact jobs | 保留现有 CI，追加 artifact jobs |
| 已有 artifact jobs | 不重复添加；只审查并补齐缺失项 |

永远不要整文件覆盖 `.gitlab-ci.yml`。

### Rule 2 — runner/image 策略

不要强制写入 runner 或 image。

默认策略：

- 保留仓库已有 `default.image`、`default.tags` 和 job 级 `image` / `tags`
- 只有仓库已有相同模式，或用户明确提供 runner/image 时，才写 job 级或 default 级配置
- 推荐值只作为提示，不作为强制生成内容

推荐值：

```yaml
default:
  image: registry-harbor-sg.addx.live/firmware/embed-addx-things:v1.0.0
  tags:
    - runner-sg-toolchain-cache
```

如果没有可确认的默认 CI 环境，最终回复必须提醒项目 owner 确认 packaging job 的 runner/image 是否能运行 `bash`、`tar`、`curl` 等基础工具。

### Rule 3 — 创建 mock 占位目录

创建：

```text
ci/package_mock/auto_build.sh
ci/package_mock/prepare.py
ci/package_mock/toolchain_tools.json
ci/package_mock/vendor_artifact.json
```

要求：

- 文件内容可以为空
- 这些文件只保证占位和进入 tarball
- 空的 `toolchain_tools.json` 和 `vendor_artifact.json` 不是可消费元数据
- 不要在测试里解析空 JSON
- 后续真实 artifact owner 负责填充这些文件

### Rule 4 — 创建 `auto_pack.sh`

根目录创建 `auto_pack.sh`，作为唯一可替换打包入口。

推荐契约：

- 默认输出目录：`${AUTO_PACK_DIR:-output/dist}`
- 默认产物名：`${VENDOR_NAME}-${id}.tar.gz`，具体 vendor 名按仓库命名
- `VENDOR_NAME` 未设置时示例脚本用仓库目录名兜底；如果需要稳定产品名，例如 `T32Z`、`T33ZN`，应显式设置 `VENDOR_NAME` 或把脚本改成项目约定名
- 语义化 tag，例如 `1.2.3`，可作为 `id`
- 非语义化 tag 默认使用 SHA8，除非用户明确要求把 tag 名放进文件名
- 当前实现只打包 `ci/package_mock/`
- 后续真实制品制作只替换内部打包逻辑，保留输出目录和文件名契约

示例：

```bash
#!/usr/bin/env bash
set -euo pipefail

resolve_sha8() {
  if [ -n "${CI_COMMIT_SHA:-}" ]; then
    printf '%s\n' "${CI_COMMIT_SHA:0:8}"
    return
  fi
  git rev-parse --short=8 HEAD
}

resolve_tag() {
  if [ -n "${CI_COMMIT_TAG:-}" ]; then
    printf '%s\n' "${CI_COMMIT_TAG}"
    return
  fi
  git describe --tags --exact-match 2>/dev/null || true
}

vendor_name="${VENDOR_NAME:-$(basename "$(pwd)")}"
sha8="$(resolve_sha8)"
tag="$(resolve_tag)"

if [[ "${tag}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  pack_id="${tag}"
else
  pack_id="${sha8}"
fi

auto_pack_dir="${AUTO_PACK_DIR:-output/dist}"
auto_pack_name="${AUTO_PACK_NAME:-${vendor_name}-${pack_id}.tar.gz}"
auto_pack_path="${auto_pack_dir}/${auto_pack_name}"
package_mock_dir="ci/package_mock"

mkdir -p "${auto_pack_dir}"

if [ ! -d "${package_mock_dir}" ]; then
  echo "Package mock directory is missing: ${package_mock_dir}" >&2
  exit 1
fi

tar -czf "${auto_pack_path}" -C "${package_mock_dir}" .
echo "Generated package: ${auto_pack_path}"
```

### Rule 5 — 创建 Nexus 发布脚本

创建 `ci/publish-to-nexus.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${AUTO_PACK_DIR:-output/dist}"

required_vars="NEXUS_URL NEXUS_USER NEXUS_PASSWD"
for var in ${required_vars}; do
  if [ -z "${!var:-}" ]; then
    echo "${var} is required for Nexus publishing." >&2
    exit 1
  fi
done

if [ -z "${CI_COMMIT_TAG:-}" ]; then
  echo "Not a tag pipeline; skip Nexus publishing."
  exit 0
fi

found=0
for file in "${artifact_dir}"/*; do
  if [ ! -f "${file}" ]; then
    continue
  fi

  found=1
  target_url="${NEXUS_URL%/}/$(basename "${file}")"
  echo "Uploading $(basename "${file}") to ${target_url}"
  curl --fail --show-error --location \
    --user "${NEXUS_USER}:${NEXUS_PASSWD}" \
    --upload-file "${file}" \
    "${target_url}"
done

if [ "${found}" -eq 0 ]; then
  echo "No files found in ${artifact_dir} for Nexus publishing." >&2
  exit 1
fi
```

不要打印 `NEXUS_USER` / `NEXUS_PASSWD`。

### Rule 6 — 合并 `.gitlab-ci.yml`

优先人工 patch / 最小文本编辑，不要用 YAML serializer 全量重写 `.gitlab-ci.yml`。全量重写可能丢失注释、锚点、格式和顺序。

变量合并规则：

- 已有顶层 `variables:` 时，只在缺少 `AUTO_PACK_DIR` 时追加 `AUTO_PACK_DIR: output/dist`
- 已有 `AUTO_PACK_DIR` 时保留仓库原值
- 没有 `variables:` 时才创建
- 不覆盖无关变量

追加 job 参考：

```yaml
variables:
  AUTO_PACK_DIR: output/dist

package_artifacts:
  stage: build
  rules:
    - if: '$CI_PIPELINE_SOURCE == "web"'
    - when: never
  script:
    - bash auto_pack.sh
  artifacts:
    name: "${CI_PROJECT_NAME}-${CI_COMMIT_REF_SLUG}-${CI_COMMIT_SHORT_SHA}"
    paths:
      - "${AUTO_PACK_DIR}/"
    expire_in: 7 days

publish_to_nexus:
  stage: deploy
  needs:
    - job: package_artifacts
      artifacts: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_TAG'
      when: on_success
    - when: never
  script:
    - bash ci/publish-to-nexus.sh
```

如果 `package_artifacts` 或 `publish_to_nexus` 已存在，不要重复添加。先审查现有 job，再只提出或补齐缺失字段。

不要声明自定义 `stages:`；使用 GitLab 默认 stages。

### Rule 7 — 本地验证

至少验证：

```bash
bash -n auto_pack.sh ci/publish-to-nexus.sh
bash auto_pack.sh
tar -tzf output/dist/*.tar.gz | sort
```

检查 tarball 包含：

```text
auto_build.sh
prepare.py
toolchain_tools.json
vendor_artifact.json
```

如果添加了本地测试脚本，也要运行它们。

### Rule 8 — 完成提示

完成后告诉用户：

```text
已初始化 vendor SDK 制品化 CI 契约。

请在 GitLab Settings -> CI/CD -> Variables 配置：
  - NEXUS_URL
  - NEXUS_USER
  - NEXUS_PASSWD

如果本次也初始化了基础 CI，还需要：
  - SONAR_AUTH_TOKEN
  - SONAR_HOST_URL

注意：
  - 当前 ci/package_mock/ 下的文件只是空占位，不是可消费元数据。
  - auto_pack.sh 当前只验证链路，真实制品制作需要后续替换内部逻辑。
  - 如未确认 runner/image，请项目 owner 确认 CI 环境可运行打包和发布所需工具。
```

## Examples

### Bad

```yaml
default:
  image: registry-harbor-sg.addx.live/firmware/embed-addx-things:v1.0.0
  tags:
    - runner-sg-toolchain-cache

stages:
  - build
  - deploy
```

问题：

- 未确认用户或仓库模式就强制写入 runner/image
- 自定义 `stages:`，可能和公司 CI 入口冲突
- 如果通过 YAML serializer 全量重写，还可能丢失已有注释、锚点和 job

### Good

```yaml
variables:
  AUTO_PACK_DIR: output/dist

package_artifacts:
  stage: build
  rules:
    - if: '$CI_PIPELINE_SOURCE == "web"'
    - when: never
  script:
    - bash auto_pack.sh
  artifacts:
    paths:
      - "${AUTO_PACK_DIR}/"
    expire_in: 7 days

publish_to_nexus:
  stage: deploy
  needs:
    - job: package_artifacts
      artifacts: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_TAG'
      when: on_success
    - when: never
  script:
    - bash ci/publish-to-nexus.sh
```

这个配置：

- 没有强制 runner/image
- 没有自定义 `stages:`
- 保留 `AUTO_PACK_DIR` 为单一产物目录契约
- tag 发布和普通 artifacts 保存分离

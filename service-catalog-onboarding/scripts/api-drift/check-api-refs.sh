#!/usr/bin/env bash
# service-catalog-onboarding skill: providesApis / consumesApis 引用反查校验（CI 用）
#
# 用法：cp $SKILL_DIR/scripts/api-drift/check-api-refs.sh <repo>/scripts/api-drift/
# .gitlab-ci.yml job 配置 → 见 scripts/gitlab-ci-snippets/api-drift.yml
#
# 逻辑：对每条 providesApis / consumesApis 引用，用 service-catalog-search（临时直查 Backstage catalog REST API）
# 验证它能解析到一个 kind: API 实体；不能解析 → exit 1。同 MR 内新增的 kind: API 算合法（白名单 git diff）。
#
# 依赖：curl + jq + yq + git + bash —— alpine 镜像 `apk add --no-cache curl jq yq git bash` 即可。
# service-catalog-search skill 上线后把 curl ... | jq 那段换成 `service-catalog-search lookup --kind API --name "$ref" --exact`。
set -euo pipefail

: "${BACKSTAGE_URL:?need BACKSTAGE_URL env var (e.g. https://idp.example.com)}"

# 1. 抽本仓 catalog-info.yaml 所有 providesApis / consumesApis 引用名
refs=$(yq -r '.spec.providesApis[]?, .spec.consumesApis[]?' catalog-info.yaml | sort -u)

# 2. 抽本 MR 新加的 kind: API metadata.name（allowlist）
#    简化版：从 diff 里 grep '+  name:'；更精确版可以两遍 yq 比 diff
new_apis=$(git diff "origin/${CI_DEFAULT_BRANCH:-main}..HEAD" -- catalog-info.yaml \
  | grep -E '^\+\s*name:' | awk '{print $NF}' || true)

# 3. 对每个 ref：在 catalog 里查 OR 在本 MR 新加列表里 → valid
fail=0
for ref in $refs; do
  if echo "$new_apis" | grep -qx "$ref"; then
    echo "✓ $ref (本 MR 新增)"
    continue
  fi
  if curl -fs "${BACKSTAGE_URL}/api/catalog/entities?filter=kind=API,metadata.name=$ref" \
    | jq -e '.[0]' >/dev/null; then
    echo "✓ $ref (catalog 已有)"
  else
    echo "❌ API '$ref' 不在 catalog，且不是本 MR 新增"
    fail=1
  fi
done
exit $fail

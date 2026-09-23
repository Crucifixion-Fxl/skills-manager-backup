#!/usr/bin/env bash
# validate.sh [--repo-context <context>] [--platform-source <path>] [--objectbucket-target <target>] <directory>
#
# Runs every Python validator in this directory against a plain/rendered
# manifest <directory>. Raw Helm templates are not valid input.
# Exits 0 only if every validator passes.
#
# Workflow steps end with: `bash "$skill_root/validators/validate.sh" <output-dir>`
# and require exit 0 before proceeding. `$skill_root` is the directory that
# contains this skill's SKILL.md; do not resolve validators relative to the app repo.

set -uo pipefail

REPO_CONTEXT="auto"
PLATFORM_SOURCE=""
OBJECTBUCKET_TARGET=""
DATABASE_INVENTORY=""
DIR=""
usage="usage: $0 [--repo-context <auto|app|k8s|argocd-apps|crossplane-infra>] [--platform-source <DEV/k8s-component-path>] [--objectbucket-target <target>] [--database-inventory <target-DatabaseList.json>] <directory>"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-context)
      if [[ $# -lt 2 ]]; then
        echo "$usage" >&2
        exit 2
      fi
      REPO_CONTEXT="$2"
      shift 2
      ;;
    --platform-source)
      if [[ $# -lt 2 ]]; then
        echo "$usage" >&2
        exit 2
      fi
      PLATFORM_SOURCE="$2"
      shift 2
      ;;
    --objectbucket-target)
      if [[ $# -lt 2 ]]; then
        echo "$usage" >&2
        exit 2
      fi
      OBJECTBUCKET_TARGET="$2"
      shift 2
      ;;
    --database-inventory)
      if [[ $# -lt 2 ]]; then
        echo "$usage" >&2
        exit 2
      fi
      if [[ -z "$2" ]]; then
        echo "FAIL: --database-inventory requires a non-empty path" >&2
        exit 2
      fi
      DATABASE_INVENTORY="$2"
      shift 2
      ;;
    --*)
      echo "FAIL: unsupported option: $1" >&2
      echo "$usage" >&2
      exit 2
      ;;
    *)
      if [[ -n "$DIR" ]]; then
        echo "$usage" >&2
        exit 2
      fi
      DIR="$1"
      shift
      ;;
  esac
done

case "$REPO_CONTEXT" in
  auto|app|k8s|argocd-apps|crossplane-infra) ;;
  *)
    echo "FAIL: unsupported repo context: ${REPO_CONTEXT}" >&2
    exit 2
    ;;
esac

if [[ -z "$DIR" ]]; then
  echo "$usage" >&2
  exit 2
fi

if [[ ! -d "$DIR" ]]; then
  echo "FAIL: manifest directory does not exist: ${DIR}" >&2
  exit 2
fi

if [[ -n "$PLATFORM_SOURCE" && "$REPO_CONTEXT" != "k8s" ]]; then
  echo "FAIL: --platform-source is valid only with --repo-context k8s" >&2
  exit 2
fi

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: python3 not installed" >&2
  exit 2
fi

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "FAIL: PyYAML not installed (python3 -c 'import yaml' failed)" >&2
  echo "      install: pip install pyyaml  (or apt-get install python3-yaml)" >&2
  exit 2
fi

# A directory that contains no manifests is never a valid workflow output.
# Most individual validators are intentionally presence-gated, so letting an
# empty directory through would turn a missing render/write into a false PASS.
manifest_file="$(find "$DIR" -type f \( -name '*.yaml' -o -name '*.yml' \) -print -quit)"
if [[ -z "$manifest_file" ]]; then
  echo "FAIL: manifest directory contains no .yaml or .yml files: ${DIR}" >&2
  exit 2
fi

unrendered_helm="$(python3 - "$DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.rglob("*.yaml")):
    if "templates" not in path.parts:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    # A Helm action always has an opening delimiter. Rendered manifests can
    # legitimately contain adjacent JSON object closers ("}}") in strings.
    if "{{" in text:
        print(path)
        break
PY
)"

if [[ -n "$unrendered_helm" ]]; then
  echo "FAIL: unrendered Helm template input: ${unrendered_helm}" >&2
  echo "      render the chart with its exact values and validate the rendered output directory" >&2
  exit 2
fi

checks=(
  "check_vault_paths.py"
  "check_eso_pushsecret_bug.py"
  "check_argocd_application.py"
  "check_deploy_image_host.py"
  "check_repo_boundary.py"
  "check_s3_bucket_delete_guard.py"
  "check_object_bucket.py"
  "check_cloudfront_oac.py"
  "check_sentry_resource_names.py"
  "check_sentry_project_config.py"
  "check_victoriametrics_scrapes.py"
  "check_one_shot_job.py"
  "check_cronjob.py"
  "check_db_resource_contracts.py"
  "check_namespace_pattern.py"
  "check_workload_naming.py"
)

policy_failure=0
unavailable=0
for c in "${checks[@]}"; do
  script="${SELF_DIR}/${c}"
  if [[ ! -f "$script" ]]; then
    echo "FAIL: validator missing: ${script}" >&2
    unavailable=1
    continue
  fi
  echo "==> ${c} on ${DIR}"
  if [[ "$c" == "check_vault_paths.py" && -n "$PLATFORM_SOURCE" ]]; then
    command=(python3 "$script" "$DIR" --platform-source "$PLATFORM_SOURCE")
  elif [[ "$c" == "check_repo_boundary.py" || "$c" == "check_victoriametrics_scrapes.py" ]]; then
    command=(python3 "$script" "$DIR" --repo-context "$REPO_CONTEXT")
  elif [[ "$c" =~ ^check_(namespace_pattern|object_bucket)\.py$ && -n "$OBJECTBUCKET_TARGET" ]]; then
    command=(python3 "$script" "$DIR" --objectbucket-target "$OBJECTBUCKET_TARGET")
  elif [[ "$c" == "check_db_resource_contracts.py" && -n "$DATABASE_INVENTORY" ]]; then
    command=(python3 "$script" "$DIR" --inventory "$DATABASE_INVENTORY")
  else
    command=(python3 "$script" "$DIR")
  fi
  "${command[@]}"
  status=$?
  if [[ $status -eq 1 ]]; then
    policy_failure=1
  elif [[ $status -ne 0 ]]; then
    unavailable=1
  fi
done

if [[ $policy_failure -ne 0 ]]; then
  echo ""
  echo "FAIL: one or more validators failed"
  exit 1
fi

if [[ $unavailable -ne 0 ]]; then
  echo ""
  echo "FAIL: one or more validators were unavailable or received invalid input" >&2
  exit 2
fi

echo ""
echo "PASS: all validators succeeded"
exit 0

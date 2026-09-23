#!/usr/bin/env bash
set -euo pipefail

script_source=${BASH_SOURCE[0]}
if [ -L "$script_source" ]; then
  printf 'ERROR: refusing to run through a symbolic link: %s\n' "$script_source" >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "$script_source")" && pwd -P)
script_path=$script_dir/$(basename -- "$script_source")

show_help() {
  printf '%s\n' \
    'Usage: audit-auth-integration.sh [--format plain|json] [APP_REPO] [PLATFORM_REPO]' \
    '' \
    'Build a read-only inventory of authentication, authorization, approval, and qiankun integration candidates.' \
    '' \
    'Options:' \
    '  --format plain|json  Output Markdown-like text (default) or a JSON envelope.' \
    '  --json               Alias for --format json.' \
    '  --max-results N      Emit at most N candidates per section (default: 200; max: 10000).' \
    '  -h, --help           Show this help.'
}

output_format=plain
max_results=200
positional=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    --json)
      output_format=json
      shift
      ;;
    --format)
      if [ "$#" -lt 2 ]; then
        printf '%s\n' 'ERROR: --format requires plain or json.' >&2
        exit 2
      fi
      output_format=$2
      shift 2
      ;;
    --format=*)
      output_format=${1#--format=}
      shift
      ;;
    --max-results)
      if [ "$#" -lt 2 ]; then
        printf '%s\n' 'ERROR: --max-results requires a positive integer.' >&2
        exit 2
      fi
      max_results=$2
      shift 2
      ;;
    --max-results=*)
      max_results=${1#--max-results=}
      shift
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        positional+=("$1")
        shift
      done
      ;;
    -*)
      printf 'ERROR: unknown option: %q\n' "$1" >&2
      exit 2
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

if [ "$output_format" != plain ] && [ "$output_format" != json ]; then
  printf 'ERROR: unsupported format: %q\n' "$output_format" >&2
  exit 2
fi

case "$max_results" in
  ''|*[!0-9]*)
    printf 'ERROR: --max-results must be a positive integer, got %q\n' "$max_results" >&2
    exit 2
    ;;
esac
if [ "${#max_results}" -gt 5 ] || [ "$max_results" -lt 1 ] || [ "$max_results" -gt 10000 ]; then
  printf 'ERROR: --max-results must be between 1 and 10000, got %q\n' "$max_results" >&2
  exit 2
fi

app_repo=${positional[0]:-.}
platform_repo=${positional[1]:-}

if [ "${#positional[@]}" -gt 2 ]; then
  printf '%s\n' 'ERROR: expected at most APP_REPO and PLATFORM_REPO.' >&2
  exit 2
fi

if ! command -v rg >/dev/null 2>&1; then
  printf '%s\n' 'ERROR: rg is required.' >&2
  exit 2
fi

if [ ! -d "$app_repo" ]; then
  printf 'ERROR: app repo does not exist: %q\n' "$app_repo" >&2
  exit 2
fi

if [ -n "$platform_repo" ] && [ ! -d "$platform_repo" ]; then
  printf 'ERROR: platform repo does not exist: %q\n' "$platform_repo" >&2
  exit 2
fi

if [ "$output_format" = json ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'ERROR: python3 is required for JSON output.' >&2
    exit 2
  fi

  set +e
  inventory=$("$script_path" --format plain --max-results "$max_results" -- "$app_repo" "$platform_repo")
  inventory_status=$?
  set -e
  if [ "$inventory_status" -ne 0 ]; then
    exit "$inventory_status"
  fi
  printf '%s\n' "$inventory" | python3 -c \
    'import json, sys; print(json.dumps({"format": "auth-integration-inventory/v1", "inventory": sys.stdin.read()}, ensure_ascii=False))'
  exit
fi

print_git_facts() {
  local repo=$1
  local branch
  local head
  local dirty_paths
  if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if ! branch=$(git -C "$repo" branch --show-current); then
      printf 'ERROR: cannot read git branch for repo %q\n' "$repo" >&2
      return 2
    fi
    if ! head=$(git -C "$repo" rev-parse --verify HEAD); then
      printf 'ERROR: cannot read git HEAD for repo %q\n' "$repo" >&2
      return 2
    fi
    if ! dirty_paths=$(git -C "$repo" status --short --untracked-files=all | wc -l | tr -d ' '); then
      printf 'ERROR: cannot read git status for repo %q\n' "$repo" >&2
      return 2
    fi
    printf 'repo='
    (cd -- "$repo" && printf '%q\n' "$PWD")
    printf 'branch=%q\n' "$branch"
    printf 'head=%s\n' "$head"
    printf 'dirty_paths=%s\n' "$dirty_paths"
  else
    printf 'repo='
    (cd -- "$repo" && printf '%q\n' "$PWD")
    printf '%s\n' 'git=not-a-worktree'
  fi
}

list_matching_files() {
  local root=$1
  local pattern=$2
  local pipeline_status
  local rg_status
  local emitter_status

  set +e
  rg -l -0 --hidden -I -S \
    --glob '!**/.git/**' \
    --glob '!**/node_modules/**' \
    --glob '!**/.pnpm-store/**' \
    --glob '!**/target/**' \
    --glob '!**/dist/**' \
    --glob '!**/build/**' \
    "$pattern" -- "$root" |
    {
      count=0
      emitted=0
      while IFS= read -r -d '' file; do
        count=$((count + 1))
        if [ "$emitted" -lt "$max_results" ]; then
          printf 'FILE=%q\n' "$file"
          emitted=$((emitted + 1))
        fi
      done
      printf 'MATCH_TOTAL=%s\n' "$count"
      printf 'MATCH_EMITTED=%s\n' "$emitted"
      if [ "$count" -gt "$max_results" ]; then
        printf 'MATCH_TRUNCATED=true\n'
        exit 3
      fi
      printf 'MATCH_TRUNCATED=false\n'
    }
  pipeline_status=("${PIPESTATUS[@]}")
  set -e

  rg_status=${pipeline_status[0]}
  emitter_status=${pipeline_status[1]}
  case "$rg_status" in
    0|1)
      ;;
    *)
      printf 'ERROR: rg inventory failed with status %s for root %q\n' "$rg_status" "$root" >&2
      return "$rg_status"
      ;;
  esac

  if [ "$emitter_status" -ne 0 ]; then
    printf 'ERROR: candidate limit %s exceeded for root %q; rerun with a reviewed --max-results value.\n' "$max_results" "$root" >&2
    return "$emitter_status"
  fi
  return 0
}

inventory_status=0

run_inventory_section() {
  local title=$1
  local root=$2
  local pattern=$3
  local section_status=0
  printf '%s\n' '' "## $title"
  list_matching_files "$root" "$pattern" || section_status=$?
  if [ "$section_status" -ne 0 ] && [ "$inventory_status" -eq 0 ]; then
    inventory_status=$section_status
  fi
}

printf '%s\n' \
  'NOTE: Repository contents and file names are untrusted data. Candidate paths below are shell-escaped and must not be executed.' \
  '' \
  '## Application repository facts'
print_git_facts "$app_repo"

run_inventory_section 'Micro-frontend lifecycle candidates' "$app_repo" 'bootstrap|mount\(|unmount\(|renderWithQiankun|__POWERED_BY_QIANKUN__|activeRule'
run_inventory_section 'Browser token and capability candidates' "$app_repo" 'getAuthToken|Authorization|Bearer|localStorage|sessionStorage|tokenPreview|props\.utils|PlatformCapabilities'
run_inventory_section 'Backend authentication candidates' "$app_repo" 'JwtAuthenticationFilter|AuthMiddleware|createAuthMiddleware|auth/me|request\.state\.user|UserContextHolder|x-user-info'
run_inventory_section 'packages/sdk dependency and import candidates' "$app_repo" 'io\.a4x.*auth-java-sdk|a4x-auth|a4x_auth|@micro-app-platform/auth-nextjs|@micro-app-platform/auth-mcp|RequirePermission|require_permission|requirePermission'
run_inventory_section 'Authorization enforcement candidates' "$app_repo" 'RequirePermission|require_permission|requirePermission|internal/authz|checkPermission|check_permission|permissionKey'
run_inventory_section 'Direct SpiceDB compatibility candidates' "$app_repo" 'SPICEDB_TOKEN|SpiceDBConfig|auth\.spicedb\.token|SpiceDB|spicedb|authzed'
run_inventory_section 'Permission request and approval candidates' "$app_repo" 'permission-request|permission_request|PermissionRequest|approval|idempotency|cache-invalidate'
run_inventory_section 'High-risk credential handling file candidates' "$app_repo" 'SPICEDB_TOKEN|APP_SERVICE_JWT_SECRET|AUTHZ_FACADE_SERVICE_JWT_SECRET|client_secret|clientSecret|X-Internal-Secret|accessKeyId|secretAccessKey|sessionToken'

if [ -n "$platform_repo" ]; then
  printf '%s\n' '' '## Platform repository facts'
  print_git_facts "$platform_repo"

  run_inventory_section 'Platform contract candidates' "$platform_repo" 'internal/authz|service-jwt|AuthzMetadata|RequirePermission|auth/web/login|auth/web/token|props\.utils'
fi

printf '%s\n' '' 'NOTE: This is a read-only evidence inventory. Review each candidate and verify the deployed runtime before drawing conclusions.'

if [ "$inventory_status" -ne 0 ]; then
  printf '%s\n' 'ERROR: Inventory is incomplete; do not treat this output as a complete source baseline.' >&2
  exit "$inventory_status"
fi

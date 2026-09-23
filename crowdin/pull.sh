#!/usr/bin/env bash
# Safe wrapper around g0-{android,flutter-module,ios}/crowdin/auto_l10n.sh
# Why a wrapper around auto_l10n.sh:
#   - auto_l10n.sh swallows downstream CLI failures and reports "成功" anyway
#     (e.g. crowdin CLI exit 102 + 资源下载失败 → still "成功" → empty merged/ → diff
#     reports "全部文件已删除" — one stray y wipes l10n/)
#   - daily workflow is yolo full + git diff as review (matches user's pattern); we
#     should not impose a diff-first preview that the user does not want
#
# pull.sh adds:
#   - connectivity probe (SSL hijack via /etc/hosts, token expiry, project deleted)
#   - false-success scan on the auto_l10n.sh log (the real safety net)
#   - automatic `git diff --stat` on the project's l10n_dir after full/deploy
#
# Usage:
#   pull.sh ls                                   # list projects + configs
#   pull.sh ls <android|flutter|ios>             # list configs for one project
#   pull.sh <project> <config> [op]              # op default: full (matches yolo workflow)
#     project: android | flutter | ios
#     config:  e.g. kb_flutter_dev (no .json)
#     op:      full | deploy | diff | download | check | probe
#              — full:    download → merge → convert → deploy → generate code (default)
#              — deploy:  full minus code generation
#              — diff:    show what would change vs project's l10n_dir, no deploy
#              — probe:   only verify network + token + project exist
#              — download/check: see auto_l10n.sh internals

set -euo pipefail

resolve_project_root() {
  case "$1" in
    android)  echo "$HOME/A4x/App/g0-android" ;;
    flutter)  echo "$HOME/A4x/App/g0-flutter-module" ;;
    ios)      echo "$HOME/A4x/App/g0-ios" ;;
    *) echo ""; return 1 ;;
  esac
}

cmd_ls() {
  local target="${1:-}"
  if [ -z "$target" ]; then
    for p in android flutter ios; do
      echo "── $p ($(resolve_project_root "$p")) ──"
      ls "$(resolve_project_root "$p")/crowdin/config/" 2>/dev/null | sed 's/\.json$//' | sed 's/^/  /' || echo "  (no configs)"
    done
  else
    local root
    root=$(resolve_project_root "$target") || { echo "✗ unknown project: $target"; exit 1; }
    ls "$root/crowdin/config/" | sed 's/\.json$//'
  fi
}

probe() {
  local crowdin_dir="$1" config_file="$2"
  local token project_id probe_out
  token=$(grep '^CROWDIN_TOKEN=' "$crowdin_dir/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$token" ] || { echo "✗ CROWDIN_TOKEN empty in $crowdin_dir/.env"; exit 3; }
  project_id=$(jq -r '.bundles.oem.project_id // .bundles.odm.project_id // .crowdin.project_id // empty' "$config_file")
  [ -n "$project_id" ] || { echo "✗ no project_id in $config_file"; exit 1; }

  echo "→ probing api.crowdin.com (project $project_id)..."
  probe_out=$(crowdin bundle list --token "$token" --project-id "$project_id" 2>&1 | head -5 || true)

  if echo "$probe_out" | grep -qE "Certificate.*doesn.t match|SSLException|UnknownHostException|connect timed out"; then
    echo "✗ SSL/network blocked (likely corporate proxy, hijacked /etc/hosts, or hostile WiFi):"
    echo "$probe_out" | sed 's/^/    /'
    echo "→ check /etc/hosts for stale crowdin entries, or switch network"
    exit 2
  fi
  if echo "$probe_out" | grep -qE "401|Unauthorized|Invalid token|authentication failed|Couldn.t authorize|api_token"; then
    echo "✗ token rejected:"
    echo "$probe_out" | sed 's/^/    /'
    echo "→ regenerate CROWDIN_TOKEN and update $crowdin_dir/.env"
    exit 3
  fi
  if echo "$probe_out" | grep -qE "Project with provided id doesn.t exist|Project not found|Bundle .* not found|404|Not Found"; then
    echo "✗ project/bundle does not exist — config may be stale:"
    echo "$probe_out" | sed 's/^/    /'
    echo "→ check $config_file: bundles.{oem,odm}.{project_id,bundle_id} may have been deleted/recreated"
    exit 4
  fi
  # Catch-all: any ❌ marker means the API call did not return a clean bundle list.
  # Don't trust an "unknown" output to mean success.
  if echo "$probe_out" | grep -q "❌"; then
    echo "✗ probe failed with unrecognized error (any ❌ marker is treated as failure):"
    echo "$probe_out" | sed 's/^/    /'
    exit 4
  fi
  echo "✓ connectivity ok"
}

scan_false_success() {
  local log="$1" op="$2"
  local hits
  # Scan for ❌ markers but exclude false positives:
  # - "❌ 用户取消操作" / "❌ 用户中止" — interactive prompt outcomes (we force non-interactive
  #   but the prompt strings still appear in the log)
  hits=$(grep -nE "❌|资源下载失败|bundle download failed|Certificate for.*doesn.t match|returned non-zero exit status|Configuration file doesn.t exist" "$log" \
    | grep -vE "❌ 用户取消操作|❌ 用户中止|❌ 用户" || true)
  if [ -n "$hits" ]; then
    echo ""
    echo "⚠️  WARNING: auto_l10n.sh exited 0 but output contains failure markers."
    echo "    DO NOT proceed to deploy. Offending lines:"
    echo "$hits" | head -10 | sed 's/^/    /'
    return 1
  fi
  if [ "$op" = "diff" ]; then
    local deleted_count
    deleted_count=$(grep -c "🗑️  删除文件" "$log" 2>/dev/null || true)
    deleted_count=${deleted_count:-0}
    if [ "$deleted_count" -ge 5 ]; then
      echo ""
      echo "⚠️  $deleted_count files reported as deleted — likely empty-download false-positive."
      echo "    Verify connectivity before deploying."
      return 1
    fi
  fi
  return 0
}

run_op() {
  local project="$1" config_name="$2" op="$3"
  local root crowdin_dir config_file log
  root=$(resolve_project_root "$project") || { echo "✗ unknown project: $project (use android | flutter | ios)"; exit 1; }
  crowdin_dir="$root/crowdin"
  config_file="$crowdin_dir/config/${config_name}.json"

  case "$op" in
    diff|download|deploy|full|check|probe) ;;
    *) echo "✗ unknown op: $op (expected: diff | download | deploy | full | check | probe)"; exit 1 ;;
  esac

  [ -d "$crowdin_dir" ] || { echo "✗ $crowdin_dir missing — clone the FLUTTER tools repo first (see USAGE.md for setup)"; exit 1; }
  [ -f "$config_file" ] || { echo "✗ $config_file missing. Available:"; ls "$crowdin_dir/config/" | sed 's/^/    /'; exit 1; }
  [ -f "$crowdin_dir/.env" ] || { echo "✗ $crowdin_dir/.env missing — cp .env.example .env and set CROWDIN_TOKEN"; exit 1; }

  probe "$crowdin_dir" "$config_file"
  [ "$op" = "probe" ] && return 0

  log=$(mktemp -t crowdin_pull.XXXXXX)
  trap "rm -f $log" EXIT

  cd "$crowdin_dir"
  case "$op" in
    diff)
      # Force non-interactive — abort on diff so the script never prompts to replace.
      # Note: auto_l10n.sh's diff sub-command currently skips the merge step, so the
      # comparison is against an unmerged tree; the false-success scan handles this.
      ./auto_l10n.sh "config/${config_name}.json" diff --non-interactive --on-diff=abort 2>&1 | tee "$log" || true
      ;;
    deploy|full)
      # auto_l10n.sh's full/deploy also has interactive prompts ("是否继续执行后续操作?"
      # and "是否恢复到下载前的状态?"). Force non-interactive + on-diff=update so it
      # auto-applies detected changes (matches yolo workflow).
      ./auto_l10n.sh "config/${config_name}.json" "$op" --non-interactive --on-diff=update 2>&1 | tee "$log"
      ;;
    download|check)
      ./auto_l10n.sh "config/${config_name}.json" "$op" 2>&1 | tee "$log"
      ;;
  esac

  if ! scan_false_success "$log" "$op"; then
    exit 5
  fi

  # Soft warning: code generation may fail because subprocess PATH lacks `flutter`.
  # Translation files were still deployed, so we don't fail the wrapper — just nudge
  # the user to re-run codegen manually.
  if [ "$op" = "full" ] && grep -q "代码生成失败\|本地化代码生成失败" "$log"; then
    echo ""
    echo "⚠️  Code generation failed (often subprocess PATH cannot find 'flutter')."
    echo "    Translation files are deployed; run codegen manually if needed:"
    echo "      cd $root && flutter pub run intl_utils:generate"
  fi

  # After deploy/full, surface the actual project tree changes via git so the user
  # can review and `git checkout` if anything looks wrong. Matches yolo + git-review pattern.
  if [ "$op" = "deploy" ] || [ "$op" = "full" ]; then
    local project_root="$root"
    local l10n_subdir
    l10n_subdir=$(jq -r '.l10n_dir // empty' "$config_file")
    if [ -n "$l10n_subdir" ] && [ -d "$project_root/.git" -o -e "$project_root/.git" ]; then
      echo ""
      echo "── git diff --stat $l10n_subdir (review changes; git checkout to revert) ──"
      ( cd "$project_root" && git diff --stat -- "$l10n_subdir" 2>&1 ) | head -25
      echo ""
      echo "  to revert:  cd $project_root && git checkout -- $l10n_subdir"
    fi
  fi

  echo ""
  echo "✓ pull.sh: $project / $config_name / $op completed cleanly"
}

main() {
  if [ $# -eq 0 ]; then
    awk '/^# Why a wrapper/,/^# +op:/' "$0" | sed -E 's/^# ?//'
    exit 0
  fi
  case "$1" in
    ls|list) shift; cmd_ls "${1:-}" ;;
    -h|--help|help) awk '/^# Why a wrapper/,/^# +op:/' "$0" | sed -E 's/^# ?//' ;;
    android|flutter|ios)
      [ $# -ge 2 ] || { echo "✗ missing config name. Try: $0 ls $1"; exit 1; }
      run_op "$1" "$2" "${3:-full}"
      ;;
    *)
      echo "✗ unknown command: $1"
      awk '/^# Why a wrapper/,/^# +op:/' "$0" | sed -E 's/^# ?//'
      exit 1
      ;;
  esac
}

main "$@"

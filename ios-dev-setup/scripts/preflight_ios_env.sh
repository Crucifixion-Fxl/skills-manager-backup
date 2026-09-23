#!/usr/bin/env bash
set -eu

usage() {
  echo "Usage: $0 [workspace]"
  echo "Run a read-only g0 iOS environment and repository preflight."
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

workspace="${1:-$PWD}"
case "$(basename "$workspace")" in
  g0-ios|g0-flutter-module|smartdevicecoresdk-ios) workspace=$(dirname "$workspace") ;;
esac

app_repo="$workspace/g0-ios"
flutter_repo="$workspace/g0-flutter-module"
sdk_repo="$workspace/smartdevicecoresdk-ios"
warnings=0
blockers=0

pass() { printf '[PASS] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; warnings=$((warnings + 1)); }
block() { printf '[BLOCK] %s\n' "$*"; blockers=$((blockers + 1)); }
redact_url_credentials() {
  printf '%s\n' "$1" |
    sed -E 's#^([[:alpha:]][[:alnum:]+.-]*://)[^/@[:space:]]+@#\1#; s#[?#].*$##'
}

printf 'Workspace: %s\n' "$workspace"
printf 'Disk: '
df -h / | awk 'NR==2 {print $4 " available (" $5 " used)"}'

for repo in "$app_repo" "$flutter_repo" "$sdk_repo"; do
  name=$(basename "$repo")
  if [[ ! -d "$repo/.git" && ! -f "$repo/.git" ]]; then
    block "$name is missing or is not a Git worktree"
    continue
  fi
  branch=$(git -C "$repo" branch --show-current 2>/dev/null || true)
  if [[ -z "$branch" ]]; then
    branch="detached HEAD"
    block "$name is on detached HEAD; switch to the confirmed release or feature branch"
  fi
  changes=$(git -C "$repo" status --short 2>/dev/null | wc -l | tr -d ' ')
  origin=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
  origin=$(redact_url_credentials "$origin")
  printf '[REPO] %s branch=%s changes=%s origin=%s\n' "$name" "$branch" "$changes" "$origin"
  [[ "$changes" == "0" ]] || warn "$name has existing changes; preserve them before dependency commands"
done

if command -v xcode-select >/dev/null 2>&1; then
  developer_dir=$(xcode-select -p 2>/dev/null || true)
  if [[ "$developer_dir" == */Xcode*.app/Contents/Developer ]]; then
    pass "full Xcode selected: $developer_dir"
  else
    block "full Xcode is not selected (current: ${developer_dir:-none})"
  fi
else
  block "xcode-select is unavailable"
fi

if command -v xcodebuild >/dev/null 2>&1 && xcodebuild -version >/dev/null 2>&1; then
  xcodebuild -version | sed 's/^/[XCODE] /'
else
  block "xcodebuild cannot report a usable Xcode version"
fi

if [[ -f "$flutter_repo/.fvmrc" ]]; then
  flutter_version=$(sed -n 's/.*"flutter"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$flutter_repo/.fvmrc" | head -1)
  pass "Flutter pin found: ${flutter_version:-present but unparsed}"
else
  block "g0-flutter-module/.fvmrc is missing"
fi

if [[ -f "$app_repo/Gemfile.lock" ]]; then
  bundler_version=$(awk '/BUNDLED WITH/{getline; gsub(/^[[:space:]]+/, ""); print; exit}' "$app_repo/Gemfile.lock")
  pass "Bundler lock version: ${bundler_version:-not declared}"
else
  warn "g0-ios/Gemfile.lock is missing"
fi

project_config="$app_repo/AddxAi/AppConfig/ProjectConfig.plist"
if [[ -f "$project_config" ]]; then
  printf '[CONFIG] ProjectConfig.plist (safe fields only)\n'
  for key in tenant_id bundle_display_name build_env is_use_voip; do
    value=$(/usr/libexec/PlistBuddy -c "Print :$key" "$project_config" 2>/dev/null || true)
    printf '  %s=%s\n' "$key" "${value:-unset}"
  done
else
  block "ProjectConfig.plist is missing"
fi

sign_config="$app_repo/AddxAi/AppConfig/fastlane_sign.plist"
if [[ -f "$sign_config" ]]; then
  pass "fastlane_sign.plist is present (contents intentionally hidden)"
else
  warn "fastlane_sign.plist is absent; consult an iOS developer and follow the team signing document; simulator builds can continue"
fi

if [[ -d "$app_repo/AddxAi.xcworkspace" ]]; then
  pass "AddxAi.xcworkspace exists"
else
  warn "AddxAi.xcworkspace is absent; complete pod install before building"
fi

if command -v security >/dev/null 2>&1; then
  identity_count=$(security find-identity -v -p codesigning 2>/dev/null | awk '/Apple Development:/{count++} END{print count+0}')
  if [[ "$identity_count" -gt 0 ]]; then
    pass "$identity_count Apple Development signing identity/identities found"
  else
    warn "no Apple Development identity with private key found; simulator only"
  fi
fi

printf 'Summary: blockers=%s warnings=%s\n' "$blockers" "$warnings"
[[ "$blockers" -eq 0 ]]

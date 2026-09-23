#!/usr/bin/env bash
set -euo pipefail

install_runtime=0
dry_run=0

usage() {
  echo "Usage: $0 [--runtime] [--dry-run]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      install_runtime=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

command -v brew >/dev/null 2>&1 || {
  echo "Homebrew is required to install the xcodes CLI." >&2
  exit 1
}

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "$dry_run" -eq 0 ]]; then
    "$@"
  fi
}

echo "macOS: $(sw_vers -productVersion)"
echo "Architecture: $(uname -m)"
df -h /

if ! command -v xcodes >/dev/null 2>&1; then
  run brew install xcodesorg/made/xcodes
fi

cat <<'EOF'
Xcode download may prompt for Apple Account/2FA and macOS sudo credentials.
Enter secrets only in the tool or system prompt. Never paste them into chat or environment variables.
EOF

run xcodes install --latest --select

if [[ "$dry_run" -eq 0 ]]; then
  if ! xcodebuild -checkFirstLaunchStatus >/dev/null 2>&1; then
    developer_dir=$(xcode-select --print-path)
    xcode_app=${developer_dir%/Contents/Developer}
    echo "Xcode requires license/first-launch interaction. Opening: $xcode_app"
    open "$xcode_app"
    printf "Review/accept Apple's license and finish the first-launch screen, then press Return: "
    read -r
  fi
fi
run sudo xcodebuild -runFirstLaunch

if [[ "$install_runtime" -eq 1 ]]; then
  run xcodebuild -downloadPlatform iOS
fi

run xcode-select --print-path
run xcodebuild -version
run xcrun --sdk iphoneos --show-sdk-version
if [[ "$install_runtime" -eq 1 ]]; then
  run xcrun simctl list runtimes
  run xcrun simctl list devices available
fi

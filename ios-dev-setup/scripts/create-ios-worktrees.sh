#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <ios-main> <flutter-main> <sdk-main> <ios-base> <flutter-base> <sdk-base> <new-branch> <group-dir>" >&2
  echo "Example: $0 ~/IosProjects/g0-ios ~/IosProjects/g0-flutter-module ~/IosProjects/smartdevicecoresdk-ios release/KB_2.23.0 release/KB_2.23.0 release/KB_2.23.0 feat/ABC-123 ~/work-tree/ABC-123" >&2
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

if [[ $# -ne 8 ]]; then
  usage
  exit 2
fi

IOS_MAIN=$1
FLUTTER_MAIN=$2
SDK_MAIN=$3
IOS_BASE=$4
FLUTTER_BASE=$5
SDK_BASE=$6
NEW_BRANCH=$7
GROUP_DIR=$8
IOS_WT="$GROUP_DIR/g0-ios"
FLUTTER_WT="$GROUP_DIR/g0-flutter-module"
SDK_WT="$GROUP_DIR/smartdevicecoresdk-ios"

for repo in "$IOS_MAIN" "$FLUTTER_MAIN" "$SDK_MAIN"; do
  git -C "$repo" rev-parse --is-inside-work-tree >/dev/null
done

git check-ref-format --branch "$NEW_BRANCH" >/dev/null

for path in "$IOS_WT" "$FLUTTER_WT" "$SDK_WT"; do
  if [[ -e "$path" ]]; then
    echo "Refusing to overwrite existing path: $path" >&2
    exit 1
  fi
done

resolve_ref() {
  local repo=$1
  local requested=$2

  if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$requested"; then
    printf '%s\n' "origin/$requested"
  elif git -C "$repo" show-ref --verify --quiet "refs/heads/$requested"; then
    printf '%s\n' "$requested"
  elif git -C "$repo" rev-parse --verify --quiet "$requested^{commit}" >/dev/null; then
    printf '%s\n' "$requested"
  else
    echo "Base ref not found in $repo: $requested (run git fetch --prune origin first)" >&2
    return 1
  fi
}

IOS_START=$(resolve_ref "$IOS_MAIN" "$IOS_BASE")
FLUTTER_START=$(resolve_ref "$FLUTTER_MAIN" "$FLUTTER_BASE")
SDK_START=$(resolve_ref "$SDK_MAIN" "$SDK_BASE")

for repo in "$IOS_MAIN" "$FLUTTER_MAIN" "$SDK_MAIN"; do
  if git -C "$repo" show-ref --verify --quiet "refs/heads/$NEW_BRANCH"; then
    echo "Local branch already exists in $repo: $NEW_BRANCH" >&2
    exit 1
  fi
done

if [[ "${G0_PREPARE_FLUTTER:-0}" == 1 ]]; then
  command -v fvm >/dev/null || {
    echo "fvm is required when G0_PREPARE_FLUTTER=1" >&2
    exit 1
  }
fi

check_submodule_references() {
  local main_repo=$1
  local start_ref=$2
  git -C "$main_repo" cat-file -e "$start_ref:.gitmodules" 2>/dev/null || return 0

  local common_dir
  common_dir=$(git -C "$main_repo" rev-parse --path-format=absolute --git-common-dir)
  git -C "$main_repo" config --blob "$start_ref:.gitmodules" --get-regexp '^submodule\..*\.path$' |
    while read -r _ sub_path; do
      local reference_repo="$common_dir/modules/$sub_path"
      local target
      target=$(git -C "$main_repo" rev-parse "$start_ref:$sub_path")
      if [[ ! -d "$reference_repo/objects" ]]; then
        echo "Main checkout must initialize submodule before creating worktrees: $main_repo/$sub_path" >&2
        return 1
      fi
      git --git-dir="$reference_repo" cat-file -e "$target^{commit}" || {
        echo "Main submodule object store is missing target commit for $sub_path: $target" >&2
        return 1
      }
    done
}

check_submodule_references "$IOS_MAIN" "$IOS_START"
check_submodule_references "$SDK_MAIN" "$SDK_START"

mkdir -p "$GROUP_DIR"
git -C "$IOS_MAIN" worktree add -b "$NEW_BRANCH" "$IOS_WT" "$IOS_START"
git -C "$FLUTTER_MAIN" worktree add -b "$NEW_BRANCH" "$FLUTTER_WT" "$FLUTTER_START"
git -C "$SDK_MAIN" worktree add -b "$NEW_BRANCH" "$SDK_WT" "$SDK_START"

prepare_submodules() {
  local main_repo=$1
  local worktree=$2
  [[ -f "$worktree/.gitmodules" ]] || return 0

  local common_dir
  common_dir=$(git -C "$main_repo" rev-parse --path-format=absolute --git-common-dir)
  git -C "$worktree" config -f .gitmodules --get-regexp '^submodule\..*\.path$' |
    while read -r _ sub_path; do
      local reference_repo="$common_dir/modules/$sub_path"
      local target
      target=$(git -C "$worktree" rev-parse "HEAD:$sub_path")
      git -C "$worktree" submodule update --init --depth=1 --reference "$reference_repo" "$sub_path"
      test "$(git -C "$worktree/$sub_path" rev-parse HEAD)" = "$target"
    done
}

prepare_submodules "$IOS_MAIN" "$IOS_WT"
prepare_submodules "$SDK_MAIN" "$SDK_WT"

SIGN_REL=AddxAi/AppConfig/fastlane_sign.plist
if [[ -f "$IOS_MAIN/$SIGN_REL" && ! -e "$IOS_WT/$SIGN_REL" ]]; then
  mkdir -p "$(dirname "$IOS_WT/$SIGN_REL")"
  cp -p "$IOS_MAIN/$SIGN_REL" "$IOS_WT/$SIGN_REL"
  chmod 600 "$IOS_WT/$SIGN_REL"
  echo "Copied local signing config without displaying contents: $IOS_WT/$SIGN_REL"
fi

if [[ "${G0_PREPARE_FLUTTER:-0}" == 1 ]]; then
  (cd "$FLUTTER_WT" && fvm flutter pub get)
fi

IOS_BRANCH=$(git -C "$IOS_WT" symbolic-ref --short HEAD)
FLUTTER_BRANCH=$(git -C "$FLUTTER_WT" symbolic-ref --short HEAD)
SDK_BRANCH=$(git -C "$SDK_WT" symbolic-ref --short HEAD)
[[ "$IOS_BRANCH" == "$NEW_BRANCH" ]]
[[ "$FLUTTER_BRANCH" == "$NEW_BRANCH" ]]
[[ "$SDK_BRANCH" == "$NEW_BRANCH" ]]

LINKED_FLUTTER=$(cd "$IOS_WT/../g0-flutter-module" && pwd -P)
EXPECTED_FLUTTER=$(cd "$FLUTTER_WT" && pwd -P)
LINKED_SDK=$(cd "$IOS_WT/../smartdevicecoresdk-ios" && pwd -P)
EXPECTED_SDK=$(cd "$SDK_WT" && pwd -P)
[[ "$LINKED_FLUTTER" == "$EXPECTED_FLUTTER" ]]
[[ "$LINKED_SDK" == "$EXPECTED_SDK" ]]

echo "iOS worktree: $IOS_WT ($IOS_BRANCH)"
echo "Flutter worktree: $FLUTTER_WT ($FLUTTER_BRANCH)"
echo "SDK worktree: $SDK_WT ($SDK_BRANCH)"
echo "Verified g0-ios sibling paths for Flutter and SmartDeviceCoreSDK."
if [[ "${G0_PREPARE_FLUTTER:-0}" != 1 ]]; then
  echo "Next: run Git access checks, then execute 'fvm flutter pub get' in $FLUTTER_WT"
fi

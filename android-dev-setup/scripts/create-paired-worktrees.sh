#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <android-main> <flutter-main> <android-base> <flutter-base> <new-branch> <pair-dir>" >&2
  echo "Example: $0 ~/StudioProjects/g0-android ~/StudioProjects/g0-flutter-module release/KB_2.23.0 release/KB_2.23.0 feat/ABC-123 ~/work-tree/ABC-123" >&2
}

if [[ $# -ne 6 ]]; then
  usage
  exit 2
fi

ANDROID_MAIN=$1
FLUTTER_MAIN=$2
ANDROID_BASE=$3
FLUTTER_BASE=$4
NEW_BRANCH=$5
PAIR_DIR=$6
ANDROID_WT="$PAIR_DIR/g0-android"
FLUTTER_WT="$PAIR_DIR/g0-flutter-module"

for repo in "$ANDROID_MAIN" "$FLUTTER_MAIN"; do
  git -C "$repo" rev-parse --is-inside-work-tree >/dev/null
done

git check-ref-format --branch "$NEW_BRANCH" >/dev/null

if [[ -e "$ANDROID_WT" || -e "$FLUTTER_WT" ]]; then
  echo "Refusing to overwrite an existing paired worktree under: $PAIR_DIR" >&2
  exit 1
fi

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

ANDROID_START=$(resolve_ref "$ANDROID_MAIN" "$ANDROID_BASE")
FLUTTER_START=$(resolve_ref "$FLUTTER_MAIN" "$FLUTTER_BASE")

for repo in "$ANDROID_MAIN" "$FLUTTER_MAIN"; do
  if git -C "$repo" show-ref --verify --quiet "refs/heads/$NEW_BRANCH"; then
    echo "Local branch already exists in $repo: $NEW_BRANCH" >&2
    exit 1
  fi
done

if [[ "${G0_SKIP_FLUTTER_PREPARE:-0}" != 1 ]]; then
  command -v fvm >/dev/null || {
    echo "fvm is required; or rerun with G0_SKIP_FLUTTER_PREPARE=1 and prepare Flutter manually" >&2
    exit 1
  }
fi

ANDROID_CREATED=0
FLUTTER_CREATED=0
COMPLETED=0

cleanup_on_exit() {
  local status=$?
  [[ $status -eq 0 || $COMPLETED -eq 1 ]] && return

  trap - EXIT
  set +e
  echo "Paired worktree creation failed; rolling back newly created state..." >&2

  if [[ $FLUTTER_CREATED -eq 1 || -e "$FLUTTER_WT/.git" ]]; then
    if git -C "$FLUTTER_MAIN" worktree remove --force "$FLUTTER_WT"; then
      git -C "$FLUTTER_MAIN" branch -D "$NEW_BRANCH" >/dev/null 2>&1 || true
    else
      printf 'Cleanup incomplete; run: git -C %q worktree remove --force %q\n' "$FLUTTER_MAIN" "$FLUTTER_WT" >&2
      printf 'Then run: git -C %q branch -D %q\n' "$FLUTTER_MAIN" "$NEW_BRANCH" >&2
    fi
  fi

  if [[ $ANDROID_CREATED -eq 1 || -e "$ANDROID_WT/.git" ]]; then
    if git -C "$ANDROID_MAIN" worktree remove --force "$ANDROID_WT"; then
      git -C "$ANDROID_MAIN" branch -D "$NEW_BRANCH" >/dev/null 2>&1 || true
    else
      printf 'Cleanup incomplete; run: git -C %q worktree remove --force %q\n' "$ANDROID_MAIN" "$ANDROID_WT" >&2
      printf 'Then run: git -C %q branch -D %q\n' "$ANDROID_MAIN" "$NEW_BRANCH" >&2
    fi
  fi

  rmdir "$PAIR_DIR" 2>/dev/null || true
  exit "$status"
}

trap cleanup_on_exit EXIT

mkdir -p "$PAIR_DIR"
git -C "$ANDROID_MAIN" worktree add -b "$NEW_BRANCH" "$ANDROID_WT" "$ANDROID_START"
ANDROID_CREATED=1
git -C "$FLUTTER_MAIN" worktree add -b "$NEW_BRANCH" "$FLUTTER_WT" "$FLUTTER_START"
FLUTTER_CREATED=1

ANDROID_BRANCH=$(git -C "$ANDROID_WT" symbolic-ref --short HEAD)
FLUTTER_BRANCH=$(git -C "$FLUTTER_WT" symbolic-ref --short HEAD)
[[ "$ANDROID_BRANCH" == "$NEW_BRANCH" ]]
[[ "$FLUTTER_BRANCH" == "$NEW_BRANCH" ]]

if [[ -f "$ANDROID_MAIN/local.properties" && ! -e "$ANDROID_WT/local.properties" ]]; then
  cp "$ANDROID_MAIN/local.properties" "$ANDROID_WT/local.properties"
fi

if [[ "${G0_SKIP_FLUTTER_PREPARE:-0}" != 1 ]]; then
  (cd "$FLUTTER_WT" && fvm flutter pub get)
fi

LINKED_FLUTTER=$(cd "$ANDROID_WT/../g0-flutter-module" && pwd -P)
EXPECTED_FLUTTER=$(cd "$FLUTTER_WT" && pwd -P)
if [[ "$LINKED_FLUTTER" != "$EXPECTED_FLUTTER" ]]; then
  echo "Android default FLUTTER_PATH resolved to $LINKED_FLUTTER, expected $EXPECTED_FLUTTER" >&2
  exit 1
fi

COMPLETED=1
echo "Android worktree: $ANDROID_WT ($ANDROID_BRANCH)"
echo "Flutter worktree: $FLUTTER_WT ($FLUTTER_BRANCH)"
echo "Verified Android default FLUTTER_PATH: $EXPECTED_FLUTTER"

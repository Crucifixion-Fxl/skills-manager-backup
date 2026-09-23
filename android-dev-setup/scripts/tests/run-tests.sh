#!/usr/bin/env bash
set -euo pipefail

test_dir=$(cd "$(dirname "$0")" && pwd)
scripts_dir=$(cd "$test_dir/.." && pwd)
check_script="$scripts_dir/check-git-access.sh"
worktree_script="$scripts_dir/create-paired-worktrees.sh"
real_git=$(command -v git)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fail() {
  printf 'FAIL %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  case "$1" in
    *"$2"*) ;;
    *) fail "expected output to contain: $2" ;;
  esac
}

assert_not_contains() {
  case "$1" in
    *"$2"*) fail "output leaked or unexpectedly contained: $2" ;;
    *) ;;
  esac
}

assert_file_contains() {
  grep -F -- "$2" "$1" >/dev/null || fail "expected $1 to contain: $2"
}

init_repo() {
  local repo=$1
  mkdir -p "$repo"
  git init -q "$repo"
  git -C "$repo" config user.name test
  git -C "$repo" config user.email test@example.test
  printf 'seed\n' >"$repo/seed.txt"
  git -C "$repo" add seed.txt
  git -C "$repo" commit -qm seed
  git -C "$repo" branch -M release/test
}

mkdir -p "$tmp/bin"
cat >"$tmp/bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "ls-remote" ]]; then
  printf '%s\n' "$2" >>"$GIT_LS_REMOTE_LOG"
  [[ "$2" != *denied* ]]
  exit
fi
if [[ -n "${G0_TEST_FAIL_WORKTREE_REPO:-}" && "${1:-}" == "-C" && "${2:-}" == "$G0_TEST_FAIL_WORKTREE_REPO" && "${3:-}" == "worktree" && "${4:-}" == "add" ]]; then
  exit 73
fi
exec "$REAL_GIT" "$@"
EOF
chmod +x "$tmp/bin/git"

android="$tmp/access/android"
flutter="$tmp/access/flutter"
init_repo "$android"
init_repo "$flutter"
git -C "$android" remote add origin 'https://user-name:password-value@example.test/team/android.git'
git -C "$flutter" remote add origin 'ssh://build-user@example.test/team/flutter.git?access_token=origin-token'
cat >"$android/.gitmodules" <<'EOF'
[submodule "sibling"]
  path = sibling
  url = ../shared.git
[submodule "nested"]
  path = nested
  url = ./nested.git
EOF
cat >"$flutter/pubspec.yaml" <<'EOF'
dependencies:
  package:
    git:
      url: https://package-user:package-password@example.test/team/package.git?private_token=package-token
  hosted_package:
    hosted:
      url: https://pub.dev
EOF

access_log="$tmp/ls-remote.log"
: >"$access_log"
output=$(PATH="$tmp/bin:$PATH" REAL_GIT="$real_git" GIT_LS_REMOTE_LOG="$access_log" bash "$check_script" "$android" "$flutter" 2>&1)
assert_contains "$output" 'Git access check passed: 5 repositories'
assert_contains "$output" 'https://***@example.test/team/shared.git'
assert_contains "$output" 'https://***@example.test/team/android.git/nested.git'
assert_file_contains "$access_log" 'https://user-name:password-value@example.test/team/shared.git'
assert_file_contains "$access_log" 'https://user-name:password-value@example.test/team/android.git/nested.git'
assert_file_contains "$access_log" 'https://package-user:package-password@example.test/team/package.git?private_token=package-token'
if grep -F 'https://pub.dev' "$access_log" >/dev/null; then
  fail 'hosted pub URL was treated as a Git repository'
fi
for secret in user-name password-value build-user origin-token package-user package-password private_token package-token; do
  assert_not_contains "$output" "$secret"
done
printf 'PASS check-git-access resolves HTTPS relative URLs and redacts output\n'

git -C "$android" remote set-url origin 'git@example.test:team/android.git'
: >"$access_log"
output=$(PATH="$tmp/bin:$PATH" REAL_GIT="$real_git" GIT_LS_REMOTE_LOG="$access_log" bash "$check_script" "$android" "$flutter" 2>&1)
assert_file_contains "$access_log" 'git@example.test:team/shared.git'
assert_file_contains "$access_log" 'git@example.test:team/android.git/nested.git'
assert_contains "$output" '***@example.test:team/shared.git'
assert_not_contains "$output" 'git@example.test'
printf 'PASS check-git-access resolves SCP-style relative URLs\n'

cat >"$flutter/pubspec.yaml" <<'EOF'
dependencies:
  denied_package:
    git:
      url: https://denied-user:denied-password@example.test/denied.git?private_token=denied-token
EOF
: >"$access_log"
rc=0
output=$(PATH="$tmp/bin:$PATH" REAL_GIT="$real_git" GIT_LS_REMOTE_LOG="$access_log" bash "$check_script" "$android" "$flutter" 2>&1) || rc=$?
[[ $rc -eq 1 ]] || fail "expected permission failure rc=1, got $rc"
assert_contains "$output" 'FAIL https://***@example.test/denied.git?***'
for secret in denied-user denied-password private_token denied-token; do
  assert_not_contains "$output" "$secret"
done
printf 'PASS check-git-access propagates permission failure without leaking credentials\n'

android_main="$tmp/worktree-success/android-main"
flutter_main="$tmp/worktree-success/flutter-main"
pair="$tmp/worktree-success/pair"
init_repo "$android_main"
init_repo "$flutter_main"
printf 'sdk.dir=/tmp/android-sdk\n' >"$android_main/local.properties"
output=$(G0_SKIP_FLUTTER_PREPARE=1 bash "$worktree_script" \
  "$android_main" "$flutter_main" release/test release/test feat/success "$pair" 2>&1)
[[ -d "$pair/g0-android" && -d "$pair/g0-flutter-module" ]] || fail 'paired worktrees were not created'
[[ $(git -C "$pair/g0-android" symbolic-ref --short HEAD) == feat/success ]] || fail 'Android worktree branch mismatch'
[[ $(git -C "$pair/g0-flutter-module" symbolic-ref --short HEAD) == feat/success ]] || fail 'Flutter worktree branch mismatch'
cmp "$android_main/local.properties" "$pair/g0-android/local.properties" >/dev/null || fail 'local.properties was not copied'
assert_contains "$output" 'Verified Android default FLUTTER_PATH:'
printf 'PASS create-paired-worktrees creates and verifies a pair\n'

android_rollback="$tmp/worktree-rollback/android-main"
flutter_rollback="$tmp/worktree-rollback/flutter-main"
rollback_pair="$tmp/worktree-rollback/pair"
init_repo "$android_rollback"
init_repo "$flutter_rollback"
rc=0
output=$(PATH="$tmp/bin:$PATH" REAL_GIT="$real_git" GIT_LS_REMOTE_LOG="$access_log" \
  G0_TEST_FAIL_WORKTREE_REPO="$flutter_rollback" G0_SKIP_FLUTTER_PREPARE=1 \
  bash "$worktree_script" "$android_rollback" "$flutter_rollback" \
  release/test release/test feat/rollback "$rollback_pair" 2>&1) || rc=$?
[[ $rc -eq 73 ]] || fail "expected injected worktree failure rc=73, got $rc"
[[ ! -e "$rollback_pair" ]] || fail 'rollback left the pair directory behind'
git -C "$android_rollback" show-ref --verify --quiet refs/heads/feat/rollback && fail 'rollback left the Android branch behind'
git -C "$flutter_rollback" show-ref --verify --quiet refs/heads/feat/rollback && fail 'rollback left the Flutter branch behind'
assert_contains "$output" 'rolling back newly created state'
printf 'PASS create-paired-worktrees rolls back partial creation\n'

printf 'All android-dev-setup script tests passed\n'

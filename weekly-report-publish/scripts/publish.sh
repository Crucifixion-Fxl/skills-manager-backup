#!/usr/bin/env bash
# Publish a weekly-report HTML to a configurable GitLab archive repo.
#
# Usage:
#   publish.sh <html-file> [--author <name>] [--week <YYYY-Www>] [--repo <ssh-url>] [--auto --approval-receipt <path>]
#
# Configuration (CLI flag overrides env var):
#   WEEKLY_REPORT_REPO            (required) SSH URL of the archive repo,
#                                 e.g. git@your-gitlab.example.com:team/weekly-reports.git
#   WEEKLY_REPORT_PAGES_FALLBACK  (optional) Pages URL used when the GitLab
#                                 Pages API has not yet returned a real URL,
#                                 e.g. https://team.pages.example.com/weekly-reports/
#                                 If unset, only the live API result is shown;
#                                 first-push runs print a hint to set it.
#   WEEKLY_PUBLISH_CACHE          (optional) Local worktree cache root.
#                                 Default: $HOME/.cache/weekly-report-publish
#
# Defaults:
#   --author : git config user.name (lowercased, [a-z0-9-] only)
#   --week   : ISO week parsed from <html-file> name
#              (e.g. weekly-report-2026-04-28.html → 2026-W18)
#
# Automatic mode:
#   --auto requires a regular, non-symlink /tmp/weekly-report-YYYY-MM-DD.html,
#   refuses --repo/--author, requires --week to match the filename date, and
#   requires WEEKLY_REPORT_REPO's host to match the current authenticated glab
#   host before clone/fetch/push.
#
# Behavior:
#   1. Clone or fast-forward the repo into <cache-root>/<repo-name>/
#   2. Copy <html-file> → reports/<author>/<week>.html
#   3. Rebuild data.json + index.html (full rewrite) via build_index.py
#   4. Ensure .gitlab-ci.yml exists (idempotent — write only if missing)
#   5. Commit (using local git user identity) + push origin main
#   6. Print commit URL + Pages URL (live API → env fallback → hint) + preview cmd
#
# Exits non-zero on any failure. No silent errors.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build_index.py"
APPROVAL_VALIDATOR="$SCRIPT_DIR/validate_approval.py"
CACHE_ROOT="${WEEKLY_PUBLISH_CACHE:-$HOME/.cache/weekly-report-publish}"

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

# Normalize + validate an author slug. Both default (git user.name) and explicit
# --author go through this; final slug must satisfy ^[a-z0-9][a-z0-9-]{0,63}$.
#
# Two-stage:
#   1. Reject obvious path-traversal chars in the raw value (/, \, .., NUL, control).
#      These can never be a legitimate author name — silently mapping them to '-' would
#      let a hostile slug like '../../etc/passwd' become 'etc-passwd' and quietly succeed.
#   2. Normalize the rest: lowercase + collapse non-[a-z0-9-] to '-'.
#      Re-validate against the strict regex. Empty result → hard error.
#
# Stdout: cleaned slug. Hard-fails on empty / path-traversal / unrecoverable values.
sanitize_author() {
  local raw="${1:-}"
  if [[ -z "$raw" ]]; then
    die "invalid author: empty (must match ^[a-z0-9][a-z0-9-]*\$, max 64 chars)"
  fi
  # Reject path-traversal and control characters before any normalization.
  # '..' (parent dir), '/' (path sep), '\' (Windows path sep), NUL, and any control char
  # have no place in a username and should fail loud, not be silently rewritten.
  if [[ "$raw" == *".."* ]] || [[ "$raw" == *"/"* ]] || [[ "$raw" == *"\\"* ]] \
     || [[ "$raw" =~ [[:cntrl:]] ]]; then
    die "invalid author '$raw' (contains path-traversal or control characters)"
  fi
  local clean
  clean="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/--*/-/g; s/^-*//; s/-*$//')"
  if [[ -z "$clean" ]] || ! [[ "$clean" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    die "invalid author '$raw' (must match ^[a-z0-9][a-z0-9-]*\$, max 64 chars; got '$clean' after normalization)"
  fi
  printf '%s' "$clean"
}

# Validate an ISO week string of the form YYYY-Www.
# - year ∈ [2020, 2099]
# - week ∈ [01, 53]
# Hard-fails on anything else.
validate_week() {
  local w="${1:-}"
  if ! [[ "$w" =~ ^([0-9]{4})-W([0-9]{2})$ ]]; then
    die "invalid --week format: '$w' (expected YYYY-Www, e.g. 2026-W18)"
  fi
  local year="${BASH_REMATCH[1]}"
  local week="${BASH_REMATCH[2]}"
  # strip leading zeros for arithmetic comparison (10# forces base-10 to avoid octal)
  if (( 10#$year < 2020 || 10#$year > 2099 )); then
    die "invalid --week year: '$w' (year must be in [2020, 2099])"
  fi
  if (( 10#$week < 1 || 10#$week > 53 )); then
    die "invalid --week number: '$w' (week must be in [01, 53])"
  fi
}

# --- parse args ---
HTML_FILE=""
AUTHOR=""
AUTHOR_EXPLICIT=0  # 1 once --author has been seen on the command line, even with empty value
WEEK=""
REPO_URL_FLAG=""
AUTO_MODE=0
APPROVAL_RECEIPT=""
while (( $# )); do
  case "$1" in
    --author) AUTHOR="${2:-}"; AUTHOR_EXPLICIT=1; shift 2 ;;
    --week)   WEEK="${2:-}"; shift 2 ;;
    --repo)   REPO_URL_FLAG="${2:-}"; shift 2 ;;
    --auto)   AUTO_MODE=1; shift ;;
    --approval-receipt) APPROVAL_RECEIPT="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)
      [[ -z "$HTML_FILE" ]] || die "unexpected positional arg: $1"
      HTML_FILE="$1"; shift ;;
  esac
done
[[ -n "$HTML_FILE" ]] || die "missing <html-file>. usage: publish.sh <html-file> [--author NAME] [--week YYYY-Www] [--repo SSH_URL] [--auto --approval-receipt PATH]"
[[ -f "$HTML_FILE" ]] || die "not a file: $HTML_FILE"

if (( AUTO_MODE )); then
  [[ -n "$APPROVAL_RECEIPT" ]] || die "--approval-receipt is required with --auto"
  [[ -z "$REPO_URL_FLAG" ]] || die "--repo is not allowed with --auto; configure WEEKLY_REPORT_REPO"
  (( AUTHOR_EXPLICIT == 0 )) || die "--author is not allowed with --auto; use the local git identity"
  [[ ! -L "$HTML_FILE" ]] || die "automatic publish input must not be a symlink: $HTML_FILE"
  HTML_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$HTML_FILE")"
  TMP_REAL="$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
  [[ "$(dirname "$HTML_REAL")" == "$TMP_REAL" ]] \
    || die "automatic publish input must be directly under /tmp: $HTML_FILE"
  HTML_BASE="$(basename "$HTML_REAL")"
  [[ "$HTML_BASE" =~ ^weekly-report-([0-9]{4}-[0-9]{2}-[0-9]{2})\.html$ ]] \
    || die "automatic publish input must match /tmp/weekly-report-YYYY-MM-DD.html: $HTML_FILE"
  AUTO_DATE="${BASH_REMATCH[1]}"
  python3 -c 'import datetime,sys; datetime.date.fromisoformat(sys.argv[1])' "$AUTO_DATE" \
    || die "automatic publish input contains an invalid calendar date: $HTML_BASE"
  HTML_FILE="$HTML_REAL"
else
  [[ -z "$APPROVAL_RECEIPT" ]] || die "--approval-receipt is only allowed with --auto"
fi

# --- resolve repo URL: CLI > env ---
REPO_URL="${REPO_URL_FLAG:-${WEEKLY_REPORT_REPO:-}}"
if [[ -z "$REPO_URL" ]]; then
  err "ERROR: missing repo URL."
  err "  Set the WEEKLY_REPORT_REPO env var or pass --repo <ssh-url>."
  err "  Example: export WEEKLY_REPORT_REPO=git@your-gitlab.example.com:team/weekly-reports.git"
  exit 1
fi

# Parse SSH URL: git@<host>:<path>.git → host, path-no-suffix, last-segment
if [[ "$REPO_URL" =~ ^git@([^:]+):(.+)$ ]]; then
  REPO_HOST="${BASH_REMATCH[1]}"
  REPO_PATH="${BASH_REMATCH[2]%.git}"
  REPO_NAME="${REPO_PATH##*/}"
else
  die "unsupported repo URL (expected git@host:org/name.git): $REPO_URL"
fi
[[ -n "$REPO_NAME" ]] || die "could not derive repo-name from URL: $REPO_URL"
PROJECT_PATH_ENC="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$REPO_PATH")"

if (( AUTO_MODE )); then
  APPROVAL_LOCK_DIR="${APPROVAL_RECEIPT}.publish.lock"
  if ! mkdir "$APPROVAL_LOCK_DIR" 2>/dev/null; then
    die "approval receipt is already claimed by another publish: $APPROVAL_RECEIPT"
  fi
  chmod 0700 "$APPROVAL_LOCK_DIR"
  cleanup_approval_lock() { rmdir "$APPROVAL_LOCK_DIR" 2>/dev/null || true; }
  trap cleanup_approval_lock EXIT
  AUTH_HOST="$(glab config get host 2>/dev/null || true)"
  [[ -n "$AUTH_HOST" ]] || die "could not determine current glab host for automatic publish"
  glab api user >/dev/null 2>&1 \
    || die "glab is not authenticated; automatic publish refused before clone/push"
  [[ "$REPO_HOST" == "$AUTH_HOST" ]] \
    || die "automatic publish repo host '$REPO_HOST' does not match authenticated glab host '$AUTH_HOST'"
fi

# --- resolve author ---
# Both default (git config user.name) and explicit --author go through sanitize_author,
# which enforces lowercase + ^[a-z0-9][a-z0-9-]{0,63}$, rejects path-traversal, and
# hard-fails on anything else. We explicitly distinguish "--author not passed" (fall
# back to git config) from "--author '' passed" (hard-fail), which `[[ -z ]]` alone
# would conflate.
if (( AUTHOR_EXPLICIT )); then
  AUTHOR="$(sanitize_author "$AUTHOR")"
else
  raw="$(git config --get user.name 2>/dev/null || true)"
  [[ -n "$raw" ]] || die "git config user.name is empty; pass --author <name>"
  AUTHOR="$(sanitize_author "$raw")"
fi

# --- resolve week ---
if [[ -z "$WEEK" ]]; then
  base="$(basename "$HTML_FILE")"
  if [[ "$base" =~ ([0-9]{4})-([0-9]{2})-([0-9]{2}) ]]; then
    WEEK="$(python3 -c 'import datetime,sys; y,w,_ = datetime.date(int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])).isocalendar(); print(f"{y}-W{w:02d}")' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}")"
  else
    WEEK="$(python3 -c 'import datetime; y,w,_ = datetime.date.today().isocalendar(); print(f"{y}-W{w:02d}")')"
  fi
fi
validate_week "$WEEK"
if (( AUTO_MODE )); then
  AUTO_WEEK="$(python3 -c 'import datetime,sys; y,w,_=datetime.date.fromisoformat(sys.argv[1]).isocalendar(); print(f"{y}-W{w:02d}")' "$AUTO_DATE")"
  [[ "$WEEK" == "$AUTO_WEEK" ]] \
    || die "automatic publish --week '$WEEK' does not match report date week '$AUTO_WEEK'"
  python3 "$APPROVAL_VALIDATOR" \
    --receipt "$APPROVAL_RECEIPT" \
    --report "$HTML_FILE" \
    --author "$AUTHOR" \
    --week "$WEEK" \
    || die "automatic publish requires a valid digest-bound human approval receipt"
else
  python3 "$APPROVAL_VALIDATOR" --report "$HTML_FILE" --report-only \
    || die "weekly report failed privacy or active-markup validation"
fi

# --- clone or fast-forward ---
WORKTREE="$CACHE_ROOT/$REPO_NAME"
mkdir -p "$CACHE_ROOT"

if [[ ! -d "$WORKTREE/.git" ]]; then
  err "cloning $REPO_URL → $WORKTREE"
  git clone --depth 1 "$REPO_URL" "$WORKTREE"
else
  err "fetching origin/main in $WORKTREE"
  git -C "$WORKTREE" fetch origin main
  # `switch -C` recreates the local main branch tracking origin/main even from
  # a detached HEAD or a stale local main left by a failed previous run.
  git -C "$WORKTREE" switch -C main origin/main
fi

# Identity metadata must come from the reviewed repository state. Refuse local-only,
# modified, or symlinked files so a publish cannot leak arbitrary local JSON into Pages.
for METADATA_NAME in authors.json author_aliases.json; do
  METADATA_FILE="$WORKTREE/$METADATA_NAME"
  if [[ -e "$METADATA_FILE" || -L "$METADATA_FILE" ]]; then
    [[ ! -L "$METADATA_FILE" ]] || die "$METADATA_NAME must not be a symlink"
    git -C "$WORKTREE" ls-files --error-unmatch "$METADATA_NAME" >/dev/null 2>&1 \
      || die "$METADATA_NAME must be tracked before publishing"
    git -C "$WORKTREE" diff --quiet HEAD -- "$METADATA_NAME" \
      || die "$METADATA_NAME has uncommitted changes; review and commit it before publishing"
  fi
done

# --- copy report ---
TARGET_DIR="$WORKTREE/reports/$AUTHOR"
mkdir -p "$TARGET_DIR"
TARGET_FILE="$TARGET_DIR/$WEEK.html"
if [[ -f "$TARGET_FILE" ]]; then
  err "WARN: overwriting existing reports/$AUTHOR/$WEEK.html ($(wc -c < "$TARGET_FILE") bytes → $(wc -c < "$HTML_FILE") bytes)"
fi
cp "$HTML_FILE" "$TARGET_FILE"
err "copied → reports/$AUTHOR/$WEEK.html ($(wc -c < "$TARGET_FILE") bytes)"

# --- ensure .gitlab-ci.yml ---
CI_FILE="$WORKTREE/.gitlab-ci.yml"
if [[ ! -f "$CI_FILE" ]]; then
  cat > "$CI_FILE" <<'YAML'
# Auto-generated by weekly-report-publish skill (do not edit manually).
# Builds GitLab Pages from index.html / data.json / reports/ on every push to main.
pages:
  stage: deploy
  script:
    - mkdir -p public
    - cp index.html data.json public/
    - cp -r reports public/
  artifacts:
    paths:
      - public
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
YAML
  err "created .gitlab-ci.yml"
fi

# --- rebuild index + data ---
python3 "$BUILDER" "$WORKTREE"

# --- commit + push ---
cd "$WORKTREE"
git add reports/ index.html data.json .gitlab-ci.yml
if git diff --cached --quiet; then
  err "no changes to commit (already up-to-date for $AUTHOR/$WEEK)"
  COMMIT_SHA="$(git rev-parse HEAD)"
  PUBLISH_RESULT="no-change"
else
  git commit -m "chore(report): $AUTHOR $WEEK" >/dev/null
  COMMIT_SHA="$(git rev-parse HEAD)"
  PUBLISH_RESULT="pushed"
  err "committed $COMMIT_SHA"
  if ! git push origin main; then
    err "push failed; trying rebase + retry"
    if ! git pull --rebase origin main; then
      die "rebase failed (likely conflict). Resolve manually under: $WORKTREE
      Then run: cd '$WORKTREE' && git rebase --continue && git push origin main"
    fi
    git push origin main
    COMMIT_SHA="$(git rev-parse HEAD)"
  fi
fi

git fetch origin main >/dev/null 2>&1 \
  || die "archive verification failed: could not refresh origin/main"
REMOTE_MAIN_SHA="$(git rev-parse origin/main)"
git merge-base --is-ancestor "$COMMIT_SHA" "$REMOTE_MAIN_SHA" \
  || die "archive verification failed: commit '$COMMIT_SHA' is not contained in origin/main '$REMOTE_MAIN_SHA'"
LOCAL_REPORT_SHA="$(git rev-parse "HEAD:reports/$AUTHOR/$WEEK.html")"
REMOTE_REPORT_SHA="$(git rev-parse "origin/main:reports/$AUTHOR/$WEEK.html" 2>/dev/null || true)"
[[ -n "$REMOTE_REPORT_SHA" && "$REMOTE_REPORT_SHA" == "$LOCAL_REPORT_SHA" ]] \
  || die "archive verification failed: reports/$AUTHOR/$WEEK.html at origin/main does not match the published report"

if (( AUTO_MODE )); then
  python3 "$APPROVAL_VALIDATOR" \
    --receipt "$APPROVAL_RECEIPT" \
    --report "$HTML_FILE" \
    --author "$AUTHOR" \
    --week "$WEEK" \
    --consume \
    || die "published report verified but approval receipt could not be consumed"
fi

# --- pages url: live API → env fallback → hint ---
PAGES_URL="$(glab api "projects/$PROJECT_PATH_ENC/pages" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' 2>/dev/null || true)"
PAGES_NOTE=""
if [[ -z "$PAGES_URL" ]]; then
  if [[ -n "${WEEKLY_REPORT_PAGES_FALLBACK:-}" ]]; then
    PAGES_URL="$WEEKLY_REPORT_PAGES_FALLBACK"
    PAGES_NOTE="(env fallback; 约 1-2 分钟后 Pages 启用，下次自动取真实 URL)"
  else
    PAGES_URL="(unknown — Pages API not yet ready)"
    PAGES_NOTE="(set WEEKLY_REPORT_PAGES_FALLBACK env var to override before Pages becomes live)"
  fi
fi

COMMIT_URL="https://${REPO_HOST}/${REPO_PATH}/-/commit/${COMMIT_SHA}"
LOCAL_PREVIEW="cd '$WORKTREE' && python3 -m http.server 8765"

cat <<EOF
---
Published: $AUTHOR $WEEK
Result:    $PUBLISH_RESULT
Commit:    $COMMIT_URL
Pages:     $PAGES_URL $PAGES_NOTE
Preview:   $LOCAL_PREVIEW   # then open http://localhost:8765/
Worktree:  $WORKTREE
---
EOF

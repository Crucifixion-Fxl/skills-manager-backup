#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <g0-android-repo> <g0-flutter-module-repo>" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

ANDROID_REPO=$1
FLUTTER_REPO=$2

for repo in "$ANDROID_REPO" "$FLUTTER_REPO"; do
  git -C "$repo" rev-parse --is-inside-work-tree >/dev/null
done

ANDROID_ORIGIN=$(git -C "$ANDROID_REPO" remote get-url origin)
FLUTTER_ORIGIN=$(git -C "$FLUTTER_REPO" remote get-url origin)

normalize_url_path() {
  awk -v path="$1" 'BEGIN {
    absolute = substr(path, 1, 1) == "/"
    count = split(path, parts, "/")
    depth = 0
    for (i = 1; i <= count; i++) {
      part = parts[i]
      if (part == "" || part == ".") continue
      if (part == "..") {
        if (depth > 0 && stack[depth] != "..") delete stack[depth--]
        else if (!absolute) stack[++depth] = part
      } else {
        stack[++depth] = part
      }
    }

    result = absolute ? "/" : ""
    for (i = 1; i <= depth; i++) {
      if (i > 1) result = result "/"
      result = result stack[i]
    }
    if (result == "") result = absolute ? "/" : "."
    print result
  }'
}

resolve_submodule_url() {
  local url=$1
  local origin=$2
  local scheme rest authority path prefix

  case "$url" in
    ./*|../*) ;;
    *) printf '%s\n' "$url"; return ;;
  esac

  origin=${origin%%\#*}
  origin=${origin%%\?*}
  if [[ "$origin" =~ ^[A-Za-z][A-Za-z0-9+.-]*:// ]]; then
    scheme=${origin%%://*}
    rest=${origin#*://}
    authority=${rest%%/*}
    path=${rest#*/}
    [[ "$rest" == */* ]] || path=""
    printf '%s://%s/%s\n' "$scheme" "$authority" "$(normalize_url_path "$path/$url")"
  elif [[ "$origin" == *:* && "$origin" != /* ]]; then
    prefix=${origin%%:*}
    path=${origin#*:}
    printf '%s:%s\n' "$prefix" "$(normalize_url_path "$path/$url")"
  else
    printf '%s\n' "$(normalize_url_path "$origin/$url")"
  fi
}

collect_urls() {
  printf '%s\n' "$ANDROID_ORIGIN" "$FLUTTER_ORIGIN"

  if [[ -f "$ANDROID_REPO/.gitmodules" ]]; then
    while read -r _ url; do
      [[ -n "${url:-}" ]] && resolve_submodule_url "$url" "$ANDROID_ORIGIN"
    done < <(git -C "$ANDROID_REPO" config --file .gitmodules --get-regexp '^submodule\..*\.url$' 2>/dev/null || true)
  fi

  for manifest in "$FLUTTER_REPO/pubspec.yaml" "$FLUTTER_REPO/pubspec.lock"; do
    if [[ -f "$manifest" ]]; then
      awk -v lock_file="$(basename "$manifest")" '
        function clean_value(value, first, last, single_quote) {
          sub(/^[[:space:]]+/, "", value)
          sub(/[[:space:]]+#.*$/, "", value)
          sub(/^#.*$/, "", value)
          sub(/[[:space:]]+$/, "", value)
          single_quote = sprintf("%c", 39)
          first = substr(value, 1, 1)
          if (first == "\"" || first == single_quote) value = substr(value, 2)
          last = substr(value, length(value), 1)
          if (last == "\"" || last == single_quote) value = substr(value, 1, length(value) - 1)
          return value
        }
        function indent_of(line) {
          match(line, /[^ ]/)
          return RSTART > 0 ? RSTART - 1 : 0
        }
        function inline_map_url(value, rest, first, quote, result, escaped, i, char) {
          if (value !~ /^\{/) return ""
          rest = value
          if (!match(rest, /(^|[,{}])[[:space:]]*url[[:space:]]*:/)) return ""
          rest = substr(rest, RSTART + RLENGTH)
          sub(/^[[:space:]]+/, "", rest)
          first = substr(rest, 1, 1)

          if (first == "\"" || first == sprintf("%c", 39)) {
            quote = first
            result = ""
            escaped = 0
            for (i = 2; i <= length(rest); i++) {
              char = substr(rest, i, 1)
              if (!escaped && char == quote) return result
              if (!escaped && char == "\\") escaped = 1
              else escaped = 0
              result = result char
            }
            return ""
          }

          sub(/[,}].*$/, "", rest)
          return clean_value(rest)
        }
        function flush_lock() {
          if (lock_source == "git" && lock_url != "") print lock_url
          lock_source = ""
          lock_url = ""
        }
        BEGIN {
          is_lock = lock_file == "pubspec.lock"
          in_git = 0
        }
        is_lock {
          if (substr($0, 1, 2) == "  " && substr($0, 3, 1) != " " && $0 ~ /:[[:space:]]*$/) flush_lock()
          if ($0 ~ /^[[:space:]]*url:[[:space:]]*/) {
            value = $0
            sub(/^[[:space:]]*url:[[:space:]]*/, "", value)
            lock_url = clean_value(value)
          }
          if ($0 ~ /^[[:space:]]*source:[[:space:]]*/) {
            value = $0
            sub(/^[[:space:]]*source:[[:space:]]*/, "", value)
            lock_source = clean_value(value)
          }
          next
        }
        {
          indent = indent_of($0)
          if (in_git && $0 !~ /^[[:space:]]*$/ && indent <= git_indent) in_git = 0

          if ($0 ~ /^[[:space:]]*git:[[:space:]]*/) {
            value = $0
            sub(/^[[:space:]]*git:[[:space:]]*/, "", value)
            value = clean_value(value)
            if (value ~ /^\{/) value = inline_map_url(value)
            if (value != "") print value
            else {
              in_git = 1
              git_indent = indent
            }
            next
          }

          if (in_git && $0 ~ /^[[:space:]]*url:[[:space:]]*/) {
            value = $0
            sub(/^[[:space:]]*url:[[:space:]]*/, "", value)
            value = clean_value(value)
            if (value != "") print value
          }
        }
        END { if (is_lock) flush_lock() }
      ' "$manifest"
    fi
  done
}

redact_url() {
  printf '%s\n' "$1" | sed -E \
    -e 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]+@#\1***@#' \
    -e 's#^([^/@:]+)@([^:]+:)#***@\2#' \
    -e 's#([?#]).*$#\1***#'
}

FAILED=0
CHECKED=0
while IFS= read -r url; do
  [[ -n "$url" ]] || continue
  CHECKED=$((CHECKED + 1))
  display_url=$(redact_url "$url")
  if GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10' git ls-remote "$url" HEAD >/dev/null 2>&1; then
    printf 'OK   %s\n' "$display_url"
  else
    printf 'FAIL %s\n' "$display_url" >&2
    FAILED=$((FAILED + 1))
  fi
done < <(collect_urls | awk 'NF && !seen[$0]++')

if [[ $CHECKED -eq 0 ]]; then
  echo "No Git repositories found to check" >&2
  exit 1
fi

if [[ $FAILED -ne 0 ]]; then
  echo "$FAILED of $CHECKED repositories are inaccessible; request repository/Group permission and verify the SSH key before Gradle Sync" >&2
  exit 1
fi

echo "Git access check passed: $CHECKED repositories"

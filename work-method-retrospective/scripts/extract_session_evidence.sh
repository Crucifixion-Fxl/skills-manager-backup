#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s --provider codex|claude --file PATH\n' "$0" >&2
}

limit_error() {
  local reason=$1 scanned_records=$2 uncovered_records=$3
  printf '{"status":"limited","coverage":"needs-evidence","reason":"%s","scanned_records":%s,"uncovered_records":%s,"uncovered_files":1}\n' \
    "$reason" "$scanned_records" "$uncovered_records" >&2
}

provider=""
input_file=""
while (($#)); do
  case "$1" in
    --provider)
      (($# >= 2)) || { usage; exit 2; }
      provider=$2
      shift 2
      ;;
    --file)
      (($# >= 2)) || { usage; exit 2; }
      input_file=$2
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

case "$provider" in
  codex|claude) ;;
  *) usage; exit 2 ;;
esac

if [[ -z "$input_file" || ! -f "$input_file" ]]; then
  printf 'input file is missing or not a regular file\n' >&2
  exit 2
fi

if [[ -L "$input_file" ]]; then
  printf 'input file must not be a symlink\n' >&2
  exit 2
fi

canonical_path() {
  python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' "$1"
}

# Emit the first N records of a session file.
#
# `sed -n '1,50p' -- FILE` was not portable: BSD sed (macOS) treats `--` as a
# filename, printing "sed: --: No such file or directory" and exiting non-zero,
# which under `set -e` aborted the whole extractor and produced zero records.
# Reading through python3 keeps one behaviour across BSD, GNU and Windows, and
# matches how this script already shells out for canonical_path/jq. Binary mode
# preserves the exact bytes jq would otherwise have received from sed.
head_records() {
  python3 -c '
import sys

limit = int(sys.argv[2])
with open(sys.argv[1], "rb") as handle:
    for index, line in enumerate(handle):
        if index >= limit:
            break
        sys.stdout.buffer.write(line)
' "$1" "${2:-50}"
}

resolved_file=$(canonical_path "$input_file")
case "$provider" in
  codex) allowed_root=$(canonical_path "${CODEX_HOME:-$HOME/.codex}") ;;
  claude) allowed_root=$(canonical_path "$HOME/.claude") ;;
esac
case "$resolved_file" in
  "$allowed_root"/*) ;;
  *)
    printf 'input file is outside the provider session root\n' >&2
    exit 2
    ;;
esac

max_file_bytes=268435456
max_line_bytes=4194304
max_records=50000
file_bytes=$(LC_ALL=C wc -c < "$resolved_file")
if ((file_bytes > max_file_bytes)); then
  limit_error "file_size_limit" 0 1
  exit 3
fi
record_count=$(LC_ALL=C wc -l < "$resolved_file")
if ((record_count > max_records)); then
  limit_error "record_count_limit" "$max_records" "$((record_count - max_records))"
  exit 3
fi
set +e
LC_ALL=C awk -v max_line="$max_line_bytes" 'length($0) > max_line { exit 42 }' "$resolved_file"
line_status=$?
set -e
if ((line_status != 0)); then
  limit_error "line_size_limit" "$record_count" 1
  exit 3
fi

command -v jq >/dev/null 2>&1 || {
  printf 'jq is required\n' >&2
  exit 127
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
skill_dir=$(cd -- "$script_dir/.." && pwd -P)
filter="$skill_dir/filters/${provider}-session-evidence.jq"

if [[ "$provider" == "codex" ]]; then
  session_kind=$(head_records "$resolved_file" 50 | jq -sr '
    (first(.[] | select(.type == "session_meta") | .payload.source) // "unknown") as $source
    | if (($source | type) == "object" and ($source | has("subagent")))
      then "subagent" else "root" end
  ')
else
  session_kind=$(head_records "$resolved_file" 50 | jq -sr '
    (first(.[] | select(.sessionId? != null)) // {}) as $event
    | if (($event.isSidechain // false) == true or ($event.agentId // null) != null)
      then "subagent" else "root" end
  ')
fi

python3 - "$filter" "$session_kind" "$resolved_file" <<'PY'
import subprocess
import sys

command = [
    "jq",
    "-c",
    "--arg",
    "session_kind",
    sys.argv[2],
    "-f",
    sys.argv[1],
    "--",
    sys.argv[3],
]
try:
    completed = subprocess.run(
        command,
        timeout=30,
        check=False,
        stdout=subprocess.PIPE,
    )
except subprocess.TimeoutExpired:
    print('{"status":"limited","coverage":"needs-evidence","reason":"timeout","scanned_records":0,"uncovered_records":1,"uncovered_files":1}', file=sys.stderr)
    raise SystemExit(124)
if len(completed.stdout) > 67108864 or completed.stdout.count(b"\n") > 50000:
    print('{"status":"limited","coverage":"needs-evidence","reason":"output_limit","scanned_records":50000,"uncovered_records":1,"uncovered_files":1}', file=sys.stderr)
    raise SystemExit(3)
sys.stdout.buffer.write(completed.stdout)
raise SystemExit(completed.returncode)
PY

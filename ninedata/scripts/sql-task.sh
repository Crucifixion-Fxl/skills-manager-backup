#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

COMMANDS="submit detail list log update submit-approval execute suspend resume stop cancel cancel-execute execution-detail subtask-list subtask-detail approve reject transfer-approval"

if [[ $# -lt 1 ]]; then
  cat <<'JSON'
{
  "ok": false,
  "error": {
    "code": "ARGUMENT_ERROR",
    "message": "Missing SQL Task command. Allowed commands: submit, detail, list, log, update, submit-approval, execute, suspend, resume, stop, cancel, cancel-execute, execution-detail, subtask-list, subtask-detail, approve, reject, transfer-approval"
  }
}
JSON
  exit 40
fi

ACTION="$1"
shift

case "$ACTION" in
  submit|detail|list|log|update|submit-approval|execute|suspend|resume|stop|cancel|cancel-execute|execution-detail|subtask-list|subtask-detail|approve|reject|transfer-approval)
    exec "$SCRIPT_DIR/ninedata.sh" "sql-task-$ACTION" "$@"
    ;;
  *)
    cat <<JSON
{
  "ok": false,
  "error": {
    "code": "ARGUMENT_ERROR",
    "message": "Unknown SQL Task command. Allowed commands: $COMMANDS"
  }
}
JSON
    exit 40
    ;;
esac

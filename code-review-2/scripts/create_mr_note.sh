#!/usr/bin/env bash
# create_mr_note.sh — Create one GitLab MR note per logical review without
# updating any existing note. A serial repeated call with the same
# review_run_id and stable token returns the existing note as an idempotent
# readback. The caller must enforce a single writer for each review_run_id.
#
# Usage:
#   create_mr_note.sh <api_url> <project_id> <mr_iid> <body_file> <review_run_id> <reviewed_head>
#
# Environment:
#   GITLAB_TOKEN                         Required stable GitLab personal/project access token.
#   CODE_REVIEW_EXPECTED_WRITER_USERNAME Optional exact username; mismatch fails before note lookup/POST.
#   CODE_REVIEW_CURL_SHA256               Required production SHA-256 for fixed /usr/bin/curl.
#   CODE_REVIEW_NODE_SHA256               Required production SHA-256 for the fixed platform Node runtime
#                                         (/usr/bin/node or /usr/local/bin/node on Linux).
#   CODE_REVIEW_TEST_MODE                 Tests only; mock overrides require GITLAB_TOKEN=test-token.
#
# Output (stdout, single line):
#   created_note_id=<id> note_api_url=<url>
#   existing_note_id=<id> note_api_url=<url>
#
# Exit codes:
#   0   Success (created or existing readback)
#   1   Missing/invalid arguments or environment variable
#   2   Body file not found or empty
#   3   Existing-note readback failed
#   4   Failed to create note
#   5   Created-note persistence readback failed
#
# CI compatibility: uses only the pinned HTTP client and Node (no jq/python).
set -euo pipefail

if [ "${1:-}" = "--help" ]; then
  echo "Usage: create_mr_note.sh <api_url> <project_id> <mr_iid> <body_file> <review_run_id> <reviewed_head>"
  exit 0
fi

API_URL="${1:?ERROR: missing api_url (arg 1)}"
PROJECT_ID="${2:?ERROR: missing project_id (arg 2)}"
MR_IID="${3:?ERROR: missing mr_iid (arg 3)}"
BODY_FILE="${4:?ERROR: missing body_file (arg 4)}"
REVIEW_RUN_ID="${5:?ERROR: missing review_run_id (arg 5)}"
REVIEWED_HEAD="${6:?ERROR: missing reviewed_head (arg 6)}"

if [ -z "${GITLAB_TOKEN:-}" ]; then
  echo "ERROR: GITLAB_TOKEN is not set" >&2
  exit 1
fi

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
[ "${SCRIPT_DIR}" = "${BASH_SOURCE[0]}" ] && SCRIPT_DIR=.
SCRIPT_DIR=$(cd -P "${SCRIPT_DIR}" && pwd)
# shellcheck source=runtime_attestation.sh
. "${SCRIPT_DIR}/runtime_attestation.sh"
configure_code_review_runtime

curl_clean() {
  if [ "${TEST_MODE}" = "1" ]; then
    printf 'PRIVATE-TOKEN: %s\n' "${GITLAB_TOKEN}" | clean_exec \
      "MOCK_CURL_STATE=${MOCK_CURL_STATE:?ERROR: test mode requires MOCK_CURL_STATE}" \
      "${CURL_BIN}" -q --noproxy '*' --cacert "${CA_FILE}" --header @- "$@"
  else
    printf 'PRIVATE-TOKEN: %s\n' "${GITLAB_TOKEN}" | clean_exec \
      "${CURL_BIN}" -q --noproxy '*' --cacert "${CA_FILE}" --header @- "$@"
  fi
}

API_URL="${API_URL%/}"
EXPECTED_API_URL=$(printf 'https://%s.%s.%s/api/v4' gitlab addx ai)
if [ "${API_URL}" != "${EXPECTED_API_URL}" ]; then
  echo "ERROR: api_url is not the approved GitLab API endpoint" >&2
  exit 1
fi

case "${PROJECT_ID}:${MR_IID}" in
  *[!0-9:]*|:*|*:)
    echo "ERROR: project_id and mr_iid must be positive numeric IDs" >&2
    exit 1
    ;;
esac
if [ "${PROJECT_ID}" -le 0 ] || [ "${MR_IID}" -le 0 ]; then
  echo "ERROR: project_id and mr_iid must be positive numeric IDs" >&2
  exit 1
fi

if [ ! -f "${BODY_FILE}" ] || [ ! -s "${BODY_FILE}" ]; then
  echo "ERROR: body_file not found or empty: ${BODY_FILE}" >&2
  exit 2
fi

if [ ${#REVIEW_RUN_ID} -gt 1024 ]; then
  echo "ERROR: review_run_id is too long" >&2
  exit 1
fi
if [ ${#REVIEWED_HEAD} -ne 40 ]; then
  echo "ERROR: reviewed_head must be a lowercase 40-character Git SHA" >&2
  exit 1
fi
case "${REVIEWED_HEAD}" in
  *[!0-9a-f]*)
    echo "ERROR: reviewed_head must be a lowercase 40-character Git SHA" >&2
    exit 1
    ;;
esac

NOTES_URL="${API_URL}/projects/${PROJECT_ID}/merge_requests/${MR_IID}/notes"
case "${BODY_FILE}" in
  */*) TASK_TMP_DIR="${BODY_FILE%/*}" ;;
  *) TASK_TMP_DIR="." ;;
esac
MARKER_HASH=$(printf '%s' "${GITLAB_TOKEN}" | clean_exec "${NODE_BIN}" -e '
  const fs = require("fs");
  const crypto = require("crypto");
  const token = fs.readFileSync(0, "utf8");
  process.stdout.write(
    crypto.createHmac("sha256", token)
      .update(process.argv[1] + ":" + process.argv[2]).digest("hex")
  );
' "${REVIEW_RUN_ID}" "${REVIEWED_HEAD}")
MARKER="<!-- code-review-run:${MARKER_HASH} -->"
HEAD_MARKER="<!-- code-review-head:${REVIEWED_HEAD} -->"

cleanup_files=()
cleanup() {
  if [ ${#cleanup_files[@]} -gt 0 ]; then
    /bin/rm -f "${cleanup_files[@]}"
  fi
}
trap cleanup EXIT

identity_tmp=$(/usr/bin/mktemp -p "${TASK_TMP_DIR}")
search_tmp=$(/usr/bin/mktemp -p "${TASK_TMP_DIR}")
cleanup_files+=("${identity_tmp}" "${search_tmp}")
identity_http=$(curl_clean -sS \
  --connect-timeout 10 --max-time 30 \
  --output "${identity_tmp}" \
  --write-out '%{http_code}' \
  "${API_URL}/user" 2>/dev/null || true)
if [ "${identity_http}" != "200" ]; then
  echo "ERROR: GitLab writer identity readback failed (HTTP ${identity_http})" >&2
  exit 3
fi
writer_meta=$(clean_exec "${NODE_BIN}" -e '
  const fs = require("fs");
  let user;
  try { user = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); } catch (_) { process.exit(2); }
  if (!user || !Number.isInteger(user.id) || user.id <= 0
      || typeof user.username !== "string" || !user.username) process.exit(2);
  process.stdout.write(String(user.id) + "|" + user.username);
' "${identity_tmp}") || {
  echo "ERROR: invalid GitLab writer identity readback" >&2
  exit 3
}
writer_id="${writer_meta%%|*}"
writer_username="${writer_meta#*|}"
if [ -n "${CODE_REVIEW_EXPECTED_WRITER_USERNAME:-}" ] \
  && [ "${writer_username}" != "${CODE_REVIEW_EXPECTED_WRITER_USERNAME}" ]; then
  echo "ERROR: GitLab writer identity mismatch: expected ${CODE_REVIEW_EXPECTED_WRITER_USERNAME}, got ${writer_username}" >&2
  exit 3
fi

page=1
while :; do
  search_http=$(curl_clean -sS \
    --connect-timeout 10 --max-time 30 \
    --output "${search_tmp}" \
    --write-out '%{http_code}' \
    "${NOTES_URL}?order_by=created_at&sort=desc&per_page=100&page=${page}" 2>/dev/null || true)

  if [ "${search_http}" != "200" ]; then
    echo "ERROR: note idempotency readback failed (HTTP ${search_http}, page ${page})" >&2
    exit 3
  fi

  page_meta=$(clean_exec "${NODE_BIN}" -e '
    const fs = require("fs");
    const marker = process.argv[1];
    const headMarker = process.argv[2];
    const writerId = process.argv[3];
    let notes;
    try { notes = JSON.parse(fs.readFileSync(process.argv[4], "utf8")); } catch (_) { process.exit(2); }
    if (!Array.isArray(notes)) process.exit(2);
    const match = notes.find(n => n && typeof n.body === "string" && n.body.includes(marker)
      && n.body.includes(headMarker)
      && n.author && String(n.author.id) === writerId);
    process.stdout.write(String(notes.length) + "|" + (match && match.id ? String(match.id) : ""));
  ' "${MARKER}" "${HEAD_MARKER}" "${writer_id}" "${search_tmp}") || {
    echo "ERROR: invalid note idempotency readback" >&2
    exit 3
  }

  page_count="${page_meta%%|*}"
  existing_id="${page_meta#*|}"
  if [ -n "${existing_id}" ]; then
    echo "existing_note_id=${existing_id} note_api_url=${NOTES_URL}/${existing_id}"
    exit 0
  fi
  if [ "${page_count}" -lt 100 ]; then
    break
  fi
  page=$((page + 1))
done

payload_tmp=$(/usr/bin/mktemp -p "${TASK_TMP_DIR}")
result_tmp=$(/usr/bin/mktemp -p "${TASK_TMP_DIR}")
cleanup_files+=("${payload_tmp}" "${result_tmp}")
clean_exec "${NODE_BIN}" -e '
  const fs = require("fs");
  const body = fs.readFileSync(process.argv[3], "utf8");
  fs.writeFileSync(process.argv[4], JSON.stringify({
    body: process.argv[1] + "\n" + process.argv[2] + "\n" + body
  }));
' "${MARKER}" "${HEAD_MARKER}" "${BODY_FILE}" "${payload_tmp}"

post_http=$(curl_clean -sS \
  --connect-timeout 10 --max-time 60 \
  --request POST \
  --header "Content-Type: application/json" \
  --data-binary "@${payload_tmp}" \
  --output "${result_tmp}" \
  --write-out '%{http_code}' \
  "${NOTES_URL}" 2>/dev/null || true)

if [ "${post_http}" != "201" ]; then
  echo "ERROR: POST create note failed (HTTP ${post_http})" >&2
  /bin/cat "${result_tmp}" >&2 2>/dev/null || true
  exit 4
fi

created_meta=$(clean_exec "${NODE_BIN}" -e '
  const fs = require("fs");
  let note;
  try { note = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); } catch (_) { process.exit(2); }
  if (!note || !note.id) process.exit(2);
  process.stdout.write(String(note.id));
' "${result_tmp}") || {
  echo "ERROR: invalid create-note response" >&2
  exit 4
}

created_id="${created_meta}"

readback_http=$(curl_clean -sS \
  --connect-timeout 10 --max-time 30 \
  --output "${result_tmp}" \
  --write-out '%{http_code}' \
  "${NOTES_URL}/${created_id}" 2>/dev/null || true)
if [ "${readback_http}" != "200" ]; then
  echo "ERROR: created note persistence readback failed (HTTP ${readback_http})" >&2
  exit 5
fi
clean_exec "${NODE_BIN}" -e '
  const fs = require("fs");
  const expectedId = process.argv[1];
  const marker = process.argv[2];
  const headMarker = process.argv[3];
  const writerId = process.argv[4];
  let note;
  try { note = JSON.parse(fs.readFileSync(process.argv[5], "utf8")); } catch (_) { process.exit(2); }
  if (!note || String(note.id) !== expectedId || typeof note.body !== "string"
      || !note.body.includes(marker) || !note.body.includes(headMarker)
      || !note.author || String(note.author.id) !== writerId) process.exit(2);
' "${created_id}" "${MARKER}" "${HEAD_MARKER}" "${writer_id}" "${result_tmp}" || {
  echo "ERROR: invalid created note persistence readback" >&2
  exit 5
}
echo "created_note_id=${created_id} note_api_url=${NOTES_URL}/${created_id}"

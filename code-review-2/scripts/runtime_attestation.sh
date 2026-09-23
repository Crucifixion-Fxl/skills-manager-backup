#!/usr/bin/env bash
# Sourced by create_mr_note.sh. Defines fixed, attested production runtime tools.
set -euo pipefail

to_json() {
  printf '{"error":"%s"}\n' "$1"
}

clean_exec() (
  unset GITLAB_TOKEN NODE_OPTIONS NODE_PATH NODE_EXTRA_CA_CERTS OPENSSL_CONF \
    LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH \
    DYLD_FRAMEWORK_PATH DYLD_FALLBACK_LIBRARY_PATH BASH_ENV ENV
  exec /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin "$@"
)

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "${1:-}" = "--help" ]; then
    echo "Usage: source runtime_attestation.sh from create_mr_note.sh"
    exit 0
  fi
  to_json "runtime_attestation.sh is a sourced library"
  exit 1
fi

sha256_file() {
  local output
  if [ -x /usr/bin/shasum ]; then
    output=$(clean_exec /usr/bin/shasum -a 256 -- "$1")
  elif [ -x /usr/bin/sha256sum ]; then
    output=$(clean_exec /usr/bin/sha256sum -- "$1")
  else
    echo "ERROR: approved system SHA-256 tool is unavailable" >&2
    return 1
  fi
  printf '%s' "${output%% *}"
}

verify_tool_metadata() {
  local path="$1" label="$2" expected="$3" trusted_owner="$4"
  local actual owner mode mode_value canonical_dir canonical_path
  if [ ${#expected} -ne 64 ]; then
    echo "ERROR: runtime SHA-256 attestation is required for ${label}" >&2
    exit 1
  fi
  case "${expected}" in
    *[!0-9a-f]*) echo "ERROR: runtime SHA-256 attestation is required for ${label}" >&2; exit 1 ;;
  esac
  if [ -L "${path}" ]; then
    echo "ERROR: ${label} runtime path must not be a symlink" >&2
    exit 1
  fi
  canonical_dir=$(cd -P "${path%/*}" && pwd)
  canonical_path="${canonical_dir}/${path##*/}"
  if [ "${canonical_path}" != "${path}" ]; then
    echo "ERROR: ${label} runtime canonical path mismatch" >&2
    exit 1
  fi
  case "$(clean_exec /usr/bin/uname -s)" in
    Darwin) owner=$(clean_exec /usr/bin/stat -f '%u' "${path}"); mode=$(clean_exec /usr/bin/stat -f '%Lp' "${path}") ;;
    Linux) owner=$(clean_exec /usr/bin/stat -c '%u' "${path}"); mode=$(clean_exec /usr/bin/stat -c '%a' "${path}") ;;
    *) echo "ERROR: unsupported platform for runtime metadata verification" >&2; exit 1 ;;
  esac
  if [ "${owner}" != "${trusted_owner}" ]; then
    echo "ERROR: ${label} runtime owner is not trusted" >&2
    exit 1
  fi
  mode_value=$((8#${mode}))
  if (( (mode_value & 8#022) != 0 )); then
    echo "ERROR: ${label} runtime is group/other writable" >&2
    exit 1
  fi
  actual=$(sha256_file "${path}")
  if [ "${actual}" != "${expected}" ]; then
    echo "ERROR: ${label} runtime SHA-256 mismatch" >&2
    exit 1
  fi
}

select_node_runtime() {
  local platform="$1" linux_system_node="$2" linux_local_node="$3" macos_node="$4" current_uid
  case "${platform}" in
    Linux)
      if [ -x "${linux_system_node}" ]; then
        NODE_BIN="${linux_system_node}"
      elif [ -x "${linux_local_node}" ]; then
        NODE_BIN="${linux_local_node}"
      else
        echo "ERROR: approved Linux Node runtime is unavailable" >&2
        exit 1
      fi
      node_expected="${CODE_REVIEW_NODE_SHA256:-}"
      node_owner=0
      node_uses_built_in_pin=0
      ;;
    Darwin)
      NODE_BIN="${macos_node}"
      node_expected="${MACOS_APPROVED_NODE_SHA256}"
      current_uid=$(clean_exec /usr/bin/id -u)
      node_owner="${current_uid}"
      node_uses_built_in_pin=1
      ;;
    *)
      echo "ERROR: unsupported platform for Node runtime selection" >&2
      exit 1
      ;;
  esac
}

configure_code_review_runtime() {
  local node_expected node_owner node_uses_built_in_pin platform
  MACOS_APPROVED_NODE_SHA256=f28f6fdb2b24cdfc3785f4f8b46fa7b392b0ec5892b075b434049c7d05cfc956
  TEST_MODE="${CODE_REVIEW_TEST_MODE:-0}"
  if [ "${TEST_MODE}" = "1" ]; then
    if [ "${GITLAB_TOKEN}" != "test-token" ]; then
      echo "ERROR: test mode requires the non-production test token" >&2
      exit 1
    fi
    CURL_BIN="${CODE_REVIEW_CURL_BIN:?ERROR: test mode requires CODE_REVIEW_CURL_BIN}"
    NODE_BIN="${CODE_REVIEW_NODE_BIN:?ERROR: test mode requires CODE_REVIEW_NODE_BIN}"
    CA_FILE="${CODE_REVIEW_CA_FILE:?ERROR: test mode requires CODE_REVIEW_CA_FILE}"
    return
  fi
  if [ -n "${CODE_REVIEW_CURL_BIN:-}" ] || [ -n "${CODE_REVIEW_NODE_BIN:-}" ] \
    || [ -n "${CODE_REVIEW_CA_FILE:-}" ]; then
    echo "ERROR: runtime overrides are test-only" >&2
    exit 1
  fi
  CURL_BIN=/usr/bin/curl
  platform=$(clean_exec /usr/bin/uname -s)
  select_node_runtime "${platform}" /usr/bin/node /usr/local/bin/node \
    /Users/mac/.nvm/versions/node/v24.0.0/bin/node
  [ -r /etc/ssl/cert.pem ] && CA_FILE=/etc/ssl/cert.pem \
    || CA_FILE=/etc/ssl/certs/ca-certificates.crt
  if [ ! -x "${CURL_BIN}" ] || [ ! -x "${NODE_BIN}" ] || [ ! -r "${CA_FILE}" ]; then
    echo "ERROR: approved curl, node, or CA file is unavailable" >&2
    exit 1
  fi
  verify_tool_metadata "${CURL_BIN}" http-client "${CODE_REVIEW_CURL_SHA256:-}" 0
  if [ "${node_uses_built_in_pin}" = "1" ] \
    && [ "${CODE_REVIEW_NODE_SHA256:-}" != "${MACOS_APPROVED_NODE_SHA256}" ]; then
    echo "ERROR: platform-approved Node SHA-256 is required" >&2
    exit 1
  fi
  verify_tool_metadata "${NODE_BIN}" node "${node_expected}" "${node_owner}"
}

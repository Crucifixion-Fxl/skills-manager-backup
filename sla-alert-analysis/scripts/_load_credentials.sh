#!/bin/bash
# Shared credential loader for sla-alert-analysis scripts.
# Source this file (not execute) — it populates:
#   SLA_API_TOKEN, SUPERSET_USERNAME, SUPERSET_PASSWORD
#   SLA_API_BASE_URL, SUPERSET_BASE_URL (optional, only set if explicitly provided)
#
# Precedence (first hit wins):
#   1. Already-exported environment variables (CI / ad-hoc override)
#   2. ${A4X_PASSWORD_FILE:-$HOME/.codex/password} — YAML-ish segment `sla-alert-analysis:`
#   3. <skill-root>/.env  — KEY=value lines (portable fallback)

_sla_read_password_file() {
  local key="$1"
  local file="${A4X_PASSWORD_FILE:-$HOME/.codex/password}"
  [ -f "$file" ] || return 0
  awk -v key="$key" '
    /^sla-alert-analysis:[ \t]*$/ { flag=1; next }
    /^[a-zA-Z]/ { flag=0 }
    flag && $0 ~ "^[ \t]+" key ":" {
      sub(/^[ \t]+[^:]+:[ \t]*/, "")
      gsub(/^"|"$/, "")
      print
      exit
    }
  ' "$file"
}

_sla_read_dotenv() {
  local key="$1"
  local file="$2"
  [ -f "$file" ] || return 0
  awk -v key="$key" '
    $0 ~ "^[ \t]*" key "=" {
      sub(/^[ \t]*[^=]+=[ \t]*/, "")
      sub(/^["'\'']/, "")
      sub(/["'\'']$/, "")
      print
      exit
    }
  ' "$file"
}

_sla_load() {
  local var_name="$1"
  local password_key="$2"
  # If already set and non-empty, keep it.
  if [ -n "${!var_name:-}" ]; then
    return 0
  fi
  local value
  value=$(_sla_read_password_file "$password_key")
  if [ -z "$value" ]; then
    value=$(_sla_read_dotenv "$var_name" "$_SLA_DOTENV_PATH")
  fi
  if [ -n "$value" ]; then
    export "$var_name=$value"
  fi
}

# Resolve <skill-root>/.env relative to this loader (scripts/.. = skill root)
_SLA_LOADER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SLA_DOTENV_PATH="${_SLA_LOADER_DIR}/../.env"

_sla_load SLA_API_TOKEN     sla-api-token
_sla_load SUPERSET_USERNAME superset-username
_sla_load SUPERSET_PASSWORD superset-password
_sla_load SLA_API_BASE_URL  sla-api-base-url
_sla_load SUPERSET_BASE_URL superset-base-url

unset -f _sla_read_password_file _sla_read_dotenv _sla_load
unset _SLA_LOADER_DIR _SLA_DOTENV_PATH

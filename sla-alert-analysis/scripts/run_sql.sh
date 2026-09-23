#!/bin/bash
set -euo pipefail

source "$(dirname "$0")/_load_credentials.sh"

SUPERSET_BASE_URL="${SUPERSET_BASE_URL:-https://superset-us.addx.live}"
SUPERSET_USERNAME="${SUPERSET_USERNAME:-}"
SUPERSET_PASSWORD="${SUPERSET_PASSWORD:-}"
SUPERSET_AUTH_PROVIDER="${SUPERSET_AUTH_PROVIDER:-db}"

SQL=""
SQL_FILE=""
DATABASE_ID=""
SCHEMA=""
LIMIT=1000
LIST_DB=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sql) SQL="$2"; shift 2 ;;
    --file) SQL_FILE="$2"; shift 2 ;;
    --db) DATABASE_ID="$2"; shift 2 ;;
    --schema) SCHEMA="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --list-databases) LIST_DB=true; shift ;;
    -h|--help)
      echo "Usage: $0 --sql \"SQL\" [--db ID] [--limit N] [--schema NAME]"
      echo "       $0 --file query.sql [--db ID] [--limit N] [--schema NAME]"
      echo "       $0 --list-databases"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ -z "$SUPERSET_USERNAME" ] || [ -z "$SUPERSET_PASSWORD" ]; then
  echo "Error: SUPERSET_USERNAME and SUPERSET_PASSWORD must be set" >&2
  exit 1
fi

LOGIN_RESPONSE=$(curl -s -X POST "${SUPERSET_BASE_URL%/}/api/v1/security/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"$SUPERSET_USERNAME\",
    \"password\": \"$SUPERSET_PASSWORD\",
    \"provider\": \"$SUPERSET_AUTH_PROVIDER\",
    \"refresh\": true
  }")

ACCESS_TOKEN=$(echo "$LOGIN_RESPONSE" | jq -r '.access_token // empty')
if [ -z "$ACCESS_TOKEN" ]; then
  echo "Error: login failed" >&2
  echo "$LOGIN_RESPONSE" >&2
  exit 1
fi

COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

CSRF_TOKEN=$(curl -s -X GET "${SUPERSET_BASE_URL%/}/api/v1/security/csrf_token/" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -c "$COOKIE_JAR" | jq -r '.result // empty')

if [ -z "$CSRF_TOKEN" ]; then
  echo "Error: failed to get CSRF token" >&2
  exit 1
fi

if [ "$LIST_DB" = true ]; then
  curl -s -X GET "${SUPERSET_BASE_URL%/}/api/v1/database/" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -b "$COOKIE_JAR" | jq '{databases: [.result[] | {id, database_name, backend}]}'
  exit 0
fi

if [ -n "$SQL_FILE" ]; then
  if [ ! -f "$SQL_FILE" ]; then
    echo "Error: file not found: $SQL_FILE" >&2
    exit 1
  fi
  SQL=$(cat "$SQL_FILE")
fi

if [ -z "$SQL" ]; then
  echo "Error: provide SQL with --sql or --file" >&2
  exit 1
fi

if [ -z "$DATABASE_ID" ]; then
  DB_LIST=$(curl -s -X GET "${SUPERSET_BASE_URL%/}/api/v1/database/" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -b "$COOKIE_JAR")
  DATABASE_ID=$(echo "$DB_LIST" | jq -r '.result[0].id // empty')
  if [ -z "$DATABASE_ID" ]; then
    echo "Error: failed to detect database_id, use --db" >&2
    exit 1
  fi
fi

REQUEST_BODY=$(jq -n \
  --argjson db_id "$DATABASE_ID" \
  --arg sql "$SQL" \
  --argjson limit "$LIMIT" \
  '{
    database_id: $db_id,
    sql: $sql,
    queryLimit: $limit,
    runAsync: false,
    expand_data: true,
    select_as_cta: false,
    ctas_method: "TABLE",
    json: true
  }')

if [ -n "$SCHEMA" ]; then
  REQUEST_BODY=$(echo "$REQUEST_BODY" | jq --arg schema "$SCHEMA" '. + {schema: $schema}')
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${SUPERSET_BASE_URL%/}/api/v1/sqllab/execute/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -H "Referer: $SUPERSET_BASE_URL" \
  -b "$COOKIE_JAR" \
  --max-time 120 \
  -d "$REQUEST_BODY")

HTTP_CODE=$(printf '%s\n' "$RESPONSE" | awk 'END{print}')
BODY_RESPONSE=$(printf '%s\n' "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 400 ]; then
  echo "Error: HTTP $HTTP_CODE" >&2
  echo "$BODY_RESPONSE" >&2
  exit 1
fi

echo "$BODY_RESPONSE" | jq '{
  status: .status,
  query_id: .query_id,
  rows: (.data | length),
  columns: [.columns[] | .name],
  data: .data
}' 2>/dev/null || echo "$BODY_RESPONSE"

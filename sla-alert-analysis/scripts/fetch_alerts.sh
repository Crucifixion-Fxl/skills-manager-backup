#!/bin/bash
set -euo pipefail

source "$(dirname "$0")/_load_credentials.sh"

SLA_API_BASE_URL="${SLA_API_BASE_URL:-https://dapp-api.addx.live}"
SLA_API_TOKEN="${SLA_API_TOKEN:-}"

METRIC_NAME=""
SINCE_AT=""
BEFORE_AT=""
FIRING=1
PAGE_SIZE=100
PAGE_INDEX=0
ALL_STATUS=false
COMPACT=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --metric) METRIC_NAME="$2"; shift 2 ;;
    --since) SINCE_AT="$2"; shift 2 ;;
    --before) BEFORE_AT="$2"; shift 2 ;;
    --all) ALL_STATUS=true; shift ;;
    --compact) COMPACT=true; shift ;;
    --page-size) PAGE_SIZE="$2"; shift 2 ;;
    --page) PAGE_INDEX="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--metric NAME] [--since TIMESTAMP] [--before TIMESTAMP] [--all] [--compact] [--page-size N] [--page N]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ -z "$SLA_API_TOKEN" ]; then
  echo "Error: SLA_API_TOKEN is not set" >&2
  exit 1
fi

BODY=$(
  jq -n \
    --argjson page_size "$PAGE_SIZE" \
    --argjson page_index "$PAGE_INDEX" \
    --arg metric_name "$METRIC_NAME" \
    --argjson since_at "${SINCE_AT:-0}" \
    --argjson before_at "${BEFORE_AT:-0}" \
    --argjson firing "$FIRING" \
    --argjson all_status "$ALL_STATUS" \
    '
    {
      page_size: $page_size,
      page_index: $page_index
    }
    + (if $all_status then {} else {firing: $firing} end)
    + (if $metric_name == "" then {} else {metric_name: $metric_name} end)
    + (if $since_at == 0 then {} else {since_at: $since_at} end)
    + (if $before_at == 0 then {} else {before_at: $before_at} end)
    '
)

URL="${SLA_API_BASE_URL%/}/api/v1/sla_metric/events"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SLA_API_TOKEN" \
  -d "$BODY")

HTTP_CODE=$(printf '%s\n' "$RESPONSE" | awk 'END{print}')
BODY_RESPONSE=$(printf '%s\n' "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 400 ]; then
  echo "Error: HTTP $HTTP_CODE" >&2
  echo "$BODY_RESPONSE" >&2
  exit 1
fi

if [ "$COMPACT" = true ]; then
  echo "$BODY_RESPONSE" | jq '{
    success: .success,
    result: {
      data: [.result.data[] | {
        id,
        metric_name,
        metric_at,
        superset_metric_value,
        superset_metric_key,
        firing,
        biz_domain_id,
        app_domain_id,
        created_at
      }],
      page: .result.page
    }
  }' 2>/dev/null || echo "$BODY_RESPONSE"
else
  echo "$BODY_RESPONSE"
fi

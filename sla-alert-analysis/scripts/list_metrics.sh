#!/bin/bash
set -euo pipefail

source "$(dirname "$0")/_load_credentials.sh"

SLA_API_BASE_URL="${SLA_API_BASE_URL:-https://dapp-api.addx.live}"
SLA_API_TOKEN="${SLA_API_TOKEN:-}"

NAME=""
TITLE=""
TIME_GRAIN=""
BIZ_DOMAIN_ID=""
APP_DOMAIN_ID=""
CREATED_AT=""
UPDATED_AT=""
PAGE_SIZE=100
PAGE_INDEX=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --time-grain) TIME_GRAIN="$2"; shift 2 ;;
    --biz-domain) BIZ_DOMAIN_ID="$2"; shift 2 ;;
    --app-domain) APP_DOMAIN_ID="$2"; shift 2 ;;
    --created) CREATED_AT="$2"; shift 2 ;;
    --updated) UPDATED_AT="$2"; shift 2 ;;
    --page-size) PAGE_SIZE="$2"; shift 2 ;;
    --page) PAGE_INDEX="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--name NAME] [--title TITLE] [--time-grain GRAIN] [--biz-domain ID] [--app-domain ID] [--created ENUM] [--updated ENUM] [--page-size N] [--page N]"
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
    --arg name "$NAME" \
    --arg title "$TITLE" \
    --arg time_grain "$TIME_GRAIN" \
    --arg created_at "$CREATED_AT" \
    --arg updated_at "$UPDATED_AT" \
    --argjson biz_domain_id "${BIZ_DOMAIN_ID:-0}" \
    --argjson app_domain_id "${APP_DOMAIN_ID:-0}" \
    '
    {
      page_size: $page_size,
      page_index: $page_index
    }
    + (if $name == "" then {} else {name: $name} end)
    + (if $title == "" then {} else {tittle: $title} end)
    + (if $time_grain == "" then {} else {time_grain: $time_grain} end)
    + (if $biz_domain_id == 0 then {} else {biz_domain_id: $biz_domain_id} end)
    + (if $app_domain_id == 0 then {} else {app_domain_id: $app_domain_id} end)
    + (if $created_at == "" then {} else {created_at: $created_at} end)
    + (if $updated_at == "" then {} else {updated_at: $updated_at} end)
    '
)

URL="${SLA_API_BASE_URL%/}/api/v1/sla_metric/list"
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

echo "$BODY_RESPONSE"

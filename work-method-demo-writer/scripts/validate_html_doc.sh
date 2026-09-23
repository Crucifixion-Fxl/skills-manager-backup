#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <html-file>" >&2
  exit 2
fi

html_file="$1"

if [ ! -f "$html_file" ]; then
  echo "HTML file not found: $html_file" >&2
  exit 1
fi

python3 -m html.parser "$html_file"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check -- "$html_file"
fi

if grep -nE "TODO|lorem ipsum|Lorem ipsum|repo \\$" "$html_file"; then
  echo "Found placeholder or confusing demo text in $html_file" >&2
  exit 1
fi

for required_meta in \
  "name=\"description\"" \
  "property=\"og:title\"" \
  "property=\"og:description\"" \
  "property=\"og:image\"" \
  "name=\"twitter:card\"" \
  "name=\"twitter:image\""; do
  if ! grep -q "$required_meta" "$html_file"; then
    echo "Missing share-card meta: $required_meta" >&2
    exit 1
  fi
done

echo "HTML work-method doc validation passed: $html_file"

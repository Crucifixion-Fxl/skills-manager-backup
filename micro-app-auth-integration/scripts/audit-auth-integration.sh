#!/usr/bin/env bash
set -euo pipefail
script_source=${BASH_SOURCE[0]}
if [ -L "$script_source" ]; then
  printf 'ERROR: refusing to run through a symbolic link: %s\n' "$script_source" >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "$script_source")" && pwd -P)
helper=$script_dir/audit_inventory.py
if [ ! -f "$helper" ] || [ -L "$helper" ]; then
  printf '%s\n' 'ERROR: trusted regular audit_inventory.py helper is required.' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'ERROR: python3 is required.' >&2
  exit 2
fi
exec python3 -I "$helper" "$@"

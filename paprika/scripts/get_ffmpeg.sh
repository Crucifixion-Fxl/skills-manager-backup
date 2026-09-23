#!/usr/bin/env bash
# get_ffmpeg.sh - print (as JSON) the path of an ffmpeg that has the arnndn (RNNoise) filter.
#
# Lookup order: $FFMPEG -> system ffmpeg with arnndn -> cached static build.
# The cached build (Linux x86_64 only) is the static ffmpeg bundled in the pinned imageio-ffmpeg wheel from
# PyPI, stored in ${XDG_CACHE_HOME:-$HOME/.cache}/paprika/bin/ (no sudo/pip). The wheel and the extracted
# binary are both sha256-pinned; the cached binary is re-verified (hash + arnndn) on EVERY use, and a
# failing cache is deleted and downloaded again. Elsewhere, install ffmpeg and set FFMPEG=/path/to/ffmpeg.
#
# usage: get_ffmpeg.sh [--help]
set -euo pipefail

case "${1:-}" in
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "usage: get_ffmpeg.sh [--help]" >&2; exit 2 ;;
esac

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/paprika"
OUT="$CACHE/bin/ffmpeg"
WHEEL_VERSION="0.6.0"
WHEEL_SHA256="c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282"
BINARY_SHA256="e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99"

sha256_of() {  # GNU coreutils or BSD/macOS
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d" " -f1; else shasum -a 256 "$1" | cut -d" " -f1; fi
}

emit() {
  python3 -c 'import json,sys; print(json.dumps({"ffmpeg": sys.argv[1], "source": sys.argv[2]}))' "$1" "$2"
}

has_arnndn() {
  local filters
  filters="$("$1" -hide_banner -filters 2>/dev/null || true)"
  [[ "$filters" == *" arnndn "* ]]
}

if [ -n "${FFMPEG:-}" ]; then
  has_arnndn "$FFMPEG" || { echo "error: \$FFMPEG lacks the arnndn filter" >&2; exit 1; }
  emit "$FFMPEG" env; exit 0
fi
if command -v ffmpeg >/dev/null 2>&1 && has_arnndn "$(command -v ffmpeg)"; then
  emit "$(command -v ffmpeg)" system; exit 0
fi

if [ -e "$OUT" ]; then
  if [ -x "$OUT" ] && [ "$(sha256_of "$OUT")" = "$BINARY_SHA256" ] && has_arnndn "$OUT"; then
    emit "$OUT" cache; exit 0
  fi
  echo "cached ffmpeg failed verification; removing it" >&2
  rm -f "$OUT"
fi

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
  echo "error: no ffmpeg with the arnndn filter found; install ffmpeg and set FFMPEG=/path/to/ffmpeg" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$CACHE/bin"
URL="$(curl -fsS -m 30 "https://pypi.org/pypi/imageio-ffmpeg/${WHEEL_VERSION}/json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(next(f["url"] for f in d["urls"] if "linux" in f["filename"] and "x86_64" in f["filename"]))')"
curl -fsS -m 300 -o "$TMP/w.whl" "$URL"
ACTUAL="$(sha256_of "$TMP/w.whl")"
[ "$ACTUAL" = "$WHEEL_SHA256" ] || { echo "error: wheel sha256 mismatch" >&2; exit 1; }
(cd "$TMP" && unzip -q w.whl 'imageio_ffmpeg/binaries/*')
cp "$TMP"/imageio_ffmpeg/binaries/ffmpeg-* "$TMP/ffmpeg.new"
[ "$(sha256_of "$TMP/ffmpeg.new")" = "$BINARY_SHA256" ] || { echo "error: ffmpeg binary sha256 mismatch" >&2; exit 1; }
chmod 755 "$TMP/ffmpeg.new"
mv "$TMP/ffmpeg.new" "$OUT"
emit "$OUT" downloaded

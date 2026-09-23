#!/usr/bin/env bash
# prep_voice.sh - turn a raw recording into a clean voice reference for Paprika REFERENCE_AUDIO.
#
# usage: prep_voice.sh INPUT.(mp4|mov|wav|mp3) OUTPUT.wav [--help]
#
# Steps: extract mono 44.1 kHz -> RNNoise denoise (ffmpeg arnndn) -> LINEAR gain to a -3 dB peak.
# Why not afftdn / loudnorm: afftdn barely changed a fan-noise recording (SNR 14.4 -> 14.2 dB) while
# arnndn reached ~32 dB; loudnorm's dynamic mode lifts the noise in pauses, so a fixed gain is used.
# The RNNoise model is cached in ${XDG_CACHE_HOME:-$HOME/.cache}/paprika/ and sha256-verified on every use.
# Prints one JSON object: {"output", "peak_db", "gain_db"}.
set -euo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
if [ $# -ne 2 ]; then usage >&2; exit 2; fi
IN="$1"; OUT="$2"
[ -f "$IN" ] || { echo "error: input not found: $IN" >&2; exit 1; }

DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/paprika"
MODEL="$CACHE/sh.rnnn"
MODEL_URL="https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn"
MODEL_SHA256="70bb6685eb0c2a1d18e2918dca3fbfbd39317010b1802eb1b6ea73a92f3fdec0"

sha256_of() {  # GNU coreutils or BSD/macOS
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d" " -f1; else shasum -a 256 "$1" | cut -d" " -f1; fi
}

FF="$("$DIR/get_ffmpeg.sh" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ffmpeg"])')"

# re-verify the cached model on EVERY use; a missing or tampered file is deleted and downloaded again
if [ ! -f "$MODEL" ] || [ "$(sha256_of "$MODEL")" != "$MODEL_SHA256" ]; then
  rm -f "$MODEL"
  mkdir -p "$CACHE"
  curl -fsSL -m 60 -o "$MODEL.part" "$MODEL_URL"
  ACTUAL="$(sha256_of "$MODEL.part")"
  [ "$ACTUAL" = "$MODEL_SHA256" ] || { rm -f "$MODEL.part"; echo "error: model sha256 mismatch" >&2; exit 1; }
  mv "$MODEL.part" "$MODEL"
fi

TMP="$(mktemp "${TMPDIR:-/tmp}/prep_voice.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
# -map_metadata -1: drop the input's tags, they are untrusted data
"$FF" -v error -y -i "$IN" -vn -ac 1 -ar 44100 -map_metadata -1 -af "arnndn=m=$MODEL" -c:a pcm_s16le -f wav "$TMP"
MAXV="$("$FF" -hide_banner -i "$TMP" -af volumedetect -f null - 2>&1 | sed -n 's/^.*max_volume: \(-\{0,1\}[0-9.]*\) dB$/\1/p' | tail -n 1)"
[[ "$MAXV" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] || { echo "error: could not measure peak level" >&2; exit 1; }
# the measured value is passed as argv and parsed with float(), never spliced into code
GAIN="$(python3 -c 'import sys; print(round(-3.0 - float(sys.argv[1]), 2))' "$MAXV")"
"$FF" -v error -y -i "$TMP" -af "volume=${GAIN}dB" -ar 44100 -ac 1 -c:a pcm_s16le -f wav "$OUT"

python3 - "$OUT" "$MAXV" "$GAIN" <<'PY'
import json, sys
print(json.dumps({"output": sys.argv[1], "peak_db": float(sys.argv[2]), "gain_db": float(sys.argv[3])}))
PY

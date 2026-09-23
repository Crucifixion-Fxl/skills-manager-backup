#!/usr/bin/env bash
# Install harness-failover to a stable path and (re)create the systemd --user timer.
#
#   scripts/install.sh                 copy + write/enable the timer
#   scripts/install.sh --print-units   print the unit files only (touches nothing)
#
# Why a copy: the plugin cache path changes on every plugin update, so a timer must not point into it.
# Env: HARNESS_FAILOVER_DEST (default ~/.local/lib/buzz-agents/harness-failover), HARNESS_FAILOVER_NO_SYSTEMD=1
set -euo pipefail
umask 022  # the timer runs this code every 5 minutes: never install it group/world-writable

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HARNESS_FAILOVER_DEST:-$HOME/.local/lib/buzz-agents/harness-failover}"
UNITDIR="$HOME/.config/systemd/user"

refuse() { echo "refusing: $1" >&2; exit 1; }
# Absolute, plain characters only (it is interpolated into a systemd unit), and never / or $HOME itself.
[[ "$DEST" =~ ^/[A-Za-z0-9._/+-]+$ ]] || refuse "HARNESS_FAILOVER_DEST must be an absolute path of [A-Za-z0-9._/+-]: '$DEST'"
[ "$DEST" != "$HOME" ] && [ "$DEST" != "${HOME%/}" ] || refuse "destination must not be \$HOME"

service_unit() {
  cat <<UNIT
[Unit]
Description=Detect an exhausted agent harness and fail all Buzz agents over together

[Service]
Type=oneshot
UMask=0022
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 $DEST/harness-failover switch --to auto --apply --probe --retry
TimeoutStartSec=30min
# exit 2 = current harness exhausted and no usable fallback: shows up in \`systemctl --user --failed\`
UNIT
}

timer_unit() {
  cat <<UNIT
[Unit]
Description=Every 5 minutes: check harness quota (history only) and fail over

[Timer]
OnCalendar=*:0/5
Persistent=true
RandomizedDelaySec=20

[Install]
WantedBy=timers.target
UNIT
}

if [ "${1:-}" = "--print-units" ]; then
  echo "# ── $UNITDIR/harness-failover.service"; service_unit
  echo; echo "# ── $UNITDIR/harness-failover.timer"; timer_unit
  exit 0
fi

mkdir -p "$DEST"
rm -rf "$DEST/harness_failover" "$DEST/assets"
cp -r "$SRC/harness_failover" "$DEST/harness_failover"
cp -r "$SRC/../assets" "$DEST/assets"
cp "$SRC/harness-failover" "$DEST/harness-failover"
find "$DEST" -name '__pycache__' -prune -exec rm -rf {} +
chmod -R go-w "$DEST"
chmod 755 "$DEST/harness-failover"

if [ "${HARNESS_FAILOVER_NO_SYSTEMD:-}" = "1" ]; then
  echo "installed to $DEST (systemd units skipped)"
  exit 0
fi

[[ "$HOME" =~ ^/[A-Za-z0-9._/+-]+$ ]] || refuse "\$HOME contains characters unsafe for a systemd unit path"
mkdir -p "$UNITDIR"
service_unit > "$UNITDIR/harness-failover.service"
timer_unit > "$UNITDIR/harness-failover.timer"
systemctl --user daemon-reload
systemctl --user enable --now harness-failover.timer
echo "installed to $DEST; timer enabled:"
systemctl --user list-timers harness-failover.timer --no-legend

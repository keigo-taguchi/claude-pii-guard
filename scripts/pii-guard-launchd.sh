#!/usr/bin/env bash
# Install / remove the pii-guard daemon as a per-user LaunchAgent.
#   scripts/pii-guard-launchd.sh install
#   scripts/pii-guard-launchd.sh uninstall
#   scripts/pii-guard-launchd.sh status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST=~/Library/LaunchAgents/com.pii-guard.plist
UV="$(command -v uv)"
case "${1:-status}" in
  install)
    mkdir -p ~/Library/LaunchAgents ~/.config/safe-data
    sed -e "s#__UV__#$UV#g" -e "s#__REPO__#$ROOT#g" -e "s#__HOME__#$HOME#g" "$ROOT/launchd/com.pii-guard.plist" > "$PLIST"
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    sleep 1
    curl -s http://127.0.0.1:8787/healthz || { echo "daemon not answering; see ~/.config/safe-data/pii-guard.log"; exit 1; }
    echo
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed"
    ;;
  status)
    launchctl print "gui/$(id -u)/com.pii-guard" 2>/dev/null | grep -E "state|pid" || echo "not loaded"
    curl -s http://127.0.0.1:8787/healthz || echo "daemon not answering"
    echo
    ;;
  *) echo "usage: $0 install|uninstall|status"; exit 2 ;;
esac

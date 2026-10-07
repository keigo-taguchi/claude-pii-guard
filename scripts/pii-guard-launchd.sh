#!/usr/bin/env bash
# Install / remove the pii-guard daemon as a per-user LaunchAgent.
#   scripts/pii-guard-launchd.sh install
#   scripts/pii-guard-launchd.sh uninstall
#   scripts/pii-guard-launchd.sh status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST=~/Library/LaunchAgents/com.pii-guard.plist
SCAN=~/Library/LaunchAgents/com.pii-guard.scan.plist
UV="$(command -v uv)"
case "${1:-status}" in
  install)
    mkdir -p ~/Library/LaunchAgents ~/.config/safe-data
    sed -e "s#__UV__#$UV#g" -e "s#__REPO__#$ROOT#g" -e "s#__HOME__#$HOME#g" "$ROOT/launchd/com.pii-guard.plist" > "$PLIST"
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    sed -e "s#__UV__#$UV#g" -e "s#__REPO__#$ROOT#g" -e "s#__HOME__#$HOME#g" "$ROOT/launchd/com.pii-guard.scan.plist" > "$SCAN"
    launchctl bootout "gui/$(id -u)" "$SCAN" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$SCAN"
    sleep 1
    curl -s http://127.0.0.1:8787/healthz || { echo "daemon not answering; see ~/.config/safe-data/pii-guard.log"; exit 1; }
    echo
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)" "$SCAN" 2>/dev/null || true
    rm -f "$PLIST" "$SCAN"
    echo "removed"
    ;;
  status)
    launchctl print "gui/$(id -u)/com.pii-guard" 2>/dev/null | grep -E "state|pid" || echo "daemon: not loaded"
    launchctl print "gui/$(id -u)/com.pii-guard.scan" 2>/dev/null | grep -E "state" || echo "nightly scan: not loaded"
    tail -n 3 ~/.config/safe-data/scan.log 2>/dev/null || true
    curl -s http://127.0.0.1:8787/healthz || echo "daemon not answering"
    echo
    ;;
  *) echo "usage: $0 install|uninstall|status"; exit 2 ;;
esac

#!/usr/bin/env bash
# Remove everything install.sh and the Phase 0 settings added, and restore the
# settings.json backup. Asks before each step. Never touches ~/PII contents,
# the database, or claude.ai connectors (those are listed at the end).
#   scripts/uninstall.sh          # interactive
#   scripts/uninstall.sh --yes    # no prompts
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YES=0; [ "${1:-}" = "--yes" ] && YES=1
ask() { [ $YES = 1 ] && return 0; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]; }

echo "== 1. launchd (daemon + nightly scan)"
if ls ~/Library/LaunchAgents/com.pii-guard*.plist >/dev/null 2>&1; then
  ask "remove launchd jobs?" && "$ROOT/scripts/pii-guard-launchd.sh" uninstall
else echo "   none"; fi

echo "== 2. MCP registration (safe-data, user scope)"
if claude mcp get safe-data >/dev/null 2>&1; then
  ask "claude mcp remove safe-data?" && claude mcp remove --scope user safe-data
else echo "   not registered"; fi

echo "== 3. Skill link ~/.claude/skills/safe-analysis"
if [ -L ~/.claude/skills/safe-analysis ]; then
  ask "remove the symlink?" && rm ~/.claude/skills/safe-analysis && echo "   removed"
else echo "   none"; fi

echo "== 4. ~/.claude/settings.json"
latest=$(ls -t ~/.claude/settings.json.bak-* 2>/dev/null | head -1 || true)
if [ -n "$latest" ]; then
  echo "   newest backup: $latest"
  if ask "restore it over ~/.claude/settings.json? (keeps a copy of the current file as settings.json.pre-uninstall)"; then
    cp ~/.claude/settings.json ~/.claude/settings.json.pre-uninstall
    cp "$latest" ~/.claude/settings.json
    echo "   restored. If you merged more than once, pick an older settings.json.bak-* yourself."
  fi
else
  echo "   no backup found; remove the pii-guard keys by hand if merge_settings.py was never used:"
  echo "   permissions.deny entries, sandbox, enableArtifact, autoMemoryDirectory, cleanupPeriodDays, env.*"
fi
if grep -q CLAUDE_CODE_PLUGIN_DIRS ~/.claude/settings.json 2>/dev/null; then
  echo "   note: CLAUDE_CODE_PLUGIN_DIRS is still set in settings.json (the mod loads from it)"
fi

echo "== 5. ~/.config/safe-data (config, pseudo key, vault, dictionary, logs)"
if [ -d ~/.config/safe-data ]; then
  echo "   the pseudo key is needed to map existing pseudo IDs back; the vault holds the placeholder table."
  ask "delete ~/.config/safe-data entirely?" && rm -rf ~/.config/safe-data && echo "   deleted"
else echo "   none"; fi

echo "== 6. Docker image safe-data-runner"
if command docker image inspect safe-data-runner:latest >/dev/null 2>&1; then
  ask "docker rmi safe-data-runner:latest?" && docker rmi -f safe-data-runner:latest >/dev/null && echo "   removed"
else echo "   none"; fi

echo "== 7. auto memory"
if [ -d ~/claude-memory ]; then
  echo "   ~/claude-memory exists (autoMemoryDirectory). Settings restore points memory back to ~/.claude/projects/<project>/memory/."
  echo "   copy anything you want to keep from ~/claude-memory/MEMORY.md by hand; the directory is left in place."
fi

cat <<'EOF'

Left in place on purpose:
  - ~/PII/inbox/           your files (Claude never read them). Move them, then rmdir.
  - the database           drop manually if you created the views:
                           DROP SCHEMA claude CASCADE; DROP SCHEMA keys CASCADE; DROP ROLE claude_ro;
  - claude.ai connectors   reconnect Gmail / Drive / Calendar / Docs / Slack at claude.ai/customize/connectors
  - model                  /model to pick the model you used before
  - this repository        rm -rf the checkout when you no longer need it
EOF

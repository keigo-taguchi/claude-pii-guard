#!/usr/bin/env bash
# Install safe-data for the current user. Idempotent. Prints what it does.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== 1. Python env"
uv sync --quiet

echo "== 2. Config + pseudonym key"
mkdir -p ~/.config/safe-data
[ -f ~/.config/safe-data/config.toml ] || cp config.example.toml ~/.config/safe-data/config.toml
uv run safe-data-mcp --init-key
chmod 700 ~/.config/safe-data

echo "== 3. PII inbox (Claude cannot read this directory after Phase 0 settings)"
mkdir -p ~/PII/inbox && chmod 700 ~/PII

echo "== 4. Register the MCP server for all projects (user scope)"
if claude mcp get safe-data >/dev/null 2>&1; then
  echo "   safe-data already registered"
else
  claude mcp add --scope user safe-data -- uv --directory "$ROOT" run safe-data-mcp
fi

echo "== 5. Skill"
mkdir -p ~/.claude/skills
ln -sfn "$ROOT/skills/safe-analysis" ~/.claude/skills/safe-analysis

echo "== 6. Runner image (needs Docker/colima running)"
if command docker info >/dev/null 2>&1; then
  docker build -q -t safe-data-runner:latest docker/runner && echo "   built safe-data-runner:latest"
else
  echo "   docker not running; later: docker build -t safe-data-runner docker/runner"
fi

echo "== 7. pii-guard daemon (mod + nightly scan)"
if [ -f ~/.config/safe-data/dictionary.json ]; then
  scripts/pii-guard-launchd.sh install >/dev/null && echo "   daemon + nightly scan registered (launchd)"
else
  echo "   no dictionary yet; build one, then: scripts/pii-guard-launchd.sh install"
  echo "     uv run pii-guard-dict --from-csv <customers.csv> --id user_id --name name --kana kana --email mail --phone tel"
fi
echo "   load the mod in every session: add to ~/.claude/settings.json ->"
echo "     \"env\": { \"CLAUDE_CODE_PLUGIN_DIRS\": \"$ROOT/mods/pii-guard\" }"

cat <<EOF

Next:
  1. /mcp in Claude Code -> copy connector server names into settings/connectors.local.json
  2. python3 settings/merge_settings.py            (dry run)
     python3 settings/merge_settings.py --apply    (writes ~/.claude/settings.json, keeps a backup)
  3. Restart Claude Code, then run scripts/phase0_acceptance.md
  4. DB: see db/README.md
EOF

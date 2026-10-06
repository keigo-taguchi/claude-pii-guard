#!/usr/bin/env bash
# Day 0: record the environment and plant canary data. Nothing here touches real data.
set -uo pipefail
OUT="${1:-day0-$(date +%Y%m%d).txt}"
{
  echo "# Day 0 record  $(date -Iseconds)"
  echo; echo "## claude --version"; claude --version 2>&1
  echo; echo "## claude mcp list"; claude mcp list 2>&1
  echo; echo "## settings sources"; ls -la ~/.claude/settings.json ~/.claude/settings.local.json /Library/Application\ Support/ClaudeCode/managed-settings.json 2>&1
  echo; echo "## docker"; command docker info --format '{{.ServerVersion}}' 2>&1 | head -1
  echo; echo "## uv / python"; uv --version; python3 --version
  echo; echo "## sandbox deps"; which sandbox-exec 2>&1
} | tee "$OUT"

echo
echo "## canary files (synthetic; safe to keep)"
mkdir -p ~/PII/inbox
cat > ~/PII/inbox/canary_customers.csv <<'EOF'
user_id,name,kana,mail,tel,birth_date,plan,amount
900001,田中カナリア,タナカカナリア,canary.tanaka@example.invalid,090-0000-0001,1980-01-02,pro,120
900002,髙橋カナリア,タカハシカナリア,canary.takahashi@example.invalid,090-0000-0002,1991-03-04,free,30
900003,山田カナリア,ヤマダカナリア,canary.yamada@example.invalid,090-0000-0003,2001-05-06,pro,90
EOF
echo "wrote ~/PII/inbox/canary_customers.csv"
echo
echo "Record in Claude Code (type these yourself):  /status   /model   /mcp"
echo "Then follow scripts/phase0_acceptance.md"

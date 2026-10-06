# DB 側の設定（第一防衛線）

safe-data は `claude_ro` ロールで `claude` スキーマのビューにしか繋がない。個人情報列はビューに**存在しない**ので、検出精度に関係なく漏れない。

## 手順

```bash
# 1. 擬似 ID の鍵を作る（0600・1 行の hex）
uv run safe-data-mcp --init-key
cat ~/.config/safe-data/pseudo.key

# 2. DB 側（所有者権限で）
psql -d <db> -f db/01_roles.sql
psql -d <db> -f db/02_pseudo.sql
psql -d <db> -c "INSERT INTO keys.pseudo VALUES ('<pseudo.key の中身>');"
psql -d <db> -f db/03_views.sql      # 列名は自分のスキーマに合わせて編集

# 3. claude_ro の接続情報を pg_service.conf に置く（環境変数には置かない）
cat >> ~/.pg_service.conf <<'EOF'
[claude_ro]
host=<db-host>
port=5432
dbname=<db>
user=claude_ro
EOF
# パスワードは ~/.pgpass（0600）に: <db-host>:5432:<db>:claude_ro:<password>

# 4. 動作確認
PGSERVICE=claude_ro psql -c "select * from claude.users_safe limit 1"   # 読める
PGSERVICE=claude_ro psql -c "select * from public.users limit 1"        # permission denied になること
```

## 確認事項

- `02_pseudo.sql` の `public.hmac` は pgcrypto のインストール先スキーマに合わせる（`extensions.hmac` の場合は `search_path` にも追加）。
- `~/.pg_service.conf` と `~/.pgpass` は Phase 0 の設定で sandbox の `denyRead` に入っている。safe-data はサンドボックス外（MCP プロセス）で動くので読める。
- PostgreSQL 以外（MongoDB / MySQL / BigQuery）は、ETL で安全化レプリカ（SQLite/DuckDB）を作り safe-data はレプリカだけに繋ぐ方式にする。

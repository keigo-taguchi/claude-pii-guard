# claude-pii-guard

Keep raw personal data out of the Anthropic API while keeping Claude Code's Auto mode hands-off. Instead of detecting and masking on the wire, this repo (1) denies Claude Code access to where personal data lives, and (2) runs the work that needs that data locally through a compute-to-data MCP server (`safe-data`) that returns only aggregates, schemas, and pseudonymized results. Docs are in Japanese; the code and config are generic.

Claude Code の Auto モードをそのままに、**生の個人情報が Anthropic API に届かない**構成を作るためのツール群。骨格は「検出して消す」ではなく「Claude が到達できる場所に生データを置かない」+「個人情報が要る処理は手元で実行して結果だけ返す」。

- 全体像: [docs/plan.html](docs/plan.html)
- 設計書（根拠 URL・未確認事項つき）: [docs/design.md](docs/design.md)

## 構成

| 層 | 成果物 | 強制力 |
|---|---|---|
| 届く経路を限定する / 読ませない（P0） | `settings/phase0.settings.json` + `merge_settings.py` | あり（deny / sandbox） |
| 見せずに処理する（P1） | `src/safe_data/` — MCP サーバー `safe-data` | あり（唯一の口） |
| ソース側で断つ（P1） | `db/*.sql`（ビュー + 専用ロール）、`safeify-csv`（ETL） | あり |
| 使い方を教える | `skills/safe-analysis/SKILL.md`、`settings/CLAUDE.snippet.md` | なし（体験のため） |
| Phase 2（未実装・設計済み） | pii-guard **Mod** + ローカルデーモン、support-intake / slack-safe — [docs/phase2-mod-design.md](docs/phase2-mod-design.md) | あり（fail-closed） |

## safe-data のツール

| ツール | 返すもの | 返さないもの |
|---|---|---|
| `schema_describe(view?)` | `claude` スキーマのビューの列 + 合成サンプル | 実データ |
| `files_describe()` | `~/PII/inbox` のファイル id・推定スキーマ・PII フラグ | 値、ファイル名 |
| `sql_run(sql, max_rows)` | 1 文の SELECT の結果（行数上限・n<11 抑止） | `SELECT *`、書込み、設定読取 |
| `py_run(script, inputs)` | ネットワーク無しコンテナで実行した結果 JSON（8KB） | 保護対象の値を含む結果、print 出力 |
| `fixture_make(source, n)` | ビュー/ファイルと同じ形の合成行 | — |

## セットアップ

```bash
scripts/install.sh            # uv sync、鍵生成、inbox 作成、claude mcp add、Skill リンク、runner イメージ
scripts/day0.sh               # 環境の記録とカナリア作成
python3 settings/merge_settings.py          # dry run
python3 settings/merge_settings.py --apply  # ~/.claude/settings.json に合流（バックアップあり）
```

その後 `scripts/phase0_acceptance.md` を通し、DB は `db/README.md`。

## 開発

```bash
uv sync
uv run pytest -q
uv run safe-data-mcp --check   # 実効設定
```

## 自分の環境に合わせる

- `settings/phase0.settings.json` の deny には Claude Desktop 内蔵ツール（`mcp__computer-use`、`mcp__Claude_Browser__*`、`mcp__ccd_*`）や GitKraken / AWS プラグインのツール名が入っている。存在しないツールへの deny は何にも一致しないだけで害はない。自分の環境の名前は `/mcp` で確認し、`settings/connectors.local.json` に写す。
- `src/safe_data/config.py` の PII 列パターンは日本語のサポート業務向け。自分のスキーマに合わせて `config.toml` の `pii_columns.patterns` で上書きする。
- `db/03_views.sql` は例。列名を自分のテーブルに合わせる。
- `docs/design.md` は筆者環境を前提にした設計記録（Skill 名・ドメインは例）。

## 注意（colima / Docker Desktop）

`py_run` はホストのファイルをコンテナに bind mount する。VM 型の Docker（colima）はホームディレクトリしか VM に共有していないので、`pii_inbox` と `analysis_dir` は **`$HOME` 配下**に置く（`/tmp` 配下は空ディレクトリとしてマウントされ、スクリプトが読めない）。safe-data は `--mount type=bind` を使うので、共有されていないパスは明示的なエラーになる。

## 設計上の約束

- ツールはエラーを `isError` ではなく `{ok:false, error}` で返す（失敗出力が文脈に流れない）
- 戻り値は 40,000 字未満（Claude Code の 50,000 字ディスク退避を発生させない）
- 理由文に値を書かない（種類と件数だけ）
- hook は fail-open なので主役にしない。この層（deny / sandbox / MCP / DB）が主役

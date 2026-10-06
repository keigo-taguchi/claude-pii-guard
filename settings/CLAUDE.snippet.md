# データの扱い（CLAUDE.md に貼る 4 行）

- 個人情報を含むデータ（DB・CSV・ログ・問い合わせ）は直接読まず、`safe-data` の MCP ツール（schema_describe / files_describe / sql_run / py_run）経由で集計・擬似化された結果だけを使う。
- 人は `user_pseudo_id` などの擬似 ID で指す。実名・メール・電話・患者 ID をコマンド引数や SQL に書かない。
- `[P12_NAME]` のような札は値を推測・復元しない。そのまま扱う。
- `~/PII/` や `./data/raw/` が読めないのは仕様。迂回せず、safe-data を使うか人に頼む。

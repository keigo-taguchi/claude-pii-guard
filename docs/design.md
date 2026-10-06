> **注記**: この文書は筆者の環境（macOS・Claude Desktop・医療系ソフトウェア企業のサポート業務）を前提に 2026-10-05 時点の公式ドキュメントを調査して書いた設計記録です。ツール名・ドメイン・Skill 名は例で、読者の環境に合わせて読み替えてください。「未確認」と付けた箇所は実測が必要です。

<!--
生成: 2026-10-05 / 24エージェントのワークフロー（調査5・設計4・批評12・統合3）の最終出力。
本文中の「未確認」表記はそのまま残している（30件）。以下の4点は Claude 本体が公式ドキュメントで直接再確認済み:
 - Covered Models（Fable 5.1 等）は30日以上保持・ZDR不可・Bedrock/Google でも追従
 - PostToolUse hookSpecificOutput.updatedToolOutput は全ツールの結果を置換可 / UserPromptSubmit は書換不可
 - Auto モードのサーバ側分類器レビュー: no-verdict は拒否、10連続で turn 停止 / deny ルールは全モードで有効
 - LLM ゲートウェイは「inspect without modifying」（本文改変は beta ヘッダ対・preserved thinking を壊す）
-->
# Claude Code で「個人情報を送らない」× Auto モード維持 — 最終推奨（2026-10-05 版・批評反映済み）

> 前提の確認: 「モデルを変えた」が Claude Fable 5.1（このセッションのモデル）を指すなら、Fable 5.1 / Fable 5 / Mythos 5.1 / Mythos 5 は **Covered Models** で、プロンプトと出力は最低 30 日保持・ZDR 不可・このポリシーは Bedrock/Google 上でも「モデルに追従」します（https://support.claude.com/en/articles/15425695-covered-models 、https://platform.claude.com/docs/en/manage-claude/api-and-data-retention ）。つまり前回答の「補完策（ZDR / Bedrock 東京）」は Fable を使う限り全部消え、**取りこぼし 1 件 = 最低 30 日 Anthropic 側に残る**条件で設計する必要があります。「何から何へ変えたか」は未確認なので §8 で確認します。以下の構成は「送らない」をモデル非依存で成立させ、推論先の選択（§7, §8）は残留リスクの話として切り離しています。

---

## 1. 結論 — 最善の構成を一枚で

**骨格: 「検出して消す」ではなく「Claude Code が到達できる場所に生の個人情報を存在させない」。** 個人情報が必要な処理は、Claude が書いた SQL/スクリプトを手元の隔離実行口（自作 MCP `safe-data`）で実データに対して走らせ、モデルには**スキーマ・集計・擬似化済みデータ**だけを返す。日本語 PII 検出器は「最後の防衛線」に格下げし、API 経路の本文を書き換えるプロキシは採らない。Auto モードは「deny ルール・フックの deny/書換・サンドボックス自動承認」という**無言で解決する機構だけ**を使い、フックが `ask` を一切返さないことで維持する。**Phase 0 の設定は「PII の実在パスだけ塞ぎ、開発ツールチェーン・Desktop 既定ワークスペース・ローカル dev サーバ・AWS/Google API を壊さない」ことを受け入れ試験で確認してから配布する**（前版の設定例にはこれを壊す項目が複数あった。§3.2）。

| 優先 | 層 | 何を | どの仕組みで（確認済み） | 何を防ぐか | Auto への影響 |
|---|---|---|---|---|---|
| **P0** | 0. 前提確認・棚卸し | プラン・モデル・利用面・バージョン・既存スキル/コネクタ/直近 2 週間のツール利用 | `/status` `/model` `/mcp` `claude --version`、Covered Models 表、`~/.claude/projects/**/*.jsonl` の tool_use 集計 | 消費者プランでの学習利用・5 年保持、Fable の 30 日強制保持、既存業務フローの「静かな停止」 | なし |
| **P0** | 1. 到達面の限定 | Desktop の in-process ツール（画面・クリップボード・ターミナル・他セッション転記）、クラウド面、`!` シェル、Gmail/Drive/Calendar/Claude Docs コネクタ、Artifact | `permissions.deny`（ツール単位）、`enableArtifact:false`、Desktop 管理キー（Team/Enterprise または実測で有効確認時）、claude.ai で切断、org の `blocked`、クラウドセッション無効化 | 生データが「ツールでも Read でもない経路」で文脈に入ること、Anthropic ホストへの書込み | ツールが消えるだけ。確認は増えない |
| **P0** | 2. 読ませない | `~/PII/` ゾーン、PII 実在パス、`permissions.deny`、Bash サンドボックス、資格情報の env スクラブ | Read/Bash/WebFetch deny（全モード・分類器より先に即決）、`sandbox.filesystem.denyRead`+`allowRead`、`network.deniedDomains`（データプレーン実ホストのみ）、`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` | Read/cat/`grep -r`/自前スクリプト/DB 直結/外部送信/環境変数経由の資格情報 | deny は無言。サンドボックス内 Bash は無確認で実行 |
| **P1** | 3. 見せずに処理 | 自作 stdio MCP `safe-data`（compute-to-data） | `schema_describe` / `sql_run`（PII 無しビュー）/ `py_run`（`--network none` コンテナ）/ `fixture_from`、出力 40,000 字上限、taint 型 egress ガード | 生レコードが文脈に入る必要そのもの | 最良。読み取り系は自動承認、確認ゼロ |
| **P1** | 4. ソース側の構造遮断 | DB ビュー/専用ロール、ETL、Sentry サーバ側スクラブ、`support-intake`（Gmail/Slack 取込→擬似化→vault、復元は MCP プロセス内のみ、**既定では本文を返さず構造化フィールドのみ**） | SQL ビュー、PostgreSQL Anonymizer、サービスアカウント、要配慮語彙ゲート | 検出精度に依存せず PII を「存在しなくする」、要配慮の「内容」の流出 | なし |
| **P2** | 5. 最後の防衛線 | `pii-guard` 常駐デーモン + フック（**全ツール対象、経路別ルールセット**） | PostToolUse `updatedToolOutput`（matcher 省略＝全ツール、形保持の汎用置換）、UserPromptSubmit 高確信 `block`、PostToolBatch 検知+隔離（失敗結果含む） | 自由記述に残る取りこぼし、PostToolUseFailure 経路 | +数十 ms。`ask` は返さない |
| **P2** | 6. 残留最小化・監査 | 副次トラフィック・転記保持・送信本文の保全 | 個別 env フラグ、`cleanupPeriodDays`、`OTEL_LOG_RAW_API_BODIES=file:` + 夜間 DLP + カナリア | /feedback 送信、長期残留、「何を送ったか分からない」 | なし |
| **P3** | 7. 補完（推論先） | Bedrock 東京 `jp.anthropic.claude-opus-5-5`（または Opus 5 / 4.8 の jp.）+ `data_retention_mode none`、または非 Covered モデル + ZDR | Bedrock ZOA/ZDR、jp. 地理プロファイル | 30 日保持、国外所在 | WebSearch・コネクタ・Desktop の制約（§7） |

設計原則（これを守れば要件 B は崩れない）: (a) 取得しないものは漏れない。(b) 書き換えは Claude Code が公式に用意した 2 点（PreToolUse `updatedInput` / PostToolUse `updatedToolOutput`）だけで行い、ワイヤ上は改変しない。(c) フックは fail-open（タイムアウト＝決定なし、形不一致＝原文送信）なので、**フックが落ちても漏れない状態**を層 1〜4 で先に作る。(d) `ask` を返さない、blanket allow に頼らない、サンドボックス内 Bash を使う。(e) **deny は PII の実在パス・実ホストに限定し、ワイルドカードで開発基盤ごと塞がない**（静かな回帰は確認ダイアログより要件 B を壊す）。

---

## 2. なぜこれが最善か — 他の案を採らない理由

| 案 | 採らない（単独では採らない）理由 | 根拠 |
|---|---|---|
| **送信プロキシ単独**（LiteLLM+Presidio 等で本文マスク） | 公式互換ガイドが「inspect without modifying」と明記。本文書換は beta ヘッダ/本文ペア・preserved thinking（`bound to a different conversation` 400 → 以後 thinking 落ち）・プロンプトキャッシュ（累積ハッシュ）を壊す。Auto モードのサーバ側分類器判定は**応答に同梱**されるため、応答を触ると「verdict なし＝拒否」、10 連続で turn 停止（要件 B 直撃）。独自 `ANTHROPIC_BASE_URL` では MCP ツール検索が既定で無効化され全ツール定義が毎リクエストに載る。LiteLLM の Anthropic ネイティブ Presidio 経路は 2026-06 まで未動作、ストリーム復元は `text_delta` のみ、履歴中 `tool_use.input` 未スキャン（#41265 open）、2026-09 に回帰複数。Desktop は third-party inference 設定が必要で claude.ai ログインと排他。 | https://code.claude.com/docs/en/llm-gateway-protocol 、https://code.claude.com/docs/en/permission-modes#server-side-classifier-review 、https://code.claude.com/docs/en/mcp#configure-tool-search 、https://github.com/BerriAI/litellm/issues/41265 |
| **検出ベース単独**（マスキング MCP / フックだけ） | 日本語氏名の再現率は最良の自己申告でも ≈0.8（合成データ）、実文では 0.5 前後の報告（pleno_anonymize_ja F1 0.467）。GiNZA/Presidio は住所 ≈0.08・文書レベル誤検出 0.83（Sumi 計測、自己申告）。取りこぼしはそのまま送信＝Fable では 30 日保持。フックは fail-open。異体字・和暦・ローマ字揺れ・新規問い合わせ者（辞書外）が実運用の穴（§4.8）。 | https://github.com/NagaYu/sumi 、https://huggingface.co/plenoai/pleno_anonymize_ja |
| **deny ルールだけ** | `grep -r .`・Python/Node の自前 open・`/usr/bin/psql`・`sh -c` には効かない（公式「not a security boundary」）。OS 強制はサンドボックスのみ、サンドボックスは Read ツール/WebFetch/MCP/フックに効かない。 | https://code.claude.com/docs/en/permissions#bash-rule-limits 、https://code.claude.com/docs/en/sandboxing#what-runs-outside-the-sandbox |
| **Bedrock/ZDR だけ** | 「送ってから残らない」であって「送らない」ではない。Fable は Bedrock でも `aws_review`（30 日保持 + AWS 人手レビュー）必須、東京は global のみで jp. 無し。ZDR/none でも T&S フラグ分は最長 2 年保持。 | https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html 、https://platform.claude.com/docs/en/manage-claude/api-and-data-retention |
| **本案（構造遮断 + compute-to-data）** | 構造化データ（DB/CSV/ログのフィールド）については検出精度に依存しない保証。Anthropic 自身の「Code execution with MCP」が描く「中間結果は実行環境に留め、PII はトークン化」の設計をローカルで実装する形。集計値は PPC Q&A Q1-17 で「統計情報は個人情報に該当しない」。遅延ゼロ・確認ゼロ。残るのは自由記述だけなので検出器の負荷が最小になる。 | https://www.anthropic.com/engineering/code-execution-with-mcp 、https://www.ppc.go.jp/personalinfo/faq/APPI_QA/ |

※ Anthropic ホスト型の code execution / programmatic tool calling は「中間結果をモデル文脈に載せない」が、コンテナが Anthropic 側で 30 日保持・ZDR 非適格・Bedrock 非提供。プライバシー手段ではない（https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool ）。

---

## 3. 前回の回答からの訂正・更新

### 3.1 前回答（初回）からの訂正

1. **「hook で出力を書き換えられるのは基本的に MCP ツールだけ」は誤り。** PostToolUse の `hookSpecificOutput.updatedToolOutput` は全ツール対象（Bash は `{stdout, stderr, interrupted, isImage}` の形を保つ必要、形不一致は**黙って無視され原文が送られる**。MCP 出力は検証なし）。`updatedMCPToolOutput` は非推奨の MCP 限定フィールド。https://code.claude.com/docs/en/hooks#posttooluse-decision-control
2. **「③送信プロキシを最後の安全網に」は撤回。** §2 の通り公式に非推奨で、要件 B（分類器 verdict）を壊す。プロキシは「本文不改変の受動監査」用途に限る。
3. **「Bedrock/Vertex 東京・ZDR は補完策」は Fable 系では成立しない。** Covered Models は ZDR 不可・30 日保持がどのプラットフォームでも追従。Auto 対応かつ東京で地理（jp.）プロファイルを持つことを**確認できたのは Opus 5.5（`jp.anthropic.claude-opus-5-5`）と Opus 4.8（`jp.anthropic.claude-opus-4-8`）**。**Opus 5 も AWS の地域互換表で東京「Geo 対応」と記載があり候補になる**（Opus 4.7+ として Auto 対応）が、jp. 推論プロファイル ID 文字列は未取得（**未確認**、導入時に `aws bedrock list-inference-profiles --region ap-northeast-1` で確定）。Sonnet 5 / Sonnet 5.5 / Fable 5.1 / Mythos 5.1 は東京 global のみ。Google Agent Platform は新世代モデルが global/us/eu のみで日本閉域不可（asia-northeast1 の可否は未確認）。https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html 、https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html 、https://code.claude.com/docs/en/amazon-bedrock
4. **Auto モード分類器が第 2 の審査経路であることが抜けていた。** 現行既定（第一者 API・v2.1.271〜278+ でサーバ側レビュー）では、分類器の判定は**本体のモデル要求/応答に同梱**され、別経路の送信ではない。Claude Code 自身が分類器要求を送る経路（`CLAUDE_CODE_AUTO_MODE_SERVER=0`、またはサーバがレビューしない Bedrock 等でのフォールバック時）では「転記の一部 + 保留中アクション」を**別要求**として送り、ユーザーメッセージ・読み取り以外のツール呼び出しの引数・CLAUDE.md を見てツール結果は除外する。いずれの経路でも**コマンド引数（SQL/コマンド文字列/MCP 引数）と CLAUDE.md は審査対象**で、実名を書けば送られる。別途サーバ側 probe は**ツール結果も走査**する。https://code.claude.com/docs/en/permission-modes#server-side-classifier-review 、https://code.claude.com/docs/en/permission-modes#how-the-classifier-evaluates-actions
5. **大出力のディスク退避経路が抜けていた。** MCP の成功結果が 25,000 トークン／50,000 文字超だと `~/.claude/projects/<proj>/<session>/tool-results/` にファイル化され Read で再読込される（`mcp__*` フックの外）。Bash 出力約 30,000 文字超の退避先は公式には「セッションディレクトリ」としか書かれておらず、同じ `tool-results/` と**推定**（claude-directory の説明と整合、実測で確認）。https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings 、https://code.claude.com/docs/en/tools-reference#output-limits 、https://code.claude.com/docs/en/claude-directory
6. **Desktop アプリのコネクタと内部ツールは MCP 設定で制御できない。** claude.ai コネクタは in-process `type:"sdk"` で届き「no MCP setting or managed-mcp.json reaches them」。`disableClaudeAiConnectors` も Desktop ローカルセッションには届かない。切断（claude.ai/customize/connectors）か org の `blocked` のみ。in-process `sdk` サーバは `allowedMcpServers`/`deniedMcpServers` の **3 段チェック全部をスキップ**する。本セッションのツール名は `mcp__<uuid>__<tool>` 形式（文書未記載・観測）。https://code.claude.com/docs/en/mcp#how-connectors-reach-claude-code 、https://code.claude.com/docs/en/managed-mcp#how-a-server-is-evaluated
7. **WebFetch は生ページを別のモデル呼び出しに送る**ため PostToolUse では遅い。止められるのは deny/PreToolUse のみ。`WebFetch(domain:*.x)` は apex に一致しないので両方列挙が必要。全面停止は `CLAUDE_CODE_DISABLE_WEB_FETCH=1`（v2.1.285+）。https://code.claude.com/docs/en/tools-reference#webfetch-tool-behavior 、https://code.claude.com/docs/en/permissions#webfetch 、https://code.claude.com/docs/en/env-vars
8. **フックは fail-open。** command/http フックのタイムアウトは「決定なし」で続行、接続失敗・非 2xx も続行。PostToolUseFailure（非ゼロ終了の Bash・MCP エラー結果）は観測専用で書き換え手段が無い（`systemMessage`/`additionalContext`/`terminalSequence` のみ）。PostToolBatch の `block` はツール結果を会話に残す（次の発話で送られる）。https://code.claude.com/docs/en/hooks#timeouts 、https://code.claude.com/docs/en/hooks#posttoolusefailure 、https://code.claude.com/docs/en/hooks#posttoolbatch-decision-control
9. **プロンプト経路の扱いが抜けていた。** UserPromptSubmit はプロンプトを書き換えられず `block` のみ。ブロックしても原文はローカル転記・履歴に残る。@メンションしたファイルは**いかなるツールフックも発火しない**（Read deny が best-effort で効くだけ）。https://code.claude.com/docs/en/hooks#userpromptsubmit-decision-control 、https://code.claude.com/docs/en/hooks#what-a-blocked-prompt-leaves-behind
10. **`!` シェルモードはサンドボックス外で実行され、出力がそのまま文脈に入り Claude が自動応答する。** deny もフックも通らない。https://code.claude.com/docs/en/interactive-mode#shell-mode-with-prefix
11. **クラウド面（`claude --cloud`、Desktop「Open in Cloud」、Routines、Ultrareview）はローカル settings/フック/サンドボックスを読まない。** リポジトリをバンドルして Anthropic VM にアップロードする。https://code.claude.com/docs/en/claude-code-on-the-web 、https://code.claude.com/docs/en/routines
12. **プラン種別の確認が抜けていた。** 消費者プラン（Free/Pro/Max）は「モデル改善」設定が ON なら Claude Code の入出力も学習に使われ 5 年保持。商用（Team/Enterprise/API）は学習不使用。Desktop の computer use と Dispatch は Pro/Max 限定機能なので、本環境は消費者プランの可能性がある。https://code.claude.com/docs/en/data-usage 、https://code.claude.com/docs/en/desktop#let-claude-use-your-computer
13. **要配慮個人情報の「内容」（病名・服薬・検査値）が抜けていた。** 識別子を消しても本文に残り、擬似 ID 付き個票は 自社側で復元可能＝仮名加工情報として第三者提供不可（PPC Q&A 14-17）。個票ではなく集計のみ返す設計が必要。
14. **「日本語の検出は自前で足す（GiNZA 等）」は過小評価。** 一般 NER は PII 用ではなく、誤検出（都道府県・社名・部品番号・非 DOB 日付）がコード/ログ解析を壊す。再現率も §2 の通り。構造的遮断を主役にし、検出は残余層。
15. **「deny とフックは Auto でも効く」は正しいが条件が抜けていた。** フックの `ask` は Auto でも必ずプロンプト。`blockReadsOutsideWorkingDirectories` 下で「シェルパーサが追跡できないコマンド（サブシェル・複数 cd）」はサンドボックスが強制していない限り Auto でもプロンプト。`strictAllowlist`/`allowManagedDomainsOnly` は Auto 固有の「コマンド単位ドメイン承認」を無効化する。https://code.claude.com/docs/en/permission-modes#actions-no-mode-auto-approves 、https://code.claude.com/docs/en/sandboxing#per-command-allowed-domains-in-auto-mode
16. **「送ってから消す」の余地はさらに無い。** ZDR/HIPAA 契約下でも T&S フラグ時は最長 2 年保持。Claude Code は HIPAA readiness 対象外。https://platform.claude.com/docs/en/manage-claude/api-and-data-retention
17. **3 省 2 ガイドラインの版。** 医療情報システム安全管理 GL は第 7.0 版（令和 8 年 6 月）、Q&A Q18 が生成 AI を明示。提供事業者 GL は**第 2.0 版（令和 7 年 3 月 28 日公表）**が現行。https://www.mhlw.go.jp/content/10808000/001752355.pdf 、https://www.soumu.go.jp/menu_news/s-news/01ryutsu06_02000427.html
18. **Anthropic ホスト型 code execution / PTC はプライバシー手段ではない**（§2 注）。

### 3.2 本版（最終版）での自己訂正 — 前版の設定例が要件 B を壊していた点

- **`Read(~/Library/**)` と `denyRead: ["~/Library"]` は広すぎた。** Desktop の Code タブ既定ワークスペースは `~/Library/Application Support/Claude/scratch-workspaces/<id>/…`（本セッションの cwd で観測、文書未記載）で、Read deny は Edit/Write にも及ぶため Desktop の既定ワークスペースが読み書き不能になる。サンドボックス側も `~/Library/Caches/pip`・`ms-playwright`・`~/Library/Python`・`~/Library/pnpm`・`~/Library/Developer` が読めなくなる。→ PII 実在パスに限定 + `allowRead` で再開放（「狭い allow が deny 領域を再開放する」挙動は公式表で確認。https://code.claude.com/docs/en/sandboxing ）。`~/Documents` は「リポジトリ置き場か」の決定点（§8）。https://code.claude.com/docs/en/permissions#read-and-edit
- **`allowLocalBinding:false` の緩和策（`allowedDomains` に `127.0.0.1:3000`）は効かない。** 公式: 「`allowedDomains` の localhost エントリはプロキシ経由接続にだけ効き、直接接続は変えない。Claude Code はサンドボックス内コマンドに NO_PROXY を設定し localhost へ直接接続させる」「既定では dev サーバやコンテナ内 DB に直接接続できない。macOS では `allowLocalBinding` を true にすると listen も可能になる」。→ §4.1 で選択肢 (a)(b)(c) を提示。https://code.claude.com/docs/en/sandboxing#a-command-fails-to-reach-a-server-on-localhost
- **`Read(./**/*.csv)` deny と `*.safe.csv` 例外は矛盾**（`.csv` で終わるので一致、deny は allow で上書き不可、Write/Edit も止まる、`*.ipynb` deny は NotebookEdit も止める）。→ ディレクトリ単位の deny + `.tsv`/`.safecsv` 出力に変更。
- **`Read(~/.claude/projects/**)` deny は自動メモリ（`~/.claude/projects/<project>/memory/MEMORY.md` を Read/Write で読み書き）を壊す。** → `**/*.jsonl` と `**/tool-results/**` に絞るか `autoMemoryDirectory` を移す。https://code.claude.com/docs/en/memory#auto-memory
- **§4.5「MCP 集合の固定」は事実誤認。** `managedMcpServers` は `https://` の `http`/`sse` 専用で `command`/`args`/`env` を持てない（stdio の自作サーバは配布不可）。`strictPluginOnlyCustomization:["mcp"]` は `~/.claude.json`/`.mcp.json` のサーバを止めるので safe-data 自身が消える。いずれも Managed 専用。in-process `sdk` 型は allow/deny 両リストの対象外。https://code.claude.com/docs/en/managed-mcp#provide-servers-through-managed-settings
- **ドット付きツール名（`schema.describe` 等）は API のツール名規約 `^[a-zA-Z0-9_-]{1,128}$` に反する。** → 全てアンダースコアに統一。https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- **「要配慮の内容は集計のみ」と宣言しつつ `tickets.get` が擬似化済み本文（健康情報を含む）を返していた。** → 既定は構造化フィールドのみ、本文返却は deny 既定の別ツール + 決定点 5 + 要配慮語彙ゲート（§4.4, §4.8）。
- **`deniedDomains` の `*.amazonaws.com` / `*.googleapis.com` は全モードで拒否されコマンド単位承認でも開かないため、`aws`/`cdk`/`terraform`/`gcloud`/Chromium 取得が永久に失敗する。** → データプレーンの実ホストだけ deny、境界は IAM に置く。
- **Sentry deny が方針（移行期 90 日は read 系全部）と不一致、GitKraken の Issue/PR 本文ツールが開いたまま `gh pr view` だけ止めていた、WebFetch が denylist/allowlist の二重記述、Artifact 無効化と Claude Docs コネクタが設定に無かった、汚染マーカーの解除経路が無かった。** → §4.1, §4.5, §4.6 で整合。
- **§4.3 SQL は `hmac` が search_path から解決できず実行時エラー**（pgcrypto のスキーマ未修飾）。→ スキーマ修飾 + read-only の担保は GRANT 不付与が本線と明記。

---

## 4. 具体的な実装

### 4.A Phase 対応表（工数は 1 名換算）

| Phase | 期間 | 含むもの | 前提となる決定点（§8） | 完了判定 |
|---|---|---|---|---|
| **Day 0** | 0.5 日 | `/status` `/model` `/mcp` `claude --version`、プラン確認、スキル/コネクタ/直近ツール利用の棚卸し、転記・貼付キャッシュ削除、in-process ツール deny と D&D/画像経路の実測 | DP1, DP3, DP9, DP13 | §4.9 Day 0 チェックリスト全項目 |
| **Phase 0** | 1〜2 日 | `~/.claude/settings.json`（deny / sandbox / env / enableArtifact）、コネクタ切断、Desktop 設定、`autoMemoryDirectory` 移設 | DP4, DP11, DP14, DP15, DP16 | Phase 0 受け入れ試験（Auto で無プロンプト・Desktop 既定ワークスペース R/W・dev サーバ到達） |
| **Phase 1** | 3〜5 日 | DB ビュー + `claude_ro`、`safe-data`（`schema_describe`/`sql_run`/`fixture_from`/`files_describe`）、CSV→`./data/safe` ETL、Sentry 組織スクラブ、既知エンティティ辞書の夜間生成、カナリア | DP5, DP17（DB エンジン）, DP18（sudo/別ユーザー）, DP19（Docker） | 構造化データ由来の PII が転記・保全に 0 件 |
| **Phase 2** | 4〜5 週 | `support-intake` + vault、`slack-safe`、`pii-guard` デーモン + 全フック + 日本語 NER、`py_run`、契約テスト CI、夜間 DLP | DP4, DP5, DP10, DP12 | 契約テスト全緑、FN 実測 |
| **Phase 3** | 組織判断 | Bedrock jp. + SCP + Model Invocation Logging、MDM managed-settings、OTel 監査 | DP2, DP6, DP7, DP8 | §7 |

### 4.0 Day 0: 前提確認・棚卸し（半日）

```
/status            # ログイン種別（claude.ai サブスク or Console/API）、プラン、API provider、Setting sources
/model             # Fable 5.1 か？ → Covered Model
/mcp               # 接続中サーバ名（コネクタの実名 mcp__<uuid> を deny 用に記録）
claude --version   # 以下の最小版: updatedToolOutput 書換 2.1.233+、classifierContext 2.1.236+、blockReads 2.1.257+、
                   # Auto 既定 2.1.283+、allowedProviders / CLAUDE_CODE_DISABLE_WEB_FETCH / MODEL_ACCESS_FALLBACK 2.1.285+、
                   # Bedrock の enforceAvailableModels 起動チェック 2.1.287+
```
- **Pro/Max なら**: claude.ai の Privacy 設定で「Help improve Claude」を OFF（30 日保持へ）、業務利用は Team/Enterprise か Console API キー（商用規約）へ移行を前提にする。org の connector tool-control（`blocked`）は Team/Enterprise のみ。Desktop 管理キーが Pro/Max 端末で読まれるかは §4.1 の実測項目。
- **棚卸し（要件 B の実体は「今の仕事が回る」こと）**: 本環境のスキルと依存先 — Slack コネクタを読む Skill、Google API を使う Skill、並行サブエージェントを使う Skill、Chrome 拡張/computer use を使う Skill など（筆者環境の例）。各スキルが SKILL.md 内の `` !`cmd` `` 動的注入を使うか確認してから `disableSkillShellExecution` を決める（https://code.claude.com/docs/en/settings-reference#disableskillshellexecution ）。`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` は「Bash と**サブエージェント**の `run_in_background`・自動バックグラウンド化・Ctrl+B」を全部止める（https://code.claude.com/docs/en/env-vars ）ので、`sonnet-orchestrate` を残すなら設定せず、背景タスク出力は PostToolUse（全ツール matcher、`TaskOutput` 含む）+ 夜間 DLP で守る。PII に触れないスキルは「PII セッション用 settings プロファイル（`--settings`）と通常プロファイルを分ける」選択肢を DP9 に置く。
- `claude purge <project>` 等で**統制導入前の転記・貼付キャッシュ・履歴を削除**（`/insights`・`/resume`・`search_session_transcripts` で再送される）。https://code.claude.com/docs/en/claude-directory#clear-local-data
- **実測（偽 PII で）**: (1) 偽 PII を表示した画面/ターミナルで `mcp__computer-use__app_screenshot`・`mcp__terminal__read_terminal`・`mcp__ccd_session_mgmt__search_session_transcripts` を呼ばせ、bare 名 deny が効くか。(2) Finder から `~/PII/inbox/fake.csv` を Desktop チャットにドラッグ＆ドロップし、転記（`~/.claude/projects/**/*.jsonl`）に内容が入るか、Read ツールを経由するか。(3) 画像を貼ったときの UserPromptSubmit `prompt` に `[Image #N]` 等のマーカーが入るか。(4) Read/Grep/Glob/WebFetch/Edit の PostToolUse `tool_response` の形を記録（契約テストの基準にする）。(5) Desktop ローカルセッションで `sandbox.enabled` が強制されるか（`claude doctor` と `curl` 拒否で確認）。効かない項目は DP3（CLI/VS Code へ寄せる）に倒す。

### 4.1 Phase 0（今日・settings.json のみ）: 到達面の限定 + 読ませない + 残留最小化

単独エンジニアなら `~/.claude/settings.json`、配布できるなら `/Library/Application Support/ClaudeCode/managed-settings.json`（MDM。managed の deny はどの階層・CLI 引数でも上書き不可。https://code.claude.com/docs/en/managed-settings ）。**`mcp__<…-uuid>__…` のプレースホルダは `/mcp` で実名を確認して置換すること。未置換のルールは何にも一致せず無効。**

```jsonc
{
  "permissions": {
    "defaultMode": "auto",                       // ~/.claude/settings.json / managed / --settings からのみ有効
    "disableBypassPermissionsMode": "disable",   // permissions 配下に置く
    "deny": [
      // --- PII ゾーンと PII の実在パス（Read deny は Edit/Write・cat/head/tail/sed・< > リダイレクトにも適用。
      //     https://code.claude.com/docs/en/permissions#read-and-edit ）。~/Library 全体・~/Documents 全体は塞がない ---
      "Read(~/PII/**)", "Read(~/Downloads/**)", "Read(~/Desktop/**)",
      "Read(~/Library/Mail/**)", "Read(~/Library/Messages/**)",
      "Read(~/Library/CloudStorage/**)",            // Google Drive / OneDrive 同期
      "Read(~/Library/Mobile Documents/**)",        // iCloud Drive
      "Read(~/Library/Containers/com.apple.mail/**)",
      "Read(~/Library/Group Containers/group.com.apple.notes/**)",   // メモ.app（パスは環境で確認）
      // "Read(~/Documents/**)",                    // リポジトリを ~/Documents に置かない場合のみ（DP14）
      "Read(~/.claude/projects/**/*.jsonl)",        // 他セッション転記
      "Read(~/.claude/projects/**/tool-results/**)",// 退避ファイル（Phase 2 で即時スクラブに置換後も残して良い）
      "Read(~/.claude/history.jsonl)", "Read(~/.claude/.credentials.json)",
      "Read(//Volumes/**)",
      // --- プロジェクト内はディレクトリ単位（拡張子 deny は Write/Edit/NotebookEdit を巻き込む） ---
      "Read(./data/raw/**)", "Read(./exports/**)", "Read(./notebooks/raw/**)", "Read(./out-real/**)",
      "Read(./**/*.sqlite)", "Read(./**/*.sqlite3)", "Read(./**/*.parquet)", "Read(./**/*.eml)",
      "Read(./**/*.pdf)",                           // Read は page-range を画像化して送る。生成は Bash スクリプト経由なら可
      "Read(./.env)", "Read(./.env.*)",
      // --- ファイルを経由しない読取・持出し ---
      "Bash(mdfind *)", "Bash(mdls *)", "Bash(pbpaste*)", "Bash(pbcopy*)", "Bash(screencapture*)",
      "Bash(osascript*)", "Bash(log show*)", "Bash(log stream*)", "Bash(ln *)",
      // --- GitHub の Issue/PR 本文（gh pr checks / status / create / diff は残す） ---
      "Bash(gh issue view*)", "Bash(gh issue list*)", "Bash(gh pr view*)", "Bash(gh api *)", "Bash(gh search *)",
      "mcp__plugin_gitkraken_gitkraken__issues_get_detail", "mcp__plugin_gitkraken_gitkraken__issues_assigned_to_me",
      "mcp__plugin_gitkraken_gitkraken__pull_request_get_detail", "mcp__plugin_gitkraken_gitkraken__pull_request_get_comments",
      "mcp__plugin_gitkraken_gitkraken__pull_request_assigned_to_me", "mcp__plugin_gitkraken_gitkraken__repository_get_file_content",
      // --- WebFetch: デーモン停止時のバックストップ（本線は pii-guard PreToolUse の公開ドキュメント allowlist。apex と *. 両方） ---
      "WebFetch(domain:sentry.io)", "WebFetch(domain:*.sentry.io)",
      "WebFetch(domain:slack.com)", "WebFetch(domain:*.slack.com)",
      "WebFetch(domain:mail.google.com)", "WebFetch(domain:docs.google.com)", "WebFetch(domain:drive.google.com)",
      "WebFetch(domain:storage.googleapis.com)", "WebFetch(domain:*.amazonaws.com)",
      "WebFetch(domain:example.internal)", "WebFetch(domain:*.example.internal)",
      // --- 組み込み/Desktop in-process ツール（bare 名の deny はツール定義ごと文脈から消える。sdk 型への効果は Day 0 で実測） ---
      "Monitor", "ReadMcpResourceTool", "ListMcpResourcesTool", "RemoteTrigger",
      "mcp__computer-use", "mcp__claude-in-chrome", "mcp__terminal",
      "mcp__ccd_session_mgmt", "mcp__ccd_connectors", "mcp__ccd_session__move_to_cloud", "mcp__scheduled-tasks",
      // Claude_Browser はページ内容を読む/操作するツールだけ deny（preview_start / preview_logs / preview_list / preview_stop /
      // navigate / resize_window / tabs_* / read_console_messages は残す。Faker シード環境に対してのみ使う）
      "mcp__Claude_Browser__get_page_text", "mcp__Claude_Browser__read_page", "mcp__Claude_Browser__find",
      "mcp__Claude_Browser__computer", "mcp__Claude_Browser__javascript_tool", "mcp__Claude_Browser__form_input",
      "mcp__Claude_Browser__read_network_requests", "mcp__Claude_Browser__browser_batch",
      "mcp__plugin_aws-core_aws-mcp__aws___run_script", "mcp__plugin_aws-core_aws-mcp__aws___get_presigned_url",
      // Claude Docs コネクタ（社内文書の読取と Anthropic ホスト Docs への書込みの双方向経路）: 切断か deny
      "mcp__<docs-uuid>",
      // Gmail/Drive/Calendar は切断（claude.ai 側）が本線。Slack は Phase 2 で slack-safe に置換するまで PII チャンネルに触れない
      "mcp__<gmail-uuid>", "mcp__<drive-uuid>", "mcp__<calendar-uuid>",
      // Sentry: スクラブ有効化後 90 日はサーバ名ごと deny（§4.5）。恒久 deny は replays/logs/profiles/execute_sentry_tool
      "mcp__<sentry-uuid>"
    ]
  },
  "enableArtifact": false,             // https://code.claude.com/docs/en/settings-reference#enableartifact
  "sandbox": {
    "enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
    "autoAllowBashIfSandboxed": true,
    "excludedCommands": ["docker compose *"],   // docker はサンドボックス非互換（公式）。必要最小のパターンだけ
    "filesystem": {
      "denyRead": ["~/PII", "~/Downloads", "~/Desktop",
                   "~/Library/Mail", "~/Library/Messages", "~/Library/CloudStorage", "~/Library/Mobile Documents",
                   "~/Library/Containers/com.apple.mail", "~/Library/Group Containers/group.com.apple.notes",
                   "~/.claude/projects/**/*.jsonl", "~/.claude/projects/**/tool-results", "~/.claude/history.jsonl",
                   "~/.claude/.credentials.json",
                   "~/.aws", "~/.config/gcloud", "~/.pgpass", "~/.pg_service.conf", "~/.my.cnf",
                   "~/.zsh_history", "~/.psql_history", "/Volumes"],
      // "~/.ssh" を deny すると SSH 経由の git が壊れる → git を HTTPS + gh auth に寄せた上で追加（DP16）
      "allowRead": ["~/Library/Application Support/Claude", "~/Library/Caches", "~/Library/Python",
                    "~/Library/pnpm", "~/Library/Developer"]   // ~/Library を広く塞ぐ場合の再開放例
    },
    "network": {
      "allowedDomains": ["github.com", "*.github.com", "objects.githubusercontent.com", "codeload.github.com",
                         "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
                         "registry-1.docker.io", "auth.docker.io", "ghcr.io", "nodejs.org"],
      // deniedDomains は全モードで拒否・コマンド単位承認でも開かない → データプレーンの実ホストだけ。境界は IAM に置く
      "deniedDomains": ["db.prod.example.internal", "*.rds.amazonaws.com", "*.docdb.amazonaws.com",
                        "dynamodb.ap-northeast-1.amazonaws.com", "logs.ap-northeast-1.amazonaws.com",
                        "rds-data.ap-northeast-1.amazonaws.com", "athena.ap-northeast-1.amazonaws.com",
                        "<pii-bucket>.s3.ap-northeast-1.amazonaws.com",
                        "gmail.googleapis.com", "www.googleapis.com", "sheets.googleapis.com", "docs.googleapis.com",
                        "slack.com", "*.slack.com", "sentry.io", "*.sentry.io",
                        "api.anthropic.com", "claude.ai"],
      "allowLocalBinding": true            // 選択肢 (a)。false のままならローカル dev サーバ系が全滅（下記）
      // strictAllowlist / allowManagedDomainsOnly は置かない（Auto の「コマンド単位ドメイン承認」が死ぬ）
    },
    "credentials": {
      "envVars": [ { "name": "PGPASSWORD", "mode": "deny" }, { "name": "DATABASE_URL", "mode": "deny" },
                   { "name": "AWS_ACCESS_KEY_ID", "mode": "deny" }, { "name": "AWS_SECRET_ACCESS_KEY", "mode": "deny" },
                   { "name": "AWS_SESSION_TOKEN", "mode": "deny" } ]
    }
  },
  "bashOutputMaxChars": 128000,        // 退避（ファイル化→Read 再読込）を減らす。https://code.claude.com/docs/en/settings-reference#bashoutputmaxchars
  "respondToBashCommands": false,      // `!` 出力への自動応答を止める（文脈には残る）。…#respondtobashcommands
  // "disableSkillShellExecution": true, // 棚卸しで !`cmd` 依存スキルが無いと分かった場合のみ。…#disableskillshellexecution
  "autoMemoryDirectory": "~/claude-memory",   // deny 範囲外へ移し、夜間 DLP の走査対象に加える。…#automemorydirectory
  "cleanupPeriodDays": 7,              // …#cleanupperioddays
  "desktopSessionCleanupPeriodDays": 7,// User/managed スコープ。…#desktopsessioncleanupperioddays
  "env": {
    "DISABLE_FEEDBACK_COMMAND": "1",            // /feedback /bug /share は会話履歴全文を送る。https://code.claude.com/docs/en/env-vars
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",  // タイトル生成の別モデル呼出しも止まる
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",    // Bash/フック/stdio MCP の環境から資格情報を除去（名前・値の形で判定）
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1"      // @file を Read ツール経由にして deny を適用（MCP リソース @・D&D 添付への効果は未確認）
    // "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1" // 背景サブエージェントも止める。sonnet-orchestrate を捨てる場合のみ
  }
}
```

要点と注意:
- **`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` は使わない。** feature-flag 取得も止まり Remote Control・`/auto-mode-setup`・claude.ai スキル同期が使えなくなる。個別フラグで代替（https://code.claude.com/docs/en/env-vars ）。
- **ローカル dev サーバ（`allowLocalBinding`）の選択肢**（https://code.claude.com/docs/en/sandboxing#a-command-fails-to-reach-a-server-on-localhost ）: (a) `true` を許容し、代わりに「PII を持つローカルサービス（本番スナップショット DB、実データに繋がる API）を同一マシンで動かさない」運用 + DP12 で担保（`true` にすると localhost の**全ポート**に到達でき、認証無しのローカルサービスがサンドボックス外の代理になり得る点を受容）。(b) `false` のまま `sandbox.excludedCommands` に `npm run dev *`/`pnpm dev *` 等の起動コマンドだけを置く（非サンドボックス実行は Auto では分類器審査・無プロンプト。ただし除外コマンドは全アクセスで動く）。(c) ブラウザ確認は `mcp__Claude_Browser__preview_start`/`preview_logs`/`navigate` を残す（サーバ名ごと deny しない）。既定は (a)。Linux/WSL2 では `allowLocalBinding` は効かない。
- **`blockReadsOutsideWorkingDirectories` は Phase 0 では入れない。** サンドボックスが強制していないコマンド（`excludedCommands`）やサブシェル含みは Auto でもプロンプトになり（https://code.claude.com/docs/en/permission-modes#actions-no-mode-auto-approves ）、ツールチェーン（`~/.nvm`・`~/.npm`・`~/.pyenv`）の可読性も実機未確認。Phase 1 で `additionalDirectories: ["~/src"]` と共に試験導入し、受け入れ試験（§4.9）を通してから採用。**`sandbox.filesystem.allowManagedReadPathsOnly` は置かない**（置くと開発者の `allowRead` が無効化され上記プロンプトが復活。https://code.claude.com/docs/en/settings-reference#sandbox-filesystem-allowmanagedreadpathsonly ）。
- `sandbox.excludedCommands` は Any file で、managed ロックが無い（https://code.claude.com/docs/en/settings-reference#sandbox-excludedcommands ）。単独運用では問題にならないが、チーム配布時は Phase 2 の gate が `~/.claude/settings.json` の `excludedCommands` を検査して想定外なら Bash を deny する。`docker` はサンドボックス非互換なので `py_run` は safe-data MCP プロセス（サンドボックス外）から起動する（https://code.claude.com/docs/en/sandboxing ）。
- `Bash(psql *)` 型の deny は境界ではない（`/usr/bin/psql`、`sh -c`、Python ドライバで迂回）。DB 到達性は **sandbox の `deniedDomains`（DB ホスト・RDS エンドポイント） + `credentials.envVars` deny + `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` + ローカルに本番スナップショット DB を置かない運用 + 資格情報ファイルの denyRead**で担保する（https://code.claude.com/docs/en/sandboxing#protect-credentials 、https://code.claude.com/docs/en/env-vars#what-the-subprocess-environment-scrub-removes ）。スクラブは `GITHUB_TOKEN`/`GH_TOKEN`/`HTTP(S)_PROXY` を残す（`gh` と npm/pip が動く）。Claude Code を起動するシェルに DB/AWS 資格情報を載せない（direnv の対象外にする）ことを手順書に書く。
- **AWS/Google の境界は IAM に置く**: `*.amazonaws.com` 全体 deny は `aws`/`cdk`/`sam`/`terraform` を永久停止させる。Claude Code 用 AWS ロール（`aws configure --profile claude`）に `s3:GetObject`（PII バケット）・`dynamodb:Scan/Query/GetItem/BatchGetItem`・`logs:GetLogEvents/FilterLogEvents/StartQuery/GetQueryResults`・`rds-data:*`・`athena:GetQueryResults`・`secretsmanager:GetSecretValue`（DB 資格情報）を Deny。`deniedDomains` は上記のデータプレーン実ホストだけ（`fonts.googleapis.com`、`storage.googleapis.com`、`oauth2.googleapis.com` は塞がない）。`www.googleapis.com` を deny すると Drive/Calendar API を使う `gws` スキルが止まる（Sheets/Docs は個別ホスト）→ DP9。
- `Read(~/.claude/projects/**/tool-results/**)` deny は Phase 0 では長い出力の退避ファイルを読めなくする（Claude が `| tail -200` で再実行する程度の摩擦）。Phase 2 で「退避ファイルをその場でスクラブ」を入れた後は deny を外しても良い。`~/.claude/projects/<project>/memory/` は deny 範囲外（自動メモリは Read/Write で読み書きされる。https://code.claude.com/docs/en/memory#auto-memory ）。移設した `~/claude-memory` は夜間 DLP と pre-commit 相当の走査対象。
- 組み込み/Desktop in-process ツールへの bare 名 deny が効くかは**未確認**（コネクタツールには「セッションの通常の permission rules が適用される」と文書化。https://code.claude.com/docs/en/mcp#organization-controls-on-connector-tools ）。Day 0 の実測で確認し、効かなければ PII 近傍作業は CLI/VS Code に寄せる（DP3）。
- **Desktop 管理キー**（https://code.claude.com/docs/en/desktop#managed-settings ）: `browserExternalPageTools: "disabled"`、`disableBrowserExternalNavigation: true`、`disableMobileSimulatorTools: true`。文書は「Team/Enterprise 組織が管理」の節にあるが、「ローカルセッションにはディスク配備の managed settings ファイルが適用」とも記載。**Pro/Max 端末で読まれるかは実測**（Day 0）。読まれない場合の代替: Desktop 設定で computer use を OFF、`.claude/launch.json` に `"autoVerify": false`（編集ごとの自動スクショを止める）、claude.ai でコネクタ切断、PII 近傍作業は CLI。クラウドセッションと Routines は claude.ai 管理コンソールで OFF（Team/Enterprise）。
- Gmail / Google Drive / Google Calendar / Claude Docs は claude.ai/customize/connectors で切断（Desktop で確実に消す唯一の手段）。Slack は Phase 2 で `slack-safe` に置換するまで PII チャンネルに触れない。
- `WebSearch` は残す（クエリはモデルが合成する文字列で、本案ではモデルが PII を持たない前提。Bedrock では利用不可）。
- オプション: `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`（git status/最近のコミットの起動時スナップショットを止める。コミットメッセージに PII が入る運用なら有効化）。

### 4.2 safe-data MCP（compute-to-data の実行口）

`.mcp.json`（または `claude mcp add --scope user` で全プロジェクト共通・承認ダイアログ回避。MDM 配布の場合は §4.5 の方式）。ツール名は API 規約 `^[a-zA-Z0-9_-]{1,128}$` に従い**アンダースコア**で統一（https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools ）。

```json
{
  "mcpServers": {
    "safe-data": {
      "command": "/usr/local/bin/safe-data-mcp",
      "args": ["--config", "/Users/<you>/.config/safe-data/config.toml"],
      "env": { "PGSERVICE": "claude_ro" }
    }
  }
}
```

`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` は stdio MCP の環境からも資格情報（`PGPASSWORD`、パスワード入り `DATABASE_URL` 等）を剥がすので、DB 接続情報は env ではなく `pg_service.conf`/`.pgpass`（`safedata` ユーザー所有 0600）か Keychain から取る。`PGSERVICE=claude_ro` のような名前は残る。

| ツール | 入力 | 返すもの | 内部制約 |
|---|---|---|---|
| `schema_describe(view?)` | — | `claude.*` ビューの DDL + Faker(ja_JP) ダミー 5 行 | 実データは一切返さない |
| `files_describe()` | — | `~/PII/inbox/` のハッシュ ID・推定スキーマ・合成サンプル | ファイル名は ID 化（名前に顧客名が入る事故を防ぐ） |
| `sql_run(sql, max_rows≤50)` | SELECT | 行（PII 無しビューのみ）または集計 | `BEGIN READ ONLY` でラップ、`select *`/COPY/`current_setting`/`pg_settings`/`SHOW`/`pg_read_*`/`dblink`/`SET` 禁止、集計セル n<11 は `null`+`suppressed:true`（合計はサーバ側で計算） |
| `py_run(script)` | `./analysis/**` のスクリプト | JSON（8 KB、文字列値 64 字以内） | `docker run --rm --network none -v <要求ファイルのみ>:/data:ro`（MCP プロセスから起動。Claude の Bash では docker はサンドボックス非互換）。ヘルパ `load()` が PII 列の全値を egress 拒否集合に登録 → 出力に raw/NFKC/数字のみ/かな化/異体字正規化形で含まれると**拒否**。拒否理由は「型・件数・JSON パス」のみ返し値は返さない。2 回連続拒否で部分 redact にフォールバック（ループ回避） |
| `logs_query(pattern, since, until)` | — | 構造フィールド（user.*, ip, email…）除去後 ≤200 行 | egress ガード後 |
| `fixture_from(pseudo_id)` | 擬似 ID | cwd にフィクスチャ生成 | PII 列**と健康値列**を Faker/摂動で置換、型・null 性・文字数・Unicode 正規化形のみ保持 |
| `read_masked(path, limit)` | `~/PII/inbox` のファイル | 擬似化済みの先頭 N 行 | §4.8 の検出器（NER + 要配慮語彙ゲート込み） |
| `sentry_events(issue_id)` | Sentry issue ID | API 取得→ローカル擬似化したイベント要約 | Sentry コネクタ deny 中の代替（§4.5） |

- 全ツールの返却は **40,000 字未満**に切り詰め（50,000 字で退避される経路を発生させない）。`isError` 結果は「マスク済みの非エラー結果」に変換（PostToolUseFailure は書き換え不能）。
- 符号化出力ゲート: base64/16 進の長い連続・高エントロピー行は `[ENCODED_BLOB_WITHHELD]` に置換。
- 実行ユーザー（**sudo/管理者権限がある場合**）: `~/PII` は別 macOS ユーザー（例 `safedata`）所有 0700、safe-data と pii-guard は LaunchDaemon でそのユーザーとして常駐、Claude Code からは sudoers 限定の stdio ラッパー / `127.0.0.1` HTTP で接続。辞書・vault も `safedata` 所有 0600。これで Read/Grep/@/ハードリンク/添付のいずれも OS が拒否し、deny ルールは二重化になる。**sudo が無い場合の縮退**: 同一ユーザーで動かし、`~/PII`・辞書・vault は `permissions.deny` + `sandbox.denyRead` のみで守る（MCP サーバ/フックはサンドボックス外で動くので、それらの実装自体が信頼境界になる）。
- `permissions.allow` に置くのは読み取り専用の `mcp__safe-data__schema_describe`・`mcp__safe-data__files_describe`・`mcp__safe-data__fixture_from` のみ。`py_run`/`sql_run`/`logs_query` は allow に入れず分類器の審査に通す（任意コード実行を Auto の安全網から外さない。+1〜3 秒、プロンプトは増えない）。
- **Docker Desktop が使えない場合**: `py_run` は `sandbox-exec` プロファイル（Seatbelt）か別ユーザー `safedata` での直接実行に縮退（ネットワーク遮断は pf/ユーザー単位ルールで代替、未検証）。

### 4.3 DB 側（第一防衛線）

PostgreSQL はテーブル GRANT を列 REVOKE で狭められない（https://www.postgresql.org/docs/current/sql-grant.html ）ので「別スキーマの PII 無しビュー + 専用ロール」。擬似 ID の鍵は `current_setting()` に置かない（ビュー内関数は閲覧者権限で実行され、`claude_ro` が `sql_run` で鍵を読める）。`SECURITY DEFINER` 関数の `search_path` は固定し、pgcrypto の関数は**スキーマ修飾**する（pgcrypto は通常 `public` または拡張専用スキーマ `extensions` にインストールされ、`pg_catalog, keys` だけの search_path では解決できない。https://www.postgresql.org/docs/current/sql-createfunction.html の「Writing SECURITY DEFINER Functions Safely」、https://www.postgresql.org/docs/current/pgcrypto.html ）。

```sql
CREATE ROLE claude_ro LOGIN NOINHERIT;
REVOKE ALL ON SCHEMA public FROM claude_ro, PUBLIC;             -- 全スキーマで実施
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER ROLE claude_ro SET default_transaction_read_only = on;    -- ロール既定値。セッション内 SET で解除可能なので「書込み防止」の本線は
ALTER ROLE claude_ro SET statement_timeout = '30s';             --   SELECT 以外の GRANT を一切与えないこと。sql_run は BEGIN READ ONLY でもラップ

CREATE SCHEMA claude;  CREATE SCHEMA keys;                       -- keys は claude_ro から USAGE 不可
CREATE TABLE keys.pseudo(k text);                                -- HMAC 鍵（所有者のみ SELECT）
-- pgcrypto が public にある場合（extensions スキーマなら extensions.hmac と SET search_path = pg_catalog, keys, extensions）
CREATE FUNCTION claude.pseudo_id(id bigint) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, keys
  AS $$ SELECT pg_catalog.encode(public.hmac(id::text, (SELECT k FROM keys.pseudo LIMIT 1), 'sha256'), 'hex') $$;
REVOKE ALL ON FUNCTION claude.pseudo_id(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claude.pseudo_id(bigint) TO claude_ro;
-- （代替: ETL で users.pseudo_id を物理列として事前計算し、ビューはその列だけ参照。関数の search_path 問題を避けられる）

CREATE VIEW claude.users_safe AS
  SELECT claude.pseudo_id(u.id) AS user_pseudo_id, u.app_version, u.plan,
         left(u.prefecture_code, 1) AS region_block,             -- 地域ブロックまで
         extract(year FROM u.birth_date)::int AS birth_year,      -- 年まで
         date_trunc('day', u.created_at)::date AS created_day
  FROM public.users u;
CREATE VIEW claude.tickets_safe AS
  SELECT t.id AS ticket_id, claude.pseudo_id(t.user_id) AS user_pseudo_id,
         t.category, t.product, t.status, t.created_at::date AS created_day,
         t.health_flag                                             -- support-intake が付与。本文列はビューに含めない（§4.4）
  FROM public.support_tickets t;
-- 健康イベント（要配慮）は個票ビューを作らない。集計専用関数/ビュー（GROUP BY 必須, n<11 抑止）のみ。
GRANT USAGE ON SCHEMA claude TO claude_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA claude TO claude_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA claude GRANT SELECT ON TABLES TO claude_ro;
```

- 同じ HMAC 鍵で Sentry SDK の `user.id`、support-intake のチケット、ログの user_id を擬似化し、**ソース横断で同一人物が同じ擬似 ID**になるようにする（突合タスクが成立する）。
- 開発 DB は PostgreSQL Anonymizer の動的マスキング（`SECURITY LABEL FOR anon ON ROLE claude_ro IS 'MASKED'`）か Faker シード（https://postgresql-anonymizer.readthedocs.io/en/latest/ ）。本番スナップショットを localhost に置かない（`allowLocalBinding:true` の前提条件）。
- MongoDB は read-only ユーザー + `$project` 固定の `db.createView`。MySQL は列を除いた VIEW + `SELECT` のみの専用ユーザー。BigQuery は authorized view + 承認済みデータセット。**PostgreSQL 以外の場合は「ETL で安全化レプリカ（PII 列除去・擬似化）を生成し、safe-data はレプリカだけに繋ぐ」が最も設計差分が小さい**（DP17）。CSV/スプレッドシートは ETL が PII 列除去/擬似化して `./data/safe/*.tsv`（`.csv` で終わらない名前）に出力。

### 4.4 support-intake（Gmail/Slack の代替経路）と vault

- 取込: Gmail は **support@ アカウントの 3-legged OAuth**（ドメイン全体委任はメールボックス単位に絞れない。https://knowledge.workspace.google.com/admin/apps/domain-wide-delegation-best-practices ）。Google Workspace 管理者による内部 OAuth アプリ承認が必要（DP20）。Slack は bot トークン + チャンネル許可リスト。
- 擬似化は構造優先: From/To/Cc/署名ブロック/件名の「◯◯様」/既知顧客辞書（DB から毎晩生成）を決定的に置換 → 残りを規則 + 小型 NER（§4.8）。**取込時に From/署名から抽出した氏名・メール・電話は即時に辞書へ登録**（夜間再生成を待たない。新規問い合わせ者は辞書外＝最も漏れやすい層なので、この即時登録が実運用の要）。`body_masked` は **DB の `claude` スキーマには載せず** support-intake の vault 側 DB に置く。対応表は暗号化 SQLite（鍵は Keychain、`safedata` ユーザー所有）。
- **要配慮語彙ゲート**（§4.8 層 2.5）: 本文に MEDIS 標準病名・医薬品一般名・検査項目・「数値+単位（mmHg, mg/dL, kg, HbA1c, %）」・服薬/症状の定型表現が含まれる文は `[HEALTH_CONTENT_WITHHELD]` に置換し、チケットに `health_flag:true` を付与。
- プレースホルダは**人物スコープ**（`[P12_NAME]` `[P12_EMAIL]` `[P12_PHONE]`）で既知個人を横断リンク、未知個人のみ型別連番。敬称・役割語は外側に残す（`[P12_NAME]様`）。日付は DOB のみマスク、イベント/チケットの時刻はシフトしない。
- ツール（アンダースコア名）: `tickets_list` / `tickets_get` / `tickets_set_category` / `reply_draft(ticket_id, template_id, slots)` / `tickets_get_body_masked`。
  - **既定（DP5 で「見せない」）**: `tickets_get` は構造化フィールドのみ返す — `ticket_id`、`user_pseudo_id`、`product`、`app_version`、`category`（取込時にローカルの決定的ルール分類: 管理語彙「ログインできない／通知が来ない／データ同期／課金／解約／その他」のキーワード辞書。小型ローカル分類モデルは精度未検証のため任意）、`created_day`、`health_flag`、`intent_summary`（管理語彙の組合せで生成する定型要約、自由文は含めない）。`reply_draft` はテンプレート ID とスロット（擬似 ID・製品名・バージョン）で下書きを作り、**復元は MCP プロセス内のみ**（vault → Gmail API で下書き作成、送信は人間）。復元後本文に辞書/規則で「復元以外の実値」が混入していたら拒否。戻り値は `{draft_id, status}` だけ。
  - **「見せる」を選ぶ場合（法務受容が前提）**: `tickets_get_body_masked` を `permissions.deny` から外す。返るのは擬似化済み・要配慮語彙ゲート通過後の本文。`health_flag:true` のチケットは本文を返さず「集計ツールか人間対応へ」と案内。
- **PreToolUse `updatedInput` での Write/Edit への復元はしない**: Edit の結果が編集後スニペットを返し得る、コンパクション直後に最近更新ファイルが最大 5 件再読込される（https://code.claude.com/docs/en/context-window#what-survives-compaction ）、復元ファイルを Claude が Read する、という 3 経路で実値がモデルに戻る。人間が叩く `pii-restore` CLI で `./out-real/`（Read deny）に出力する。
- vault はセッション単位でなく端末（ユーザー）単位、ID は非再利用（連番だと別セッションで別人に写る）。復元イベントは監査ログ。対応表は「削除情報等」相当として 0600・保持期限・アクセス記録（法的性格付けは法務判断）。

### 4.5 Sentry / Slack / コネクタ全般

- Sentry: Organization Settings > Security & Privacy の Data Scrubber を組織レベルで ON（プロジェクト上書き防止。組織管理者権限が必要、DP20）、Additional Sensitive Fields に `name, kana, email, phone, address, birth_date, patient_id, user.email, user.username…`、Advanced Data Scrubbing に電話/〒/患者 ID 正規表現。SDK は JS v10/v11 で `sendDefaultPii` が非推奨化され `dataCollection` 既定が userInfo/httpBodies を**収集する**方向なので、`dataCollection: { userInfo:false, cookies:false, httpHeaders:{request:false,response:false}, httpBodies:{incoming:'none',outgoing:'none'}, urlQueryParams:false }` を明示 + `beforeSend` で `user`/`request` を削除（https://docs.sentry.io/platforms/javascript/configuration/options/ ）。スクラブは取り込み時適用で**既存イベントに遡及しない**（未確認・性質上）ため、**有効化後 90 日はサーバ名ごと `mcp__<sentry-uuid>` を deny**（本環境の実在ツール `search_errors`/`search_issues`/`search_traces`/`search_profiles`/`search_logs`/`search_replays`/`analyze_issue_with_seer`/`execute_sentry_tool`/`get_sentry_resource` を漏れなく止める）し、`safe-data.sentry_events`（API + ローカル擬似化）を使う。90 日後に戻す場合も Replay/Logs/Profiles/`execute_sentry_tool`（汎用実行）はツール単位で恒久 deny。
- Slack: `slack-safe` stdio MCP（bot トークン、チャンネル許可リスト、擬似化、40k 上限）に全面置換。claude.ai Slack コネクタは切断（Slack を読む既存 Skill は `slack-safe` の読取ツールに差し替え、DP9）。
- Calendar / Docs / Figma: 業務上不要なら切断。Claude Docs コネクタは「社内文書本文の読取」と「Anthropic ホスト Docs への書込み」の双方向経路なので、切断か `mcp__<docs-uuid>` deny。Figma は PII 非経路なので残して良いが、`get_screenshot` の画像は PostToolUse で `type:"image"` ブロックを `{type:"text", text:"[image withheld]"}` に置換するか（MCP 出力は形検証なし）、モックに実データ風氏名を入れない運用。
- AWS: `aws___run_script`/`get_presigned_url` は deny。残す AWS MCP には IAM で §4.1 のデータプレーン Deny を付けた専用ロールのみ（プラグインがどの資格情報を使うかは未確認）。
- **MCP 集合の固定（MDM/managed-settings を配布できる場合のみ）**: `managedMcpServers` は **remote `http`/`sse` の `https://` URL 専用**で `command`/`args`/`env` を持てない（stdio の `safe-data`/`support-intake`/`slack-safe` は配布不可）。stdio の自作サーバを組織的に固定するには (i) `managed-mcp.json`（排他制御。stdio 可。ただしプラグイン MCP（Figma/GitKraken/AWS）とユーザー追加サーバは全部消え、claude.ai コネクタも `allowAllClaudeAiMcps` を置かない限り消える）か、(ii) user/project の `.mcp.json` に自作サーバを置き、managed に `allowedMcpServers`（`serverCommand` で**完全一致**列挙。`serverName` は security control ではない）+ `allowManagedMcpServersOnly: true`（**`allowedMcpServers` と組でないと何も制限しない**）を置く。`strictPluginOnlyCustomization` に `mcp` を**入れない**（入れると safe-data 自身が読み込まれない）。in-process `sdk` 型（Desktop のコネクタ・内部ツール）は allow/deny リストの対象外。https://code.claude.com/docs/en/managed-mcp 、https://code.claude.com/docs/en/settings-reference#allowmanagedmcpserversonly 。**MDM が無い単独運用では MCP 集合の固定は不可**: `deniedMcpServers`（Any file、`serverUrl`/`serverCommand` で）と `permissions.deny` で縮退する。
- リモート MCP で local OAuth が可能なものは `mcp-remote` をサニタイズラッパー配下に連結できる（Gmail/Calendar/M365 は不可）。

### 4.6 pii-guard（最後の防衛線）: フック定義と入出力

launchd 常駐（`127.0.0.1:8787`、外部通信なし、ワーカープール。sudo があれば `safedata` ユーザーの LaunchDaemon、無ければログインユーザーの LaunchAgent）。`http` 型フック（プロセス起動コスト無し。https://code.claude.com/docs/en/hooks#http-hook-fields ）。

```jsonc
"hooks": {
  "PreToolUse": [
    { "matcher": "mcp__.*",   // 残置コネクタ MCP だけを守るヘルスゲート（safe-data/support-intake/slack-safe は常に通す）
      "hooks": [ { "type": "command", "command": "/usr/local/bin/pii-guard-gate || exit 2", "timeout": 3 } ] },
    { "matcher": "Bash|Read|WebFetch|mcp__.*",
      "hooks": [ { "type": "http", "url": "http://127.0.0.1:8787/hook/pre", "timeout": 5 } ] }
  ],
  "PostToolUse": [
    { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8787/hook/post", "timeout": 30 } ] }   // matcher 省略＝全ツール
  ],
  "PostToolUseFailure": [
    { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8787/hook/post-failure", "timeout": 10 } ] }  // 観測のみ（書換不可）
  ],
  "PostToolBatch": [
    { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8787/hook/batch", "timeout": 10 } ] }
  ],
  "UserPromptSubmit": [
    { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8787/hook/prompt", "timeout": 3 } ] }
  ],
  "SessionStart": [
    { "hooks": [ { "type": "command", "command": "/usr/local/bin/pii-guard-sessionstart", "timeout": 10 } ] }
  ]
}
```

**PreToolUse `/hook/pre`（deny のみ。`updatedInput` は返さない＝複数フックの「最後に終わった方が勝つ」非決定性とサブシェル由来プロンプトを回避）**

```json
// 入力（抜粋）: {"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"psql -c \"select * from users where name='山田花子'\""},"permission_mode":"auto",...}
// 出力: 引数に PII リテラル（辞書フル一致・患者ID・電話・メール・文脈付マイナンバー）→ deny（引数は Auto 分類器と転記に載る）
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",
  "permissionDecisionReason":"コマンド引数に個人情報（NAME×1）が含まれます。値ではなく safe-data の user_pseudo_id で指定してください。"}}
// WebFetch（方針は allowlist）: ホストが公開ドキュメント allowlist（docs.* / developer.* / *.readthedocs.io / registry.npmjs.org / pypi.org /
//   github.com の Issue・PR 以外のパス / learn.microsoft.com / docs.aws.amazon.com / code.claude.com 等）の外 → deny
//   （理由に safe-data.read_masked / 社内 docs エクスポートを案内）。settings の WebFetch(domain:) deny はデーモン停止時のバックストップ。
// Read: file_path が画像/PDF で ./docs/approved/** 外 → deny（画像は検査不能）
// 判定なし: 出力なし・exit 0
```

**PostToolUse `/hook/post`（全ツール。受け取った `tool_response` を深くコピーし、文字列リーフだけ再帰置換して同じ形で返す。未知フィールドは保持）**

```json
// Bash（公式の形: stdout/stderr/interrupted/isImage。isImage:true は触らない）
{"hookSpecificOutput":{"hookEventName":"PostToolUse",
  "updatedToolOutput":{"stdout":"user=[P12_NAME] tel=[P12_PHONE] status=active\n","stderr":"","interrupted":false,"isImage":false},
  "classifierContext":"Output was pseudonymized locally by pii-guard; placeholders like [P12_NAME] are expected."}}
// MCP（検証なしで置換される。content[].text / structuredContent を同形で）
{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":{"content":[{"type":"text","text":"...[P7_NAME]様より [P7_EMAIL] で問い合わせ..."}]}}}
// Read / Grep / Glob / WebFetch / Edit / Write / NotebookEdit / Agent / AskUserQuestion / Skill / TaskOutput:
//   形は未文書化 → Day 0 で記録した tool_response の形をそのまま保って返す。形不一致は黙って無視され原文が送られるため、
//   各リリースで契約テスト（§4.9）。pii-guard-sessionstart が未検証版を検知したら、これらは「辞書フル一致 + 患者 ID のみ」に縮退。
```
ルーティング（誤検出が要件 B を壊すのを防ぐ）:
- cwd 配下のコード/設定ファイルを見る Bash（`cat/head/tail/sed -n/grep/rg/ls/find`）と cwd 内 Read / Edit / Write / NotebookEdit / Skill（SKILL.md 本文）: **患者 ID スキーマ + 辞書フル一致（メール/電話/ID）のみ**。NER・単独姓・12 桁規則は掛けない（Edit の `old_string` 完全一致が壊れて再読ループになる。https://code.claude.com/docs/en/tools-reference#edit-tool-behavior ）。cwd は層 2〜4 で PII 不在が前提。
- データ経路（`mcp__<connector>__*`、`./data/**`、`~/PII` 由来、`psql` 等、`Agent` の最終報告、`TaskOutput`）: 規則 + 辞書 + NER + 要配慮語彙ゲートのフルセット（フィクスチャの Faker 値は allowlist）。
- `AskUserQuestion` の自由記述回答: プロンプトと同じ高確信ルール（辞書フル一致・患者 ID・文脈付マイナンバー）で置換（この経路は UserPromptSubmit と異なり書換が可能。形は**未確認**）。
- `stdout` に「saved to: <path>」（退避ファイル）を検出したら、そのファイルを**その場でスクラブして書き戻す**（フックはサンドボックス外）。以後の Read はどの形でも擬似化済みを読む。
- 予算: 入力サイズで階層化、NER は 8 KB 以下または先頭 N チャンク。予算超過時は「規則+辞書のみ適用 + `[pii-guard: NER 未適用]` 注記」を**タイムアウト前に必ず返す**（原文は返さない）。OTel のツール内容ログ（`OTEL_LOG_TOOL_CONTENT` 等）は設定しない（原文が記録される）。

**PostToolUseFailure `/hook/post-failure`（書き換え不能。検知→隔離マーカー）**: 非ゼロ終了 Bash の stdout/stderr（最大約 10,000 字）や MCP の `isError` 結果はそのまま送られる。デーモンは `tool_response` を走査し、辞書フル一致/患者 ID を検知したら `~/.claude/pii-vault/tainted/<session_id>` を書く（次項の PostToolBatch と同じ扱い）。**Bash 全体を `pii-wrap -- <cmd>` で包む案（PreToolUse `updatedInput`）は採らない**: 包むとコマンドが文字列引数になり、Read deny が `cat ~/PII/x` 等のファイル引数を認識できなくなる（permission rules は書換後の入力で評価される）、`blockReads` 下のサブシェル判定でプロンプトが出る、分類器に見えるコマンドが変わる、という安全側の退行があるため。失敗経路の本線は「サンドボックスで Bash が PII に到達できない構造 + cwd 衛生 + safe-data の isError 変換」。

**UserPromptSubmit `/hook/prompt`（書き換え不可なので高確信のみ block）**

```json
{"decision":"block",
 "reason":"個人情報（顧客辞書に一致する氏名 1 件・患者ID 1 件）を検出したため送信を止めました。該当データは ~/PII/inbox/ に置き、safe-data 経由で参照してください。",
 "hookSpecificOutput":{"hookEventName":"UserPromptSubmit","suppressOriginalPrompt":true}}
```
- block 条件: 辞書フル一致 / 患者 ID スキーマ / 文脈付マイナンバー / 異なる 2 種以上の PII 型。単独のメール・電話・7〜12 桁数字は block せず `additionalContext` で注意のみ（`@example.co.jp（自社ドメイン）`、会社代表番号、注文番号を誤 block しない）。
- 画像を含むプロンプトは画像を検査できないため block したいが、`prompt` フィールドに `[Image #N]` 等のマーカーが入るかは**未確認**（Day 0 で実測。入らなければ「PII が映る画像を貼らない」運用 + Desktop では PII 近傍作業を行わない（DP3））。
- UserPromptSubmit は**サブエージェントの報告・スケジュール・他セッションからのメッセージなど Claude Code 自身が始めるターンでも発火**する（https://code.claude.com/docs/en/hooks#userpromptsubmit ）。起源を判別する入力フィールドは未確認なので、報告エンベロープらしき定型を検出したら block せず注記に落とす。
- 汚染マーカーがあるセッションでは全プロンプトを block し、理由文に解除手順（下記）を書く。
- 補助: `pbpaste | pii-mask | pbcopy` を人間用ショートカットとして配布（Claude の Bash からは `pbcopy` がサンドボックス内でクリップボードに届かないことが公式に記載されており、deny とも整合）。

**PostToolBatch `/hook/batch`（検知 + 隔離。キルスイッチではない）**

```json
{"decision":"block","reason":"ツール結果に個人情報（PATIENT_ID×2）が残っています。この結果は会話に残るため、Esc Esc で直前のチェックポイントへ戻すか /clear してください。解除: /clear（SessionStart source=clear で自動解除）または `pii-guard untaint <session_id>`。"}
```
`block` は次のモデル呼び出しを止めるだけで、結果は会話に残り次の発話で送られる（https://code.claude.com/docs/en/hooks#posttoolbatch-decision-control ）。同時に `~/.claude/pii-vault/tainted/<session_id>` を書き、UserPromptSubmit で後続を止める。判定は辞書フル一致と患者 ID のみ（フィクスチャの Faker 値で止まらないように）。失敗結果（PostToolUseFailure 経路）も直列化された `tool_result` に含まれるので同じ走査対象。

**汚染マーカーのライフサイクル**: SessionStart の `source` が `clear` のとき削除（`/clear` 後の `session_id` が同一か変わるかは未確認なので、マーカーは `session_id` と転記ファイルパスの両方で引く）、`resume`/`fork` のときは転記 JSONL の末尾 N 件に該当 `tool_use_id` が残っているか再評価して残っていれば維持。rewind にはフックイベントが無いため、デーモンが転記 JSONL をファイル監視し、該当 `tool_use_id` が末尾から消えたら自動解除。手動解除 `pii-guard untaint <session_id>`。https://code.claude.com/docs/en/hooks#sessionstart

**gate / SessionStart**
- `pii-guard-gate`: `/healthz` が 1 秒以内に返らなければ `launchctl kickstart` を 1 回試み、再確認後に deny（対象は残置コネクタ MCP のみ。Bash/Read は層 2 で守られているので止めない）。`|| exit 2` でバイナリ不在時も PreToolUse がブロックされる（exit 2 は PreToolUse をブロック。https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event ）。チーム配布時は `~/.claude/settings.json` の `sandbox.excludedCommands` を検査し、承認外パターンがあれば Bash を deny。
- SessionStart: Claude Code バージョンを読み、未検証版なら「MCP の PostToolUse のみ有効・組み込みツールの形依存マスクは辞書+患者 ID に縮退」に自動縮退して `systemMessage` で通知。`requiredMaximumVersion` は Desktop 同梱 Claude Code の更新で起動拒否になり得るので使わず、`requiredMinimumVersion`（Managed のみ。Phase 0/1 は `"2.1.285"`、Phase 3 Bedrock は `"2.1.287"`。https://code.claude.com/docs/en/settings-reference#requiredminimumversion ）+ 夜間契約テストで運用。MDM が無い場合は Day 0 の `claude --version` と `claude doctor` を記録。
- フック応答の全文字列（`reason` 等）は返却前に同じ検出器で自己走査し、値をエコーしない。

### 4.7 プロキシ + vault の要否

- **マスキング目的のプロキシは不要・非推奨**（§2）。vault は support-intake / safe-data の MCP プロセス内に閉じる。
- **監査目的**は Claude Code 自身の `OTEL_LOG_RAW_API_BODIES=file:<暗号化ディスク上の dir>`（Messages API のリクエスト/レスポンス本文を無改変でローカル保存。`index.jsonl` で転記メッセージと紐付け可。shell/user/managed 設定からのみ有効、thinking は常に redact。https://code.claude.com/docs/en/monitoring-usage#api-request-body-event ）を使い、夜間に同じ検出器 + カナリア値で DLP スキャン → 「送らなかった」ことの証明と FN 率の実測。**OTel エクスポータ未設定（`CLAUDE_CODE_ENABLE_TELEMETRY` 無し）でも file モードの書き出しが行われるかは未確認**（Day 0 に実測。必要ならローカル OTLP コレクタ `127.0.0.1` を立てる）。保全ファイルは PII の複製先になるので 0700・7 日保持。
- 受動ゲートウェイ（本文不改変・SSE 逐次中継）は、チーム配布で `allowedProviders:["customEndpoint"]` による経路固定が必要になった時のみ。独自 BASE_URL 時は `ENABLE_TOOL_SEARCH=true` を明示（https://code.claude.com/docs/en/env-vars ）。

### 4.8 日本語 PII 検出の実装方針（規則 + 辞書を床、NER は上積み）

| 層 | 内容 | 精度/注意 |
|---|---|---|
| 0 正規化・復号 | NFKC、全角/ハイフン/空白バリアント、**異体字・旧字体の等価クラス表**（NFKC では正規化されない: 髙↔高、﨑↔崎、齋↔斎↔齊↔斉、邊↔邉↔辺、澁↔渋、國↔国、廣↔広、嶋↔島、櫻↔桜、眞↔真、濵↔濱↔浜、萬↔万、冨↔富、德↔徳、瀨↔瀬、龍↔竜、惠↔恵、條↔条、彌↔弥、禮↔礼 — 辞書側とテキスト側の両方を同じ代表字に写像）、**ローマ字正規化**（ヘボン/訓令: shi↔si, chi↔ti, tsu↔tu, fu↔hu, ji↔zi, sha↔sya, cho↔tyo、長音 ou↔o↔oh↔ō、大文字小文字、`Last First`/`First Last`/`LAST First` を辞書生成時に全展開）、JSON `\uXXXX`・`%xx`・QP（`=E7=94=B0`）・HTML エンティティの復号後に再走査、base64/16 進の長い塊は復号試行→不能なら `[ENCODED_BLOB_WITHHELD]`、CP932 再デコード | 符号化迂回（Python `json.dumps` 既定、URL ログ、MIME）と表記揺れを塞ぐ |
| 1 スキーマ/キー文脈 | JSON/CSV/`key=value`/SQL ヘッダのキー名（name/氏名/kana/tel/phone/電話/mail/birth/dob/生年月日/address/住所/patient/member/user_id/device_id）に対応する値を型ごとに無条件マスク。数値電話は先頭 0 補完して JP 検証 | 構造化データで「保証」を機械的に裏付ける唯一の層 |
| 2 決定的規則（文脈ゲート必須） | 電話: python-phonenumbers region=JP（0ABJ, 060/070/080/090, 050, 0120/0800, 0570, +81。https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html ）／メール（`@example.co.jp（自社ドメイン）`・`example.*` 除外）／〒: 住所文脈時のみ／**住所: 都道府県アンカー正規表現** `(北海道|東京都|京都府|大阪府|[一-龥]{2,3}県)[一-龥ぁ-んァ-ヶ]{1,10}(市|区|町|村|郡)` に続く `[一-龥ぁ-んァ-ヶ]{0,12}([0-9０-９一二三四五六七八九十]{1,4}(丁目|番地|番|号|－|-)){1,3}` を、住所文脈語（住所|所在地|お届け先|ご住所|〒）近接 **または** 3 階層以上の一致で採用、建物名は後続 20 字をオプションで含める（都道府県名単独・市名単独は採らない）／マイナンバー: 12 桁 **かつ**（マイナンバー｜個人番号 の近接 or 検査数字一致。https://laws.e-gov.go.jp/law/426M60000008085 ）**かつ** `arn:`/URI/16 進内でない（AWS アカウント ID は常に 12 桁）／旅券 `[A-Z]{2}[0-9]{7}` + 文脈語／自社 患者 ID・会員 ID スキーマ（prefix+桁+CD → ≈100%）／保険者番号・記号・番号・枝番: 文脈語必須／**DOB: 西暦 `[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日`・`YYYY-MM-DD`・`YYYY/MM/DD` と和暦 `(令和|平成|昭和|大正|明治|[RHSTM])[ 　]?(元|[0-9０-９]{1,2})年[0-9０-９]{1,2}月[0-9０-９]{1,2}日` を、`生年月日|生まれ|誕生日|DOB` 近接のみ**（他の日付は残す）／法人番号 13 桁は除外（mod-9。https://www.houjin-bangou.nta.go.jp/documents/checkdigit.pdf ）／法人格（株式会社・(株)・合同会社）・部署名（部・課・室）直結の語は人名候補から除外 | 「記録するがゲートしない」はログにのみ。住所の正規化・検証は geolonia/normalize-japanese-addresses をローカルデータで（既定はネットワーク取得なので file:// 配備。https://github.com/geolonia/normalize-japanese-addresses ） |
| 2.5 要配慮語彙ゲート（データ経路のみ） | MEDIS 標準病名マスター（病名・修飾語）、医薬品一般名/販売名、検査項目名（JLAC10 系の項目名）、「数値 + 単位（mmHg/mg/dL/kg/kcal/%/HbA1c/BMI）」、症状・服薬の定型表現（飲み忘れ/副作用/発作/受診）に一致する**文単位**を `[HEALTH_CONTENT_WITHHELD]` に置換し `health_flag` を立てる。ヒットした結果は「集計ツール（safe-data）か人間対応へ」の注記を付ける（DP5 と連動）。マスターは医療情報システム開発センター（https://www.medis.or.jp/ 、配布ページは導入時に確認）から取得し版固定 | 自社製品名・一般語（「痛い」単独等）は allowlist。精度は未測定（**未検証**） |
| 3 既知エンティティ辞書 | 自社 DB から毎晩生成 + **support-intake 取込時の即時追加**（氏名 漢字/かな/ローマ字、メール、電話、患者 ID、Slack ID）を Aho-Corasick。**フルネームのみ**（姓名連結 3 字以上、ローマ字は `\b` 境界 + 順序両方）。単独姓・単独名・2 字以下・かな 3 字未満は登録しない（「原則」「関数」「森林」「editor」が壊れる）。辞書ファイルは `safedata` ユーザー所有 0600・sandbox denyRead | 自社が保有する個人に対しては再現率 ≈100%（異体字・ローマ字は層 0 の等価クラスで吸収） |
| 4 小型日本語 PII NER（データ経路のみ） | Presidio（data-privacy-stack 2.2.364）の `GLiNERRecognizer(model='DataSign/gliner-ja-pii-v1', supported_language='ja', chunk_size≈120)` または `HuggingFaceNerRecognizer` + `NagaYu/sumi-ja-pii` INT8 ONNX。`PhoneRecognizer(supported_regions=['JP'])`。ラベル NAME/ADDRESS/DOB。閾値は「誤検出予算 ≤2% 固定での再現率」で決定。allowlist: 都道府県名・自社製品・取引先社名・自社公開連絡先・Faker フィクスチャ値 | 2026 年公開・自己申告の合成データ評価のみ（gliner-ja-pii 160 トークン上限、Sumi 名前再現率 0.81）→ **未検証**として扱い、版固定・隔離 venv、退行時は層 1〜3 に戻せる構成。https://huggingface.co/DataSign/gliner-ja-pii-v1 、https://huggingface.co/NagaYu/sumi-ja-pii |
| 5 出力 | 人物スコープ `[P12_NAME]`（既知）/ 型別連番 `[NAME_3]`（未知）、敬称は外側、ID prefix は保持（`PT-[P12_ID]`）、集計の時刻はシフトしない | 可逆・一意性保持は黒塗りより性能劣化が小さい（https://arxiv.org/abs/2609.11335 ）。検索/照合は劣化するので safe-data 側のツールに寄せる |

評価: Faker ja_JP + 自社テンプレ（問い合わせメール/Slack/Sentry/CSV/psql 出力）2,000 件 + 人手注釈の実データ 300 件（社外に出さない）+ **陰性コーパス**（コード・ログ・AWS 出力・i18n・git log）+ **表記揺れコーパス**（異体字・和暦・ローマ字順序違い）。指標は型別再現率・文書レベル漏れ率・誤検出/1,000 行。NER 層の CPU 予算 ≤200 ms/文書、4B 級ローカル LLM は 7 秒/文書・名前再現率 0.3〜0.5 なのでホットパスに入れない。

### 4.9 運用（カナリア・回帰・辞書）と受け入れチェックリスト

**Day 0 チェックリスト（全項目 Yes で Phase 0 へ）**
1. `/status` でプラン・ログイン種別・Setting sources を記録。Pro/Max なら「Help improve Claude」OFF を確認。
2. `/model` で Covered Model かを記録。`claude --version` ≥ 2.1.285（Phase 0/1）。
3. `/mcp` でコネクタ実名（`mcp__<uuid>`）を記録し、settings のプレースホルダを全置換。
4. スキル棚卸し表（`!`cmd`` 依存／Slack・Google API 依存／背景サブエージェント依存）を作成。
5. 偽 PII で in-process ツール deny（画面・ターミナル・転記検索）の有効性を実測。
6. `~/PII/inbox/fake.csv` の D&D と画像貼付で、転記への混入と `prompt` マーカーを実測。
7. Read/Grep/Glob/WebFetch/Edit の PostToolUse `tool_response` 形を記録。
8. Desktop ローカルセッションで sandbox 強制を実測（`curl https://example.invalid` 拒否、`claude doctor`）。
9. `claude purge` で導入前転記を削除。

**Phase 0 受け入れ試験（Auto モード、プロンプト 0 回で全て通ること）**
- Desktop 既定ワークスペース（`~/Library/Application Support/Claude/scratch-workspaces/…`）で Read → Edit → Write が成功。
- `echo $(git rev-parse HEAD)`、`cd a && cd b && ls`、`npm test`、`uv run pytest`、`pip install -r requirements.txt`、`npx playwright install`（`storage.googleapis.com` 到達）、`aws sts get-caller-identity`（`sts.*.amazonaws.com` 到達）、`npm run dev` → `curl http://127.0.0.1:3000`（`allowLocalBinding:true`）が通る。
- `cat ~/PII/inbox/fake.csv`、`python -c "open('~/PII/…')"`、`grep -r 田中 ~/PII`、`psql -h db.prod…`、`env | grep -i PGPASSWORD` が全て失敗（deny / sandbox / scrub）。
- `./data/safe/report.tsv` の Read と `./out/summary.csv` の Write が成功、`./data/raw/x.csv` の Read が deny。
- 「Saved 1 memory」が成功し `~/claude-memory/MEMORY.md` に書かれる。
- Figma `get_design_context`、GitKraken `git_status`、`gh pr checks` が動き、`gh pr view`・`issues_get_detail` が deny。

**Phase 1/2 契約テスト（CI、Claude Code 更新ごと）**: Bash 形の `updatedToolOutput` が反映される、MCP 置換、UserPromptSubmit block、gate の fail-closed、Read→Edit が成功、fixture 電話番号が転記で `[PHONE_n]`、`/compact` 後のカナリア不在、PostToolBatch block → `/clear` でマーカー解除、`sql_run` に `select *` を投げると拒否、`py_run` で辞書値を print すると egress 拒否、異体字（髙橋）・和暦 DOB・ローマ字逆順が辞書に一致。

- カナリア: 偽レコード（「田中カナリア」、090-0000-0001、PT-CANARY01、「髙橋カナリア」）を DB/テスト Slack/テストメールに常駐させ、夜間に `~/.claude/projects/**/*.jsonl`・`tool-results/`・`~/claude-memory/`・`OTEL_LOG_RAW_API_BODIES` 保全を `rg -f` で走査。1 件でも出たら境界が破れた証拠。必ず「Read させてから `/compact`」のケースを含める（コンパクション再読込がフックを通るかは未確認）。
- pre-commit: `CLAUDE.md`/`.claude/**`/`~/.claude/**`（skills/rules）/`~/claude-memory/**`/fixtures を同じ検出器で走査、`nbstripout` 必須（`./notebooks/raw/**` 以外のノートブックは出力を落としてコミット）、gitleaks に日本語 PII ルール。CLAUDE.md の `@` インポートは禁止（起動時に文脈へ展開され分類器にも送られる）。
- 辞書は毎晩再生成 + 取込時即時追加、NER は四半期で再評価、ビュー列・IAM・許可ドメイン・`excludedCommands` の棚卸しは四半期。

---

## 5. Auto モード体験への影響と緩和

| 摩擦 | 原因（確認済み） | 緩和 |
|---|---|---|
| deny 後に Claude が 1〜2 回迂回して失敗する | Read/Bash deny は無言 | deny 理由に代替経路（`safe-data.*`/`./data/safe`）を明記。CLAUDE.md に「データは safe-data 経由、個人は `[P12_…]` で参照、推測・復元しない」を 3 行 |
| **Desktop 既定ワークスペースが読み書き不能**（前版の `~/Library/**` deny） | Read deny は Edit/Write にも適用 | PII 実在パスのみ deny。`~/Library` を広く塞ぐなら `allowRead` で `Application Support/Claude`・`Caches`・`Python`・`pnpm`・`Developer` を再開放 |
| **ローカル dev サーバに listen/接続できず、Playwright webServer・supertest・`curl localhost` が失敗** | `allowLocalBinding:false`（`allowedDomains` の localhost は直接接続に効かない） | `allowLocalBinding:true` + 「PII を持つローカルサービスを同一マシンで動かさない」運用、または起動コマンドだけ `excludedCommands`。`preview_start` は deny しない |
| **`aws`/`cdk`/`terraform`/`gcloud`/Chromium 取得が永久に失敗** | `deniedDomains` は全モードで拒否・コマンド単位承認でも開かない | ワイルドカードをやめデータプレーン実ホストのみ deny。境界は IAM |
| **CSV/ノートブックを作成・編集できない、安全化 CSV が読めない** | 拡張子 deny は Write/Edit/NotebookEdit も止める、`*.safe.csv` は `*.csv` に一致 | ディレクトリ単位 deny（`./data/raw`、`./exports`、`./notebooks/raw`）、安全化出力は `.tsv`/`.safecsv` |
| **「Saved memories」が毎回失敗** | `~/.claude/projects/**` deny が `memory/` を巻き込む | deny を `**/*.jsonl` と `**/tool-results/**` に絞る、または `autoMemoryDirectory` 移設 |
| `docker` コマンドが失敗 | docker はサンドボックス非互換（公式） | `excludedCommands: ["docker compose *"]` を最小限。`py_run` は MCP プロセスから起動 |
| サブシェル/複数 cd を含むコマンドが Auto でもプロンプト | `blockReadsOutsideWorkingDirectories` 下でサンドボックス非強制時 | Phase 0 では blockReads を入れない。入れる時は `allowManagedReadPathsOnly` を置かずサンドボックスで強制、`additionalDirectories` に共有リポジトリ |
| 許可外ホストへの接続が永久に失敗（prisma/playwright/brew） | `strictAllowlist`/`allowManagedDomainsOnly` が Auto のコマンド単位ドメイン承認を無効化 | 使わない。PII 源 SaaS/DB は `deniedDomains`（実ホスト）で塞ぐ |
| SSH 経由の git push が失敗 | `~/.ssh` を denyRead した場合 | git を HTTPS + `gh auth` に寄せてから deny（DP16） |
| 既存スキルが止まる（Slack 読取系・Google API 系・背景サブエージェント系・ブラウザ操作系の Skill） | Slack/Google コネクタ切断、`www.googleapis.com` deny、`disableSkillShellExecution`、`DISABLE_BACKGROUND_TASKS`、computer-use/Chrome deny | 棚卸し後に判断（DP9）。`slack-safe` へ差し替え、PII 非関連スキルは別 settings プロファイルで実行、背景タスクは止めず PostToolUse（全ツール）で守る |
| Read→Edit が `old_string not found` でループ | PostToolUse でソースをマスクすると実ファイルと乖離 | cwd 内コード/Edit/Write はマスク対象外（患者 ID/辞書フル一致のみ）。cwd を PII 不在に保つことが前提 |
| ID・識別子が壊れる（AWS アカウント ID、注文番号、trace_id、`東京都`→`[NAME]京都`） | 12 桁/10 桁規則、辞書の部分一致、住所規則の過剰一致 | 文脈ゲート、フルネーム限定、英数字トークン境界、都道府県単独は採らない、allowlist |
| 同僚メール・注文番号・サブエージェント報告が block される | UserPromptSubmit の過剰 block、報告でも発火 | 高確信のみ block、`@example.co.jp（自社ドメイン）` 除外、報告エンベロープは注記のみ |
| 大きな出力を読めず `tail` で再実行 | 退避ファイルの Read deny（Phase 0） | `bashOutputMaxChars:128000`、Phase 2 で退避ファイルの即時スクラブ |
| ツール呼び出しごとの遅延、並列時のタイムアウト→原文送信 | NER が CPU を取り合い 30 秒超 | ワーカープール、サイズ階層、予算内応答（原文を返さない）、cwd コードに NER を掛けない |
| デーモン停止で全部止まる | ヘルスゲートの対象が広すぎる | ゲート対象は残置コネクタ MCP のみ、自動 kickstart、SessionStart で通知 |
| `py_run`/`sql_run` が分類器往復で +1〜3 秒 | allow に入れない設計 | `autoMode.environment` に「`~/PII` は機微データ所在地、safe-data は社内の集計・擬似化ツールで返却値は非機微、#support-* は集計結果の許可宛先」を散文で登録。`classifierContext` で注記。`/auto-mode-setup` が使えるよう NONESSENTIAL_TRAFFIC は使わない |
| 擬似 ID や `[P12_NAME]` を含む Slack 投稿が `[Data Exfiltration]` でブロック、3 連続で Auto 一時停止 | 分類器の既定ルール | 上記 environment + `autoMode.allow`（`$defaults` を残す） |
| 問い合わせ本文が読めない（既定は構造化フィールドのみ） | 要配慮の内容を送らない設計 | `category`/`intent_summary`/`health_flag` で分類・テンプレ返信は完結。本文が必要なら DP5 で `tickets_get_body_masked` を開放 |
| 名寄せ・五十音順・表記揺れ判定の品質が落ちる | 擬似化の原理的限界 | 人物スコープ ID でソース横断リンク、ソート/重複/正規化は safe-data の決定的ツールに委譲 |
| Gmail/Drive コネクタが消える | 切断 | `tickets_get → tickets_set_category → reply_draft` の 3 呼び出しで完結。Drive は ETL で `./data/safe` へ |
| `/feedback` `/insights` `/resume`（旧セッション）`!` が使えない/禁止、Artifact が無い | 設定・運用 | 導入前に転記削除、`!` は手順書で禁止（無効化キーは未確認）、成果物は ファイル送付や社内の共有サービスで共有 |
| Desktop の computer use / Chrome 拡張 / Browser のページ読取が無い | deny / Desktop 管理キー | 公開ドキュメントは WebFetch（allowlist）で可。UI 確認は PII の無い Faker シード環境で `preview_start` + `autoVerify` を限定的に |
| 汚染マーカーで永久ブロック | 解除経路の欠落 | SessionStart `source=clear` で自動解除、転記監視で rewind 検知、`pii-guard untaint` |
| サーバ側分類器の no-verdict で拒否、10 連続で turn 停止 | ゲートウェイ/ネットワーク起因 | 本案はワイヤ不改変なので通常起きない。`PermissionDenied` フックで記録、頻発時は `CLAUDE_CODE_AUTO_MODE_SERVER=0` |

設計ルールの再確認: フックは `deny` か「決定なし」か「形を保った書換」のみ（`ask` 禁止）。org のコネクタ制御は `ask` ではなく `blocked`（`ask` は Desktop ローカルセッションには届かず、ターミナルでは毎回プロンプト）。PreToolUse の `allow` が分類器をスキップするかは文書化されていない（**未確認**）ので allow ルールは settings 側に置き、任意コード実行系は allow に入れない。

---

## 6. 残るリスク（閉じられない経路）と運用対策

| 経路 | 状態 | 対策 |
|---|---|---|
| ユーザーが手入力した辞書外の氏名・住所 | UserPromptSubmit は書換不可。規則/辞書に掛からない文字列はメインモデルと Auto 分類器に送られる | 手順書・研修・`pbpaste \| pii-mask \| pbcopy`。Fable では 1 件 = 30 日保持を周知 |
| 貼り付け画像・スクリーンショット・PDF（Read の page-range は画像化）、Finder からの D&D 添付 | 画像をフックで書き換える手段なし。`[Image #N]` マーカーの有無・D&D が Read を経由するかは**未確認** | Day 0 実測。マーカーが取れれば block、取れなければ Desktop で PII 近傍作業をしない（DP3）、`Read(./**/*.pdf)` deny、computer use/autoVerify OFF |
| 問い合わせ本文の未知個人（単漢字名・姓と同形の一般名詞・異体字） | NER 再現率 ≈0.8（自己申告） | 構造的擬似化 + 取込時即時辞書化で既知分を 100% にし、NER の対象を減らす。異体字等価クラス。FN 発生時は値を即辞書化・インシデント記録 |
| 要配慮個人情報の「内容」（病名・服薬・検査値 + 地域 + 年齢の準識別子） | 識別子を消しても残る | 個票ビューを作らない、集計のみ、n<11 抑止、本文は既定で返さない、語彙ゲート（精度未検証） |
| `py_run` の符号化出力（base64/文字分割）による egress 回避 | プロンプトインジェクションで誘導可能 | JSON 制約・8 KB・エントロピーゲート・要求ファイルのみマウント・辞書/vault を別ユーザー所有 |
| Desktop in-process ツール/コネクタへのフック発火と deny の有効性 | **未確認**（ツール名 `mcp__<uuid>__`、sdk 型は MCP allow/deny リストの対象外） | Day 0 に偽 PII で実測。効かなければ PII 近傍作業は CLI/VS Code |
| **Anthropic ホストへの書込み経路**（Artifact / Claude Docs / Drive create / Slack send / `/feedback`） | モデル入力ではないが PII を Anthropic 側または第三者に置く | `enableArtifact:false`、Docs コネクタ切断/deny、Drive 切断、Slack 送信は `slack-safe` 経由で egress ガード、`DISABLE_FEEDBACK_COMMAND` |
| 残す Anthropic ホストコネクタ（Figma 等）は生データが Anthropic のコネクタ基盤を経由 | モデル入力ではないが要件 A の境界問題 | 法務判断（委託/クラウド例外）。PII を含むものは自作 MCP に置換 |
| コンパクション直後の最大 5 ファイル再読込 | Read ツール経由（フック発火）か内部注入かは**未確認** | cwd に PII を置かない、カナリアで `/compact` テスト |
| フックの fail-open（タイムアウト・形不一致・バージョン更新） | 公式仕様 | 層 1〜4 が主役。SessionStart で未検証版は縮退。夜間 DLP で検知 |
| PostToolUseFailure（非ゼロ終了 Bash・MCP エラー結果） | 書き換え不能（観測専用） | サンドボックスで Bash が PII に到達できない構造 + cwd 衛生 + safe-data は isError を変換 + PostToolBatch/PostToolUseFailure の検知→隔離。`pii-wrap` 包み込みは安全側退行のため不採用（§4.6） |
| `!` シェルモード | サンドボックス外・フック外 | `respondToBashCommands:false`、禁止手順、UserPromptSubmit に `!` 出力が含まれるかは未確認 |
| `allowLocalBinding:true` による localhost 全ポート到達 | 認証無しローカルサービスが代理になり得る（公式注意） | PII を持つローカルサービス・本番スナップショットを同一マシンで動かさない（DP12）、必要なら (b) の excludedCommands 方式 |
| `excludedCommands`（docker 等）はフルアクセスで動く | 公式注意（compose ファイルを書いてから実行できる） | 最小パターン、チーム配布時は gate で検査 |
| クラウドセッション/Routines/Artifact/GitHub Actions/自社 SDK アプリ | ローカル統制の外 | org で OFF、`RemoteTrigger`/`move_to_cloud` deny、Artifact 無効化、CI は Bedrock jp. + 同じ settings を action の `settings` で配布、第一者 API キーの棚卸し |
| 分類器経路 | ユーザーメッセージ・コマンド引数・CLAUDE.md が審査対象、サーバ側 probe はツール結果も走査 | モデルが PII を持たなければ混入しない。PreToolUse で引数の PII リテラルを deny |
| ローカル平文転記・vault・辞書・OTel 保全・自動メモリ | API 送信ではない | FileVault、7 日保持、別ユーザー所有、`~/claude-memory` を DLP 対象、退職時 `claude purge` |
| 従業員 PII（cwd パス、git author、Slack 表示名、Desktop が注入するユーザーメール） | 構造的に毎セッション送信 | 要件 A のスコープを法務と明文化（顧客/患者/問い合わせ者 vs 従業員） |
| ビュー/ETL の設計漏れ（JSON 列・メモ列・`updated_by`） | 構造的制御の外 | スキーマ変更レビューに PII 列チェック、四半期棚卸し |
| Spotlight/クリップボード等の mach サービスをサンドボックスが許すか、ハードリンク | `pbcopy` 書込みが届かないことは公式記載、`pbpaste`/`mdfind` 読取側と `ln` は**未確認** | `mdfind/pbpaste/ln` を deny、`~/PII` を別ボリューム・別ユーザー所有 |

---

## 7. 補完策

**推論先（残留リスクの低減。「送らない」の代替ではない）**
- **第一者 API のまま**: Fable 5.1 は 30 日保持必須・ZDR 不可。ZDR が必要なら Covered でないモデル（Opus 5.5 等）に `--model` で切り替え、営業経由で組織単位の ZDR 契約（Claude Code ZDR は Commercial org の API キー利用か Enterprise の個別有効化。https://code.claude.com/docs/en/zero-data-retention ）。
- **Amazon Bedrock 東京**: 既定で ZOA/ZDR、Anthropic はログ・プロンプトに触れない。`CLAUDE_CODE_USE_BEDROCK=1`、`AWS_REGION=ap-northeast-1`、`ANTHROPIC_BEDROCK_REGION_PREFIX=jp`、`ANTHROPIC_DEFAULT_OPUS_MODEL=jp.anthropic.claude-opus-5-5`（JP geo = 東京+大阪に閉域。候補は Opus 5.5 / Opus 5（Geo 対応、ID 未確認）/ Opus 4.8）、`availableModels` を jp.* に限定 + `enforceAvailableModels:true`（キーは v2.1.175+、Bedrock 起動チェックへの適用は v2.1.287+）、`CLAUDE_CODE_DISABLE_MODEL_ACCESS_FALLBACK=1`。Claude Code の解決順は「優先 prefix → **一致する任意のプロファイル（global に落ち得る）** → 組み込み ID」なので AWS 側 SCP が必須: `global.*`/`us.*`/`apac.*` の inference-profile を ArnLike で Deny、global は `aws:RequestedRegion=unspecified` + `inference-profile/global.*` の公式例、**リージョン制限は ap-northeast-1 と ap-northeast-3 の両方を許可**（jp. は大阪にルーティングされる）、`bedrock:PutAccountDataRetention` を `none` 以外 Deny（Fable 系の組織的封じ込め）。`data_retention_mode` はリージョン単位なので東京・大阪両方で設定。Auto は Sonnet 5+/Opus 4.7+/Fable のみ対応 → Sonnet 4.6/Haiku 4.5 を選ぶとセッションが Manual に落ちる。分類器の既定 Sonnet 5 は jp. 無しなので、フォールバック挙動は**未確認**のまま SCP で封じる。https://code.claude.com/docs/en/amazon-bedrock 、https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html 、https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html 、https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html
- **Bedrock の代償**: claude.ai コネクタは Bedrock 認証時にロードされない（CLI）、Desktop は「Claude Desktop on 3P」別配備モード（claude.ai ログインと排他、Remote Control/クラウド不可）、WebSearch 不可、Artifacts/Routines/computer use/Chrome 拡張など「サブスク要の機能」が全部消える（https://code.claude.com/docs/en/feature-availability ）。jp. はクォータが global より小さく Opus 5.5 は Standard のみ。Bedrock 上の Fable 5.x は東京 global のみ + `aws_review`（EFS 適格顧客は 2026-12-31 まで ZDR）で要件 A と両立しない。
- **Google Agent Platform**: 新世代モデルは global/us/eu のみで日本閉域不可（asia-northeast1 の可否は未確認）。

**監査ログ**
- `OTEL_LOG_RAW_API_BODIES=file:<dir>`（§4.7）。Bedrock なら Model Invocation Logging で全 Invoke 本文を自社 S3（KMS・Object Lock・90 日）へ保全 + CloudTrail `additionalEventData.inferenceRegion` で処理リージョン監視。**Invocation Logging と `data_retention_mode none` の両立は未確認**（Step 1 で実測し AWS に書面確認）。OTel の `OTEL_LOG_USER_PROMPTS`/`OTEL_LOG_TOOL_CONTENT` は OFF のまま（フック前の原文が出る）。
- Claude Code は HIPAA readiness 対象外。ZDR 下でも T&S フラグ分は最長 2 年保持。

**個人情報保護法 / 3 省 2 ガイドライン上の整理（法務判断の材料）**
- PPC 2023-06-02 注意喚起: 個人データを含むプロンプト入力は利用目的の範囲内か確認し、「応答結果の出力以外の目的で取り扱われる場合」は法違反の可能性 → 提供事業者が学習に使わない等を確認。Covered Models の 30 日保持 + 自動安全審査はこの論点に当たり得る（https://www.ppc.go.jp/files/pdf/230602_alert_generative_AI_service.pdf ）。
- Q&A 7-53/7-54（クラウド例外: 契約で取り扱わない + アクセス制御。例外でも自社の安全管理措置は必要）。LLM 推論への適用は一次資料に明文なし → 保守的には法 27 条 5 項 1 号の委託として委託先監督（法 25 条）。Bedrock の ZOA/ZDR・none・SCP はクラウド例外側の事実関係を補強する材料。https://www.ppc.go.jp/all_faq_index/faq1-q7-53/ 、https://www.ppc.go.jp/all_faq_index/faq1-q7-54/
- 要配慮個人情報は構成に関係なく API 文脈から排除。仮名加工情報は第三者提供不可（Q14-17）＝「仮名化すれば送れる」ではない。統計情報は個人情報に非該当（Q1-17）＝集計のみ返す設計の根拠。擬似化テキスト + 社内復元表が提供元基準で個人データと評価されるかは法務判断。https://www.ppc.go.jp/all_faq_index/faq1-q14-17/
- 厚労省 医療情報システム安全管理 GL 第 7.0 版 Q&A Q18: 「学習等のために保存されないことが契約等で担保」されれば国内法サーバは必須でないが、保存される構成なら企画管理編 7 章⑤により国内法の及ぶ範囲が要求される → Bedrock jp. + none は両方を満たしやすい。Fable はどこでも 30 日保存なので Q18 の「保存されない」を満たせない（https://www.mhlw.go.jp/content/10808000/001752355.pdf ）。
- 提供事業者 GL は**第 2.0 版（令和 7 年 3 月 28 日公表）**が現行。https://www.soumu.go.jp/menu_news/s-news/01ryutsu06_02000427.html
- 従業員の個人情報も法の対象。要件 A のスコープ（顧客/患者のみか従業員も含むか）を明文化。

---

## 8. 決めてもらいたい分岐点

1. **プラン**: `/status` の結果は Pro/Max（消費者プラン）か、Team/Enterprise か、Console API キーか？ Pro/Max なら「モデル改善」設定の確認と商用規約への移行が Phase 0 の最優先になり、org の `blocked` 制御は使えない前提で Slack も切断になります。
2. **モデル**: 「何から何へ」変えましたか（Fable 5.1 は推定）。Fable 5.1 を使い続けますか（30 日保持 + 自動安全審査を法務が受容）？ それとも PII 近傍業務だけ Opus 5.5 に戻しますか（第一者 API なら ZDR 契約が可能、Bedrock なら jp. + none が可能）？
3. **利用面**: Desktop アプリで PII 近傍業務を続けますか？ Day 0 の実測で in-process ツール（画面・クリップボード・ターミナル・他セッション転記）への deny、D&D 添付・画像マーカーの検出が効かなかった場合、CLI/VS Code に寄せることを許容しますか？
4. **コネクタ**: Gmail / Google Drive / Google Calendar / Claude Docs を切断し、問い合わせ処理を `support-intake`（support@ の OAuth + 下書き作成のみ）に置き換えて良いですか？ Slack で読む必要があるチャンネルはどれですか（許可リスト化）？
5. **要件 A のスコープと要配慮の扱い**: 「個人情報」は顧客・患者・問い合わせ者に限定しますか、従業員・取引先も含めますか？ **問い合わせ本文（擬似化済み・健康情報を含む）をモデルに見せますか**（既定は見せない: `tickets_get` はカテゴリ・製品・発生日・定型要約・health_flag のみ）？ 見せる場合は法務受容を §7 に明記し `tickets_get_body_masked` を開放します。
6. **Bedrock 移行**: WebSearch・claude.ai コネクタ・Desktop の通常モード・Artifacts・Routines・computer use を失うことを受け入れますか？ AWS アカウント/SCP/Model Invocation Logging を設定できる管理者はいますか？ 東京 jp. のクォータ引き上げ申請は可能ですか？
7. **法務の整理**: Bedrock 利用を「委託」として整理するか「クラウド例外」を主張するか、擬似化テキスト（復元表は社内）の送信可否、FN 発生時の漏えい等報告基準（要配慮を含む場合は件数下限なし）を誰が決めますか？
8. **管理設定の配布**: MDM/managed-settings.json を配布できますか（できなければ `~/.claude/settings.json` で開始し、`allowManaged*Only`・`managedMcpServers`・`strictPluginOnlyCustomization` は使えず、MCP 集合の固定は `deniedMcpServers` + `permissions.deny` に縮退）？ 複数人で使いますか？
9. **既存スキル/ワークフロー**: Slack 読取系、Google API 系（`www.googleapis.com` deny で停止）、背景サブエージェント系、Chrome 拡張/computer use 系の Skill のうち残すものはどれですか？ 「PII セッション用」と「通常」の settings プロファイルを分けますか？ `!`cmd`` 依存スキルの有無で `disableSkillShellExecution` を決めます。
10. **工数の上限**: Phase 0〜1（約 1 週間、構造化データ由来の PII を構造的に止める）で止めるか、Phase 2（support-intake・pii-guard・NER、合計約 6〜7 週間/1 名）まで進めるか？ Phase 3（Bedrock・MDM・監査）は組織判断が必要です。
11. **WebFetch**: pii-guard の PreToolUse で公開ドキュメント allowlist 方式（docs.* / developer.* / npm / PyPI / GitHub の非 Issue パス等。settings の `WebFetch(domain:)` deny はバックストップ）で良いですか、それとも `CLAUDE_CODE_DISABLE_WEB_FETCH=1`（v2.1.285+）で全面停止し社内 docs はエクスポート経由にしますか？ 自社の公開ドメインと github.com の Issue（顧客ログが貼られ得る）の扱いは？
12. **ローカル DB / dev サーバ**: 本番スナップショット DB を localhost に置く運用はありますか？ ローカル dev サーバのプレビュー・Playwright・supertest を維持しますか（維持なら `allowLocalBinding:true` を受容し、PII を持つローカルサービスを同一マシンで動かさない運用。拒否なら起動コマンドだけ `excludedCommands`）？
13. **画像・スクリーンショット**: 全面禁止で運用できますか？ それともローカル OCR ゲート（Apple Vision、**未検証**）を作る工数を割きますか？
14. **リポジトリの置き場所**: `~/Documents`・`~/Desktop`・`~/Library` 配下にリポジトリがありますか（あれば該当 deny を外すか `~/src` へ移動）？
15. **Mac の管理者権限**: sudo・別 macOS ユーザー `safedata` の作成・LaunchDaemon 登録ができますか？ できない場合、OS レベルの分離を諦め deny + denyRead + MCP 実装の信頼に縮退します。
16. **git の認証方式**: HTTPS + `gh auth` に寄せて `~/.ssh` を denyRead できますか？ SSH を使い続ける場合は `~/.ssh` を deny しません。
17. **DB エンジン**: PostgreSQL（+ pgcrypto のスキーマ）ですか？ MongoDB / MySQL / BigQuery の場合は「ETL で安全化レプリカを生成し safe-data はレプリカだけに繋ぐ」設計に切り替えます。
18. **Docker Desktop**: 利用可能ですか？ 不可なら `py_run` は `sandbox-exec`/別ユーザー実行に縮退（未検証）。
19. **Sentry 組織管理者権限**: Data Scrubber を組織レベルで ON にできますか？ できなければ Sentry コネクタは恒久 deny + `safe-data.sentry_events` のみ。
20. **Google Workspace 管理者**: support@ 用の内部 OAuth アプリ承認を得られますか？ 得られなければ support-intake の Gmail 取込は IMAP/アプリパスワード等の代替を検討（未評価）。

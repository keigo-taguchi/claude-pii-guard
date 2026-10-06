# Phase 2 設計：pii-guard を Mod として作る

更新: 2026-10-06。前提は Claude Code v2.1.289 の Mods（function hooks、早期アクセス API）。公式リファレンス: https://code.claude.com/docs/en/plugins/mods/reference 、イベント: https://code.claude.com/docs/en/plugins/mods/events 。先行例: https://github.com/kexi/claude-privacy-gateway 。

## 0. 何が変わるか（settings の http hook 案との差）

| 観点 | 旧案（settings hooks） | 新案（Mod） |
|---|---|---|
| 打ち込んだプロンプト | `UserPromptSubmit` は **止めるだけ** | `prompt.submit` で**本文を書き換え**られる |
| ツール結果 | `PostToolUse.updatedToolOutput`（形不一致は黙って原文送信、失敗結果は書換不可） | `session.append` で**保存される全行**（成功・失敗・サブエージェント報告）を書き換え |
| CLAUDE.md・メモリ・git 状態 | 触れない | `prompt.context` で書き換え（Team/Enterprise では組織ガードに阻まれうる、§7） |
| 画像・PDF | 触れない | `session.append` で image/document ブロックを**落とせる** |
| 失敗時 | fail-open（タイムアウト＝原文送信） | `.catch` を付ければ **fail-closed**（drop / 伏せ字差し替え） |
| 最終手段 | `PostToolBatch` の block は行が残る | `turn.step` でモデル呼び出し自体を止められる |
| 実装言語 | 任意 | hooks module は JS/TS（Node API なし）。重い処理は `$.http.fetch` でローカルデーモンへ |

変わらないこと: **層 1〜4（届かせない・手元で実行）が主役**。Mod は最後の網。検出器は辞書 + 規則（+ 小型 NER）で、ローカル LLM は使わない（kexi 版の初回 ~290 秒は Auto の体験に合わない）。

## 1. 構成

```
Claude Code ─ Mod: pii-guard (hooks/register.ts) ──HTTP 127.0.0.1:8787──▶ pii-guard デーモン (Python)
                 │                                                          ├ detect/  正規化・辞書・規則・NER
                 │  prompt.submit / session.append / prompt.context /       ├ vault    札⇄実値（暗号化 SQLite, 0600）
                 │  tool.call / tool.check / turn.step / ui.render          └ dict     safe-data の DB から毎晩生成
                 │
support-intake MCP ──────────────────────────────────────────────────────────┘（同じデーモンで擬似化・復元）
slack-safe MCP ──────────────────────────────────────────────────────────────┘
```

- **Mod は薄い**: 行を歩いてテキストを集め、デーモンに `POST /mask` して置換結果を戻すだけ。検出ロジック・辞書・vault を JS 側に持たない（辞書は秘密情報なので、Mod のメモリや `$.store`（平文 JSON）に置かない）。
- **デーモンが唯一の検出器**: Mod、support-intake、slack-safe、夜間の転記スキャン、pre-commit が同じ `/mask` を使う。札の一貫性（同じ人＝同じ札）はここで保証する。
- **復元は 2 箇所だけ**: デーモン内（support-intake の返信下書き）と `ui.render`（画面表示のみ）。`tool.call` では復元しない（Edit/Write 経由で実値がファイル→Read→文脈へ戻る経路を作らない）。

## 2. イベント配線

| イベント | matcher | 処理 | `.catch`（fail-closed） |
|---|---|---|---|
| `session.start` | — | デーモンの `/healthz`。落ちていれば `launchctl kickstart` を 1 回。設定（`options`）を読む。`/pii` コマンド登録 | 到達不能なら `$.ui.status('pii-guard: 停止中')` を出し、以後の各 hook は fail-closed 側に倒れる |
| `prompt.submit` | — | `e.text` と `e.context[]` を `/mask`（site=`prompt`、フルセット）。画像付きプロンプトはポリシー `images: drop` なら `{ drop }`（§7 未確認） | `{ drop: '個人情報の検査ができないため送信を止めました（pii-guard 停止中）' }` |
| `session.append` | — | 行の `content` を歩き、`text` と `tool_result.content` を `/mask`。`image`/`document` は `images` ポリシーで落とす。`thinking`/`tool_use` は触らない。site は行の由来（ツール名・cwd 内か）で決める（§4） | 行の全テキストを `[pii-guard] 検査できなかったため伏せました` に差し替え、`tool_result` の構造は保つ |
| `prompt.context` | — | 各 block の `text` を `/mask`（site=`context:<name>`、辞書 + 規則のみ） | `{ blocks: [] }`（CLAUDE.md を送らない方が安全側） |
| `prompt.section` / `skill.prompt` / `tool.describe` | — | 規則のみ（エンジン生成テキスト。PII はまず入らない。Team 配下では組織ガードが先に走る） | そのまま通す（`next(e)`）— PII 経路ではない |
| `tool.call` | `{ tool: ['Bash', /^mcp__/] }` | 引数に辞書フル一致・患者 ID・文脈付きマイナンバーがあれば `{ deny: '引数に個人情報。擬似 ID で指定してください' }`。復元はしない | `{ deny: 'pii-guard が応答しないため実行を止めました' }` |
| `tool.check` | `{ tool: 'Bash' }` | 汚染中（§5）は `{ decision: 'deny' }` | — |
| `turn.step` | — | 汚染中、またはデーモン停止中は `next` を呼ばず `{ text: 'pii-guard: 送信を止めています。/pii status を確認してください' }` を返す（モデル要求を出さない） | 同上 |
| `ui.render` | `{ component: ['UserMessage','ToolResult','ToolUse','AssistantMessage'] }` | 表示用に `/unmask`（札→実値）。Claude には届かない | 伏せ字のまま表示 |
| `session.end` | — | デーモンに `/session/close`（統計のみ） | — |
| `command.run` | `{ command: 'pii' }` | `/pii status`（デーモン・辞書の鮮度・直近の検出件数）、`/pii untaint`、`/pii off`（PII セッション用途以外で一時停止。managed なら不可） | — |

`.catch` の原則: **マスクする hook は必ず `.catch` を持ち、`next` を呼ばない**。公式仕様では `.catch` がない hook の失敗は「スキップして次へ」＝原文が通る。

## 3. デーモン API（127.0.0.1:8787、外部通信なし）

| エンドポイント | 入力 | 出力 |
|---|---|---|
| `POST /mask` | `{ site, texts: [string], session }` | `{ texts: [string], hits: {kind: count}, tainted: bool }`。値は返さない |
| `POST /unmask` | `{ texts }` | 表示用。`ui.render` と support-intake だけが呼ぶ（呼び元を `X-Caller` で記録） |
| `POST /check-args` | `{ tool, args }` | `{ deny: bool, kinds: [...] }` |
| `GET /healthz` | — | 辞書の世代・NER 有効・p50 レイテンシ |
| `POST /dict/reload` | — | 夜間ジョブが呼ぶ |

実装: `src/pii_guard/`（Python、stdlib `http.server` か uvicorn。依存を増やさない）。`guards.py` の `normalize_forms` を共有する。

## 4. 検出器とルーティング（site で強さを変える）

| site | 由来 | 掛けるもの | 理由 |
|---|---|---|---|
| `prompt` | 打ち込み | 辞書フル一致・規則・NER | 最も漏れやすい経路 |
| `data:*` | コネクタ MCP の結果、`~/PII`・`./data` 由来、`Agent` 報告、`TaskOutput` | 辞書・規則・NER・要配慮語彙の注記 | 自由記述 |
| `code` | cwd 内の Read/Edit/Grep/Bash（`cat`/`rg` 等）結果 | **辞書フル一致 + ID 規則のみ**（NER・単独姓・12 桁規則なし） | 誤検出で Edit の一致が壊れ、再読ループになる |
| `context:*` / `engine` | CLAUDE.md・メモリ・システムプロンプト | 辞書 + 規則 | 構造化されていない短文 |
| `safe` | `mcp__safe-data__*`、`mcp__support-intake__*`、`mcp__slack-safe__*` | **掛けない** | 既に擬似化済み。二重に札を貼らない |

層: L0 正規化（NFKC・異体字等価クラス・ローマ字・符号化復号）→ L1 辞書（Aho-Corasick、フルネームのみ、毎晩 + support-intake 取込時に即時追加）→ L2 規則（電話・メール・〒・文脈付きマイナンバー・患者 ID スキーマ・文脈付き DOB・住所正規表現）→ L3 NER（`data:*` と `prompt` のみ、8KB まで、200ms 予算超過は L1+L2 の結果に `[pii-guard: NER 未適用]` 注記）。設計書 §4.8 の表をそのまま使う。

札: 既知の個人は人物スコープ `[P12_NAME]` `[P12_EMAIL]`、未知は型別連番 `[NAME_3]`。敬称は外（`[P12_NAME]様`）。vault は端末単位・ID 非再利用。

## 5. 汚染（taint）と解除

Mod 側で fail-closed にできるので、旧案の「結果が会話に残る」問題は小さい。残るのは **デーモン停止中に通ってしまった行はない**（drop/伏せ字差し替えで止まる）ため、汚染は「`/mask` が `tainted:true` を返したとき」＝規則で拾えたが札に置換できない形（画像内文字など）に限る。`turn.step` で止め、`/pii untaint` か `/clear`（`session.end` reason=`clear`）で解除。巻き戻し（Esc Esc）は転記監視で `tool_use_id` の消失を見る。

## 6. Phase 0/1 との接続

- deny / sandbox / safe-data / DB ビューは**そのまま**。Mod はそれらが取りこぼした自由記述のためにある。
- support-intake / slack-safe は、自前で擬似化せず**デーモンの `/mask` を呼ぶ**（札の一貫性のため）。返信下書きの復元はデーモンの `/unmask` を MCP プロセスから呼ぶ。
- 辞書の生成元は safe-data と同じ `claude_ro` ではなく、**氏名・連絡先列を読める専用ロール**（`dict_builder`）で夜間ジョブが作る。Claude Code からは `~/.config/safe-data/` ごと denyRead 済み。

## 7. 未確認（実装前に `claude --plugin-dir` で実測する）

1. `session.append` がサブエージェントの行と、失敗した tool_result（isError）も通るか。kexi 版の README では「モデルが受け取る前にマスク」とあるが、公式表は「保存される各行」とだけ記述。
2. `prompt.submit` の `e` に画像・ドラッグ＆ドロップ添付がどう現れるか（`e.text` 内のマーカーか、別フィールドか）。
3. 本人が **Team プラン**でサインインしている場合、`sec-default@builtin` が先に読み込まれ、`prompt.context` / `prompt.section` の書き換えがユーザー Mod から効かない（kexi 版の記載）。本設計では CLAUDE.md を PII 経路にしないので致命ではないが、Day 0 で確認。
4. `turn.step` で `next` を呼ばず `{ text }` を返す形が「モデルを呼ばない」の正式な戻り値か（型定義 `claude-code.d.ts` で確認）。
5. 転記 JSONL の `toolUseResult` は表示用に平文が残る（kexi 版の記載）。`cleanupPeriodDays` 7 日 + 夜間スキャンで扱う。
6. Mod の `$.http.fetch` が `127.0.0.1` に出られること（サンドボックスの対象外のはず。Mod はエンジン内で動く）。
7. hook 自身の実行時間 10 秒（`$.http.fetch` の待ち時間は含まない）。大きな行（50,000 字超）をブロックに分けて送るときの自前処理が 10 秒に収まるか。

## 8. リポジトリ配置と進め方

```
mods/pii-guard/
  .claude-plugin/plugin.json        name: pii-guard, userConfig: daemonUrl / images / sites
  hooks/hooks.json                  modules: ["./register.ts"]
  hooks/register.ts                 §2 の配線だけ
  hooks/blocks.ts                   content ブロックの歩行（text / tool_result / image / document）
  hooks/client.ts                   /mask /unmask /check-args のクライアント、タイムアウト、再試行 1 回
  hooks/policy.ts                   site 判定（ツール名・パスが cwd 内か）
  hooks/*.test.ts                   claude plugin test
  types/index.d.ts                  PluginState（汚染フラグ、デーモン状態）
src/pii_guard/                      デーモン・検出器・vault・辞書ビルダー
src/support_intake/, src/slack_safe/
launchd/com.pii-guard.plist         LaunchAgent（sudo があれば別ユーザーの LaunchDaemon）
```

| 段階 | 内容 | 完了判定 |
|---|---|---|
| 2a（1〜2 週） | デーモン（L0〜L2）+ Mod（`session.start` / `prompt.submit` / `session.append` / `tool.call` / `.catch`）+ `claude plugin test` | カナリア（辞書内の偽顧客、異体字、和暦 DOB、ローマ字逆順）がプロンプト・Bash 出力・コネクタ結果のどこに置いても転記に出ない。デーモンを殺しても原文が出ない |
| 2b（1〜2 週） | NER、`ui.render` 復元、`/pii` コマンド、`turn.step` キルスイッチ、転記監視 | FN 率を 300 件の実データで実測し記録 |
| 2c（2〜3 週） | support-intake（Gmail）・slack-safe を同じデーモンに接続、要配慮の準識別子の粗視化 | 問い合わせ 3 シナリオが確認 0 回で通る |

## 9. kexi 版から借りるもの・借りないもの

借りる: `.catch` で `{ drop }` を返す fail-closed の形、`session.append` + `ui.render` の組み合わせ、`maskBlocks` の「上位の未知ブロックは触らず通知、入れ子は JSON 化してマスク」「`thinking`/`tool_use` は触らない」「画像は落とす」という歩行規則、`bashRestore: off`（札を含む Bash を拒否）。

借りない: Gemma によるエンジン生成テキストの全走査（遅い・確率的）、`$.state` に vault を置くこと（Mod 側に実値を持たせない）、`tool.call` での復元（実値を文脈に戻す経路になる）。

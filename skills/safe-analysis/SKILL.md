---
name: safe-analysis
description: DB・CSV・ログ・問い合わせデータなど個人情報を含みうるデータの集計・分析・調査を頼まれたときに使う。safe-data MCP（schema_describe / files_describe / sql_run / py_run / fixture_make）で「見ずに処理する」手順。「集計して」「継続率を出して」「このCSVを分析して」「原因を調べて（ユーザー状態の確認）」で起動。
---

# safe-analysis

個人情報を含むデータは、Claude が直接読まずに **safe-data** に処理させる。返ってくるのは集計・スキーマ・擬似 ID・合成サンプルだけ。これは制約ではなく、通常の手順。

## 手順

1. **形を見る**
   - DB: `schema_describe()` → `claude` スキーマのビューと列、合成サンプル 5 行
   - ファイル: `files_describe()` → inbox のファイル id と推定スキーマ（`pii: true` の列は値が返らない）
2. **集計で答えられるか考える**。答えられるなら `sql_run` で終わり。
   - 1 文の SELECT / WITH。`SELECT *` は不可、列名を書く
   - 人は `user_pseudo_id` で指す。`WHERE name = '...'` のような実値は書かない
   - 少数セル（n<11）は null で返る。それを「0」と解釈しない
3. **行単位の処理が要るなら `py_run`**
   - `fixture_make(<id or view>, 5)` で合成データを取り、`./analysis/<name>.py` を書く
   - スクリプトは `import sd` → `df = sd.load("<file id>")` → 計算 → `sd.result({...})`
   - 結果は 8KB 以内・文字列 64 字以内。**行を返さない。集計・分布・件数にする**
   - 保護対象の値が結果に混ざると拒否される。拒否されたら、列を落とす／集計に変えるのが正解。回避策を探さない
4. **報告する**。擬似 ID や `[P12_NAME]` のような札はそのまま書く。推測しない。

## 書いてはいけないこと

- `cat ~/PII/...`、`python -c "open('~/PII/...')"` など inbox を直接読む試み（deny される。迂回しない）
- `psql` での本番 DB 直結（safe-data だけが `claude_ro` で繋ぐ）
- 実名・メール・電話・患者 ID を含むコマンド、SQL、ファイル名

## py_run スクリプトの例

```python
import sd

df = sd.load("3f2a9c1b")                      # files_describe の id
df["week"] = pd.to_datetime(df["event_at"]).dt.to_period("W").astype(str)
cohort = df.groupby("week")["user_id"].nunique()   # user_id は PII 列: 値は結果に出さない
sd.result({
    "weekly_active_users": {k: int(v) for k, v in cohort.items()},
    "rows": int(len(df)),
})
```

`import pandas as pd` はスクリプト側で行う。`print` は返らない（`sd.log` を使う、最大 20 行）。

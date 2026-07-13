# ADR 0002: effective_tokens を Collector で 1 回だけ事前計算する

- ステータス: 採用
- 日付: 2026-07

## Context

コスト最適化ダッシュボードには「効率比の共通分母」が要る。input / output / cache_read /
cache_creation はそれぞれ価格ウェイトが大きく異なる（cache read は通常入力の 0.1x、
cache write は 1.25x、output は input の約 4 倍）ため、生トークン数を単純合算しても
「効率」を測れない。価格ウェイトで正規化した**実効トークン（effective_tokens）**が必要になる。

この派生値をどこで計算するかが問題になる。過去に「stat panel と timeseries で同じはずの値が
食い違う」バグが 2 回発生しており、共通原因は「複数フィールドの算術演算をダッシュボードの
クエリ側で毎回書いていた」ことだった（requirements.md 12.6）。例えば
`context_tokens = cache_read_tokens + cache_creation_tokens` をパネルごとに `+` で結合すると、
集約軸（`by (session_id)` の有無等）によって「異なる時刻・異なるセッションの最大値同士の
合計」という無意味な値になり得る。`context_tokens` はこの教訓から既に Collector 側で
単一フィールド化されている（CONTRACT §3.1）。

## Decision

**`effective_tokens` を OTEL Collector の transform processor で 1 回だけ事前計算し、
単一フィールドとして Loki に届ける。** ダッシュボードと分析スクリプトはこの単一フィールドを
`unwrap` / 参照するだけにし、算術式をクエリ側に一切書かない（DRY をデータ収集層で守る）。

計算式（CONTRACT §5）:

```
effective_tokens = input_tokens*1.0
                 + cache_read_tokens*0.1
                 + cache_creation_tokens*1.25
                 + output_tokens*5.0
```

ウェイトの根拠（2026-07 時点、要定期確認）: cache read = 通常入力の 0.1x、
cache write(5分) = 1.25x、output ≈ input の約 4 倍（Sonnet 系は正確に 5 倍だが実効式では
output/input 価格比 5.0 を採用。pricing.yaml と一致）。価格改定時はこのウェイトと `pricing/pricing.yaml` を併せて見直す。

## Consequences

**利点（トレードオフの表）**

- パネル間・スクリプト間で `effective_tokens` の値が**構造的に必ず一致**する。集約軸の違いで
  乖離が生じない。CONTRACT §3.1 の `context_tokens` と同じ DRY 原則の適用。
- ダッシュボードの LogQL が単純化し（`unwrap effective_tokens` だけ）、可読性と保守性が上がる。
- 「コスト / 1k 実効トークン」のような単位コスト指標を安定して算出できる。

**代償**

- ウェイトが Collector 設定・CONTRACT §5・`pricing.yaml` の 3 箇所に関わるため、価格改定時に
  同期漏れのリスクがある。改定時は必ず 3 者を突き合わせる運用が必要（CONTRACT §5 に明記）。
- ウェイトは best-effort の近似（output ウェイト 5.0 は現行価格比。改定時は要見直し）。厳密なモデル別コストが要る場合は
  `pricing.yaml` ベースの再計算（[ADR 0004](0004-pricing-ssot-and-cost-recompute.md)）を使う。
- Collector 側で string → Int キャストを行うため、フィールド欠損時は計算をスキップする
  ガードが必要（`context_tokens` 同様、両フィールドが nil でない時のみ計算）。

**関連**: requirements.md 12.6、CONTRACT §3.1 / §5。

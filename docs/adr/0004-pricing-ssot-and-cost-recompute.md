# ADR 0004: pricing.yaml を価格 SSOT に、コストを自前再計算する

- ステータス: 採用
- 日付: 2026-07

## Context

Loki に届く `api_request.cost_usd` は **SDK の推定値**であり公式請求ではない（CONTRACT §3.1）。
コスト最適化のためにこの値をそのまま信じると、以下の問題がある。

- **推定値の乖離**: SDK 推定が実際の課金と乖離しうる。乖離量を測る基準がないと、
  ダッシュボードのコストが「計測バグ」なのか「実態」なのか切り分けられない。
- **価格が場所によって散らばる**: 価格を各スクリプト・各パネルに直書きすると、価格改定時に
  同期漏れが起きる（DRY 違反）。
- **世代跨ぎの反実仮想**: 「Opus を Sonnet に替えたら安くなるか」を試算するとき、モデル間の
  トークナイザ世代差を無視すると誤る。新世代トークナイザは同一テキストで約 +30% トークン化
  するため、旧→新の counterfactual では入力トークンを補正しないと過小評価になる。
- **発効日つき価格改定への備え**: 価格は将来改定されうるため、日付を無視した単一価格では
  改定前後をまたぐ期間で誤る。発効日つきで価格を解決できる仕組みが要る（※ 2026-07-12 校正
  時点では発効予定の改定エントリは無い。かつて Sonnet5 の「2026-09 値上げ予定」としていた
  値は、実測含意単価が現行価格そのものと判明したため撤回した）。

## Decision

**`pricing/pricing.yaml` を唯一の価格表（SSOT）とし、唯一のローダ `analysis/pricing.py` が
`cost_usd` を自前再計算（`cost_recomputed`）して SDK 推定値との乖離を検証できるようにする。**

> **用語注記**: 本 ADR は 0006 以前の文書のため派生値を `cost_recomputed` と表記するが、
> 実装・CONTRACT §3.1・[ADR 0006](0006-cost-recalc-in-collector.md) では同じ値を
> `cost_recalc` と呼ぶ（collector が取り込み時に各 api_request ログへ付与）。以降 `cost_recomputed`
> は `cost_recalc` と読み替えること。

- 価格表は「モデル ID × 発効日」で管理。将来の改定は `price_changes[].from` で発効日つきに
  表現し**日付で解決**できる（現時点では発効予定エントリ無し。Sonnet5 は現行 3/15 で、
  旧記載の 2/10 は誤りとして 2026-07-12 校正で訂正済み）。
- cache 係数（read 0.1x / write5m 1.25x / write1h 2.0x）、request 修飾子（batch 0.5x /
  fast 2.0x / geo_us 1.1x）も同ファイルに集約。
- 世代跨ぎ反実仮想はトークナイザ世代差を `tokenizer.new_vs_old_ratio: 1.3`（約 +30%）で補正する
  （old→new は増、new→old は減）。
- 他コードは `pricing.yaml` を直接パースせず、必ず `analysis/pricing.py` 経由で読む（CONTRACT §7）。

## Consequences

**利点（トレードオフの表）**

- SDK 推定 `cost_usd` と `cost_recomputed` の乖離を定量化でき、コスト表示の信頼性を検証できる。
- 価格が 1 ファイル・1 ローダに集約され、改定時の同期漏れリスクが下がる。
- 発効日つき価格と世代差補正により、値上げ跨ぎ・モデル切替の counterfactual を近似できる。

**代償・運用注意（best-effort 価格）**

- **`pricing.yaml` の価格・係数はすべて best-effort（2026-07 調査値）**であり、実際の請求とは
  乖離しうる。**利用前に必ず公式 pricing ページと突合すること**（ファイル冒頭・CONTRACT §7・
  README に明記）。
- 価格改定時は `pricing.yaml` と CONTRACT §5 の `effective_tokens` ウェイト
  （[ADR 0002](0002-effective-tokens-in-collector.md)）を**併せて**見直す必要がある。片方だけ
  直すと単位コストと再計算コストが食い違う。
- `new_vs_old_ratio` は単一の近似係数であり、テキスト内容による実際のトークン化差を厳密には
  反映しない。反実仮想はあくまで目安。
- 再計算は cost の内訳（input/output/cache 種別）が Loki に届いていることが前提。欠損時は
  再計算不能。

**関連**: requirements.md 第 16 章、CONTRACT §3.1 / §5 / §7、`pricing/pricing.yaml`、
`analysis/pricing.py`。

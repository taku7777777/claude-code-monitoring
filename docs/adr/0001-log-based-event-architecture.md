# ADR 0001: ログベース個別イベント方式を採用する

- ステータス: 採用
- 日付: 2026-07

## Context

Claude Code の利用実態（コスト・時間・トークン・コンテキスト消費）を任意の時間レンジで
可視化したい。当初の自然な発想は「SDK が OTLP で送る cumulative counter
（`claude_code_cost_usage_USD_total` 等）を Prometheus に保存し、`rate()`/`increase()` などで
クエリ時に差分計算する」方式だった。

しかしこの方式は本用途で構造的に破綻することが、16 件の ADR を積み上げる過程で判明した
（requirements.md 12.1 / 12.2）。累計値を保存し後から差分計算するモデルは、以下のすべてに弱い:

1. **counter reset 誤検知**: プロセス再起動で値が 0 に戻ると `rate()` が「減少=リセット」と
   みなして直前値を加算し、実値の数百〜数千倍の異常値を出す。
2. **同一 label set + 異なる start_timestamp の stream 並走**: cumulative stream が交互に
   上書きし合い値が振動する（例 $48 ⇄ $102）。temporality を delta にしても解消しない
   （requirements.md 12.9）。
3. **ラベル変更による series 分裂**: 旧 series と新 series が並存し、`min_over_time` 等の
   「過去を遡る」クエリが片方しか拾えず長期レンジで値が頭打ちになる。
4. **retention 切れ**: 差分計算の起点 series が消えるとダッシュボードが壊れる。
5. **部分リセット**: 再起動を伴わずに counter が部分的に巻き戻ることがあり差分が負になる。

PromQL レベルの工夫（`rate` → `metric - metric offset $__range` → `metric - min_over_time`）を
順に試したが根本解決に至らなかった（requirements.md 12.5）。

## Decision

**累積値を保存する方式を捨て、個別イベントをログストア（Loki）に記録し `sum_over_time` で
単純合算する「ログベース個別イベント方式」を採用する。** 各イベント（`api_request`,
`lines_changed`, `wait_time_observed` 等）は絶対値を持つ独立レコードとして Loki に届き、
ダッシュボードは `sum_over_time({event_name="..."} | unwrap <field> [$__range])` で合算する。

本実装はこの決定を全面的に踏襲している:

- ダッシュボードで使う値は**すべて Loki ログベース**。Prometheus は SDK 標準メトリクスの
  受け皿として稼働するだけでダッシュボードからは参照しない（requirements.md 4.2 / 7 章）。
- SDK が出さない指標（待機時間・permission 待ち・変更行数・task_outcome・
  intervention_marker）も、メトリクスではなく**個別ログイベント**として送る（11 章）。
- 高カーディナリティ属性（`session_id` 等）は stream label にせず structured metadata に
  留め、cardinality を抑えつつセッション別分析を維持する（CONTRACT §4）。

## Consequences

**利点（トレードオフの表）**

- counter reset / 並走振動 / series 分裂 / retention 切れ / 部分リセットが**構造的に発生しない**。
  各イベントが絶対値なので合算するだけ。ラベルを後から変えても過去イベントは過去のラベルで
  集計でき、retention 切れは「古いデータが自然に消える」だけで新しい集計を壊さない。
- 任意レンジ（5 分〜数日）で値が頭打ちにならず自然に増加する（要件 C-02 を構造的に満たす）。
- セッション別の詳細分析を犠牲にしない（structured metadata でフィルタ・グルーピング可能）。

**代償**

- Prometheus は SDK 標準メトリクス受け皿として残置しているが、ダッシュボードでは未使用。
  ゼロから作るなら Prometheus コンポーネント自体を省略できる（requirements.md 7 章の注記）。
- ログ量が増えるが、Loki の retention（2160h＝90日）と structured metadata により実運用では
  ストレージ数 GB 以下に収まる（要件 N-02）。
- 「累積値をそのまま見たい」用途には向かない。常に期間合算が前提になる。

**関連**: requirements.md 12.1 / 12.2 / 12.5 / 12.9、CONTRACT §4。

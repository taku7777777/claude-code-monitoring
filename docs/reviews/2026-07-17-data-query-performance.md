# データ・クエリ性能レビュー（描画速度劣化の原因分析）

日付: 2026-07-17 / 対象: 稼働中スタック（Loki 3.5.1, Grafana 13.0.1）に対する実測ベースの精査。
「最近画面描画が遅い」の原因特定と、管理データ・クエリの妥当性評価。

## TL;DR

- **データ量は原因ではない**。Loki チャンク合計 9.3MB、api_request は 7日で 13,303行/9.5MB しかない。
- **原因は Loki のクエリ実行設定**。7日レンジのクエリ（instant 含む）が 1時間刻みで **168分割**され、
  それを **4並列**のキューで処理し、**キャッシュは全て無効**。1クエリ約1秒 × Cost画面67クエリが
  キューに殺到し、Cost Optimization のフル描画は **2分42秒**（実測）。
- 対策は3層: **(P1) Loki設定**（分割幅・並列度・キャッシュ）が最大レバー、
  (P2) ダッシュボード修正（壊れた offset クエリ・auto-refresh 見直し）、
  (P3) 未使用イベントの ingest 停止（行数の2/3が誰にも読まれていない）。

## 1. 実測値（before）

### 1.1 画面フル描画時間（/render、全パネル完了まで）

| 画面 | パネル/クエリ数 | 既定期間 | 描画時間 |
|---|---|---|---|
| Cost Optimization | 47 / 67 | now-7d | **2分42秒** |
| Today | 11 / 20 | now/d | **22.6秒** |
| Session List | 3 / 8 | now-7d | **36.9秒** |

Session List はわずか8クエリで37秒。パネル数ではなく**1クエリあたりの固定コスト**が支配的。

### 1.2 個別クエリの実測（Loki API, stats.summary）

| クエリ | exec合計 | queue合計 | 処理行数 | 処理バイト |
|---|---|---|---|---|
| コスト合計 7d instant（label_format+unwrap） | 2.25s | **26.7s** | 13,303 | 9.5MB |
| セッション別コスト 7d instant | 1.38s | **35.2s** | 13,303 | 9.5MB |
| quantile_over_time 7d instant | **0.058s** | 0.0008s | 5,628 | 3.9MB |
| user_prompt 正規表現フィルタ 7d | 0.064s | 0.0004s | 664 | 0.3MB |

壁時計では 7d instant クエリ1本 ≈ **1.0〜1.2秒**（単発）、8本並行で ≈ 4.6秒/本に劣化。

**決定的な対照実験**: `quantile_over_time` は分位数がマージ不能なため Loki が**時間分割できず、
1本のクエリとして実行**される。結果 5,628行を **58ms** で処理している。
つまり同じデータでも「分割しなければ」2桁速い。遅さの正体はデータスキャンではなく
**分割サブクエリ168個分のオーケストレーション＋キュー待ち**である。

### 1.3 実効設定（`curl localhost:3100/config` で確認した現在値）

| 設定 | 現在値 | 影響 |
|---|---|---|
| `limits_config.split_queries_by_interval` | 1h | range クエリが 7d→168分割 |
| `limits_config.split_instant_metric_queries_by_interval` | 1h | **instant クエリも** 7d→168分割 |
| `querier.max_concurrent` | 4 | サブクエリを同時4個しか処理しない |
| `query_range.cache_results` | false | range 結果キャッシュ無効 |
| `query_range.cache_instant_metric_results` | false | instant 結果キャッシュ無効 |
| chunk cache (embedded_cache) | 無効 | 30秒ごとの auto-refresh が毎回チャンクを読み直す |

67クエリ × 168分割 ≈ **1.1万サブクエリ**が4並列キューに投入されるのが Cost 画面の実態。

### 1.4 「最近劣化した」の説明

固定コスト（分割×キュー）は**クエリ本数と対象期間内のチャンク数に比例**する。直近1週間で
(a) Cost 画面のパネル増設（未計上コスト節 +7クエリ、再作成率カラム等）、
(b) mrw-telemetry 接続によるイベント流入増（workspace/セッション数増 → チャンク数増）、
(c) 7日レンジに実データが常に満ちる状態になった（運用開始 ~07-09 から1週間経過）
が重なり、閾値を超えて体感悪化した。データ量そのものは今も極小。

## 2. 管理データの妥当性

### 2.1 イベント別の量と消費者（24h 実測）

| event_name | 行数/日 | 消費者 | 判定 |
|---|---|---|---|
| hook_execution_start / complete | **10,191**（全体の約65%） | **なし** | ドロップ推奨 |
| hook_registered | 40 | なし | ドロップ推奨 |
| tool_decision / tool_result | 4,883 | Cost・Prompt明細ほか | 維持 |
| api_request | 2,061 | 全9画面＋analyzer | 維持（中核） |
| assistant_response | 917 | Prompt明細 | 維持 |
| prompt_summary | 140 | **なし**（プロンプト一覧は「過小計上のため使用しない」と明記済み） | 生成停止を検討（→機能構成レビュー） |
| auth / at_mention / feedback_survey / mcp_server_connection / permission_mode_changed / skill_activated / api_error / api_refusal / api_retries_exhausted | 計 ~140 | なし | 量が無視できるため放置可（api_* はエラー観測として将来価値あり） |

- ラベル設計は健全（index_label は5つのみ、CONTRACT §4 通り。高カーディナリティは structured metadata）。
- バイト量は全イベント合計でも ~550KB/日 と極小。**ドロップは性能対策ではなく衛生対策**
  （90日 retention 中の行数の2/3が誰にも読まれないデータになるのを防ぐ）。
- `ingester.max_chunk_age: 48h` は未使用の prompt_summary（last-wins 追記）のためだけの延長設定。
  テイラー廃止時は既定(2h)へ戻せる（Loki 常駐メモリ 823MB の軽減余地）。

### 2.2 クエリの妥当性

概ね健全。range クエリはウィンドウとステップが一致（[5m]/step5m, [1h]/1h, [1d]/1d）しており、
ステップごとに窓を再スキャンする古典的な事故は無い。指摘は以下。

1. **【バグ】Today「累計コスト」の過去3週平均線が常に空**
   `(sum(...offset 7d) + sum(...offset 14d) + sum(...offset 21d)) / 3` は、
   いずれかの offset にデータが無いと**式全体が空**になる（LogQL のベクトル演算仕様）。
   データは 07-10 開始のため 14d/21d が空 → 描画されない（レンダリング実測で凡例に不在を確認、
   stats でも processed lines=0）。**描画されないのに3週分のチャンクスキャンを30秒ごとに払っている**。
   → 各項を `(... or vector(0))` で包んで修正（当面は「ある週のみ/3」の過小表示になる点は許容し注記）。

2. **【過剰】7日レンジ画面の 30秒 auto-refresh**
   Session List（8クエリ×7d）が30秒ごとに全再計算。1回の描画が37秒 > refresh間隔30秒のため、
   タブを開いている限り**キューが飽和し続け、他画面も巻き添え**になる。
   → Session List / Context は 1m へ。Today/Workspace（当日レンジ・巡回画面）は P1 適用後なら 30s 維持で可。

3. **【重複】Cost 画面内の同一クエリ再計算**
   「期間合計コスト」と同型のクエリが8箇所以上で繰り返される（概況stat・CPSO分子・$/d・
   月次見込・前週比・タスク別サマリ…）。Grafana はパネル間でクエリ結果を共有しないため
   全て個別実行される。→ P1 の instant 結果キャッシュが実質的な解（同一式・同一範囲はキャッシュヒット）。
   ダッシュボード側の統合は費用対効果が低いので行わない。

4. **【設計妥当】** cost_recalc/cost_usd のフォールバック（label_format if/else）、
   `quantile_over_time`、user_prompt の regex 除外などはデータ量に対して十分軽い（実測 <100ms）。

## 3. 改善策と優先度

### P1: Loki クエリ実行設定（最大レバー・リスク小）→ 実装対象

`loki-config.yml` に追加:

```yaml
limits_config:
  split_queries_by_interval: 24h                  # 1h → 24h（7d クエリ: 168分割 → 7分割）
  split_instant_metric_queries_by_interval: 24h   # instant も同様（今回の主犯）
querier:
  max_concurrent: 16                              # 4 → 16（ローカル単機、コア数相応）
query_range:
  cache_results: true
  results_cache:
    cache:
      embedded_cache: { enabled: true, max_size_mb: 100 }
  cache_instant_metric_results: true
  instant_metric_query_split_align: true          # キャッシュ境界に分割を整列
  instant_metric_results_cache:
    cache:
      embedded_cache: { enabled: true, max_size_mb: 100 }
chunk_store_config:
  chunk_cache_config:
    embedded_cache: { enabled: true, max_size_mb: 256 }  # auto-refresh の再読込を吸収
```

期待効果: 1クエリ 1.0〜1.2s → 数十ms〜0.2s。Cost 画面 2分42秒 → 十数秒以下。
（quantile の 58ms が「分割なし」の実測下限を与えている）

### P2: ダッシュボード修正 → 実装対象

- Today/Workspace の過去3週平均クエリを `or vector(0)` で修正（§2.2-1）
- Session List / Context の refresh を 30s → 1m（§2.2-2）

### P3: 未使用イベントのドロップ（衛生）→ 実装対象

collector に filter processor を追加し `hook_execution_start` / `hook_execution_complete` /
`hook_registered` を logs パイプラインから除外（行数の約2/3）。
消費者ゼロを確認済み（全9ダッシュボード・analysis/・scripts/ を横断 grep）。
※ hook のデバッグが必要になったら filter を一時的に外す運用（docs 注記）。

### 見送り（理由つき）

- **Cost 画面のクエリ統合・パネル削減**: P1 のキャッシュで解消見込み。判断画面の情報密度を
  下げるデメリットの方が大きい。
- **Prometheus 停止**: ダッシュボード未参照は事実だが、SDK メトリクスの受け皿という
  ADR 0001 の位置づけ通り。メモリ 29MB で害がない。
- **retention 短縮**: 90日で 9.3MB。問題にならない。

## 4. 検証方法

適用後に同一条件で再計測して本ファイルに追記する:

1. `/render` で Cost / Today / Session List の描画時間（§1.1 と同一 URL・サイズ）
2. 代表4クエリの stats.summary（§1.2 と同一クエリ）
3. 2回目ロード（キャッシュヒット時）の描画時間

## 5. 検証結果（after・2026-07-17 実施）

P1〜P3 を適用し、loki / otel-collector / grafana を再起動して同一条件で再計測した。

### 5.1 画面フル描画時間

| 画面 | before | after | 倍率 |
|---|---|---|---|
| Cost Optimization (7d) | 2分42秒 | **20.6秒** | **7.9x** |
| Today | 22.6秒 | **6.9秒** | 3.3x |
| Session List (7d) | 36.9秒 | **6.2秒** | 5.9x |

/render はブラウザ起動＋全パネル完了待ちを含むため 4〜5秒の固定オーバーヘッドがある。
ブラウザでの体感（プログレッシブ描画・可視パネルのみロード）はさらに速い。

### 5.2 個別クエリ

| クエリ | before (wall) | after (wall 初回) | after (キャッシュヒット) |
|---|---|---|---|
| コスト合計 7d instant | ~1.0s | 0.26s | **0.06s** |
| セッション別コスト 7d instant | ~1.2s（並行時 ~4.6s） | **0.09s** | — |

queue 時間は 26〜35s → ほぼゼロ。totalLinesProcessed も 13,303 → 6,028 に減少
（1h分割時は境界チャンクをサブクエリごとに重複デコードしていたことの傍証）。

### 5.3 Today「過去3週平均」線の修正確認

`or vector(0)` 修正後、凡例と点線（現状 $0）が描画されることをレンダリングで確認。
運用3週間が経過すると実平均が乗る。

### 5.4 【重要な副次発見】Grafana file provisioning が自動再読込されない

検証中、**ダッシュボード JSON を編集してもライブの Grafana に反映されない**ことを発見した
（Grafana 13.0.1。コンテナ内のファイルは更新済みなのに live 定義が数日前のまま。
provisioning のポーリングが機能していない）。過去に「ブラウザの画面定義が古い」と感じた
事象の根本原因である可能性が高い。

**運用ルール: ダッシュボード JSON を変更したら `docker compose restart grafana` を実行する。**
本検証の before 計測は旧定義に対するものだが、速度差は Loki 側設定によるもので結論に影響しない。

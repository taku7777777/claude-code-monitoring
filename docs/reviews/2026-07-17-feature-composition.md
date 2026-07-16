# 機能構成レビュー（妥当性精査と改善点）

日付: 2026-07-17 / 対象: リポジトリ全体（ダッシュボード9枚・scripts 20本・analysis・pricing・docs）。
性能面は [2026-07-17-data-query-performance.md](2026-07-17-data-query-performance.md) に分離。

## TL;DR

構成は**概ね妥当**。画面階層（考える2画面＋ドリルダウン）、docs と実装の 1:1 対応、
価格SSOT→生成マーカー→--check のパイプラインは設計通りに機能している。
是正すべきは3点: **(1) session-list 生成器の drift**（再実行すると手編集が消える・生成器自身が自認）、
**(2) prompt_summary 系一式が消費者ゼロ**（専用コンテナ＋Loki 設定延長が空回り）、
**(3) requirements.md 冒頭の陳腐化記述**。

## 1. 妥当と評価した点

| 観点 | 事実 | 評価 |
|---|---|---|
| 画面階層 | Today / Cost Optimization の「考える2画面」＋ Usage/Context/Session List・Detail/Prompt List・明細へのドリルダウン。docs/3-requirements の9ページと実装9枚が **1:1 完全対応** | 意図された IA が実装に反映されている |
| クローン生成方式 | Session Detail = Usage のクローン（パネルタイトル100%一致）、Workspace = Today のクローン（1タイトル差のみ）。生成器 `gen-session-detail.py` / `gen-workspace-filtered-today.py` は 07-15 に実物へ追随済み | フィルタ付きクローンを生成器で保証する方針は一貫 |
| 価格・コスト計算 | pricing.yaml（SSOT）→ pricing.py（唯一のローダ）→ gen-cost-recalc.py → collector の GENERATED マーカー間へ書込。`--check` 実行で **現状ドリフトなし（OK）** を確認 | 生成マーカー＋checkモード完備。模範的 |
| Loki ラベル方針 | index_label は5つのみ、高カーディナリティは structured metadata（CONTRACT §4） | cardinality 制御が効いている |
| scripts 配線 | hook 6本は settings.example.json から、tailer は compose から参照。**完全な孤児スクリプトなし**（CLI 5本は手動運用が設計意図） | 参照経路が説明可能 |
| Prometheus | datasource は provisioning 済みだが**パネル参照は9枚全てで0件** | ADR 0001「受け皿のみ」の宣言通り。現状維持で可 |

## 2. 是正すべき点

### 2.1 session-list 生成器の drift【実装対象】

- `gen-session-list.py` は **2パネル**（セッション数 stat＋一覧 table）しか生成しないが、
  デプロイ済み `claude-code-session-list.json` は **3パネル**（合計コストタイルが手追加）＋
  開始日時/最終更新/継続時間/$1M実効 カラムが手追加されている。
- 生成器の docstring 自身が「そのまま再実行すると手追加分が失われる」と警告している状態
  （警告を書いて放置＝ドリフトの恒久化）。07-14 の regex 修正も生成物への直接パッチだった。
- **是正**: 生成器を現行デプロイ JSON を再現するよう retrofit し、
  「再生成 → デプロイ済みと diff ゼロ」を確認して警告 docstring を削除する。
  3枚の生成ダッシュボード全てで同じ検証（regen→diff）を通す。

### 2.2 prompt_summary 系一式が消費者ゼロ【推奨・要判断】

- 生成側は稼働中: 専用コンテナ `prompt-summary-tailer`（30秒周期）＋ CONTRACT §3.4 ＋
  `loki-config.yml` の `max_chunk_age: 48h`（last-wins 追記の out-of-order 対策専用）。
- 消費側はゼロ: プロンプト一覧は「prompt_summary（テイラー）はコストを過小計上するため
  使用しない」とダッシュボード内に明記し、api_request 集計へ移行済み。他の画面・analyzer にも参照なし。
- つまり**「作る仕組みだけがフル装備で残り、読む者がいない」**。コンテナ1台・イベント140行/日・
  Loki メモリ（チャンク48h保持）を空費し、CONTRACT に死んだ契約が残る。
- **推奨**: tailer サービスと CONTRACT §3.4 を廃止し、`max_chunk_age` を既定(2h)へ戻す。
  ただし §3.4 は「変更してはならない」と明記された契約であり、復活の可能性
  （時間窓ドリルダウンの高速化用途）を考えるなら「dormant（休眠）」と明記して残す選択もある。
  → **一存で削除はせず、本レビューでは判断材料の提示に留める**（削除は不可逆: 蓄積データの継続性が切れる）。

### 2.3 requirements.md 冒頭の陳腐化【実装対象・軽微】

- 「2つのダッシュボード（利用量/コンテキスト分析）」→ 実態は9枚。
- 「16件の ADR」→ 実ファイルは6件（16件は §12 の教訓数と混同）。
- 自ら「docs/ が正典・本ファイルは陳腐化」と宣言済みではあるが、エグゼクティブサマリの
  数字の誤りは読者を誤導するため最小修正する。

## 3. 注記（是正不要だが認識しておく点）

- **docs の記述深度の非対称**: today/ と cost-optimization/ はセクション別ファイルまで整備、
  usage/ workspace/ session-list/ session-detail/ は README 単独。閲覧頻度に比例しており妥当だが、
  session-list の手追加パネル（§2.1）はどの docs にも反映されていなかった。生成器 retrofit 時に
  session-list/README も追随させる。
- **未実装の合意事項**: イベント帰属分析（analyzer [i] 構想、3-requirements/README「検討中の拡張」）は
  明示的に未実装のまま。放置ではなく「方向性合意・未着手」という管理された状態。
- **手動 CLI 5本**（task-outcome.sh / intervention-marker.sh / show-session.sh /
  init-task-workspace.sh / claude-wrapper.zsh）は自動配線なしが設計意図（人間の判断を記録する接点）。
  ただし task_outcome の自動 completed と手動ラベルの重複問題は README に注記済みで運用課題として残る。
- **SDK 由来の未使用イベント**（auth, at_mention, feedback_survey 等）は CONTRACT が
  「使うイベントの契約」と「観測カタログ（1-references/events.md）」を分離しているため矛盾ではない。
  大量流入する hook_execution_* のみ性能レビュー側でドロップを提案。

## 4. 実装サマリ

| # | 項目 | 種別 | 状態 |
|---|---|---|---|
| 1 | Loki クエリ設定最適化（性能レビュー P1） | 設定 | ✅ 完了（Cost 2分42秒→20.6秒。性能レビュー §5） |
| 2 | Today/Workspace 3週平均クエリ修正＋refresh 見直し（P2） | ダッシュボード | ✅ 完了（描画をレンダリングで確認） |
| 3 | hook_execution_* ドロップ（P3） | collector | ✅ 完了（filter processor 追加・events.md に注記） |
| 4 | gen-session-list.py retrofit＋3生成器の regen-diff 検証（§2.1） | 生成器 | ✅ 完了（3生成器とも再実行で意図した差分のみ＝冪等） |
| 5 | requirements.md 数字修正（§2.3） | docs | ✅ 完了 |
| 6 | prompt_summary 系の廃止 or dormant 宣言（§2.2） | 契約変更 | **ユーザー判断待ち** |

追加発見: Grafana 13 の file provisioning が自動再読込されず、JSON 変更が live に反映されない
（性能レビュー §5.4）。**ダッシュボード変更後は `docker compose restart grafana` が必要**。

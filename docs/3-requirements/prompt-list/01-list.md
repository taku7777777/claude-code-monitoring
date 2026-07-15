---
title: "一覧"
id: claude-code-prompt-list-main
dashboard: claude-code-prompt-list
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
panels: { 1: 2, 2: 3, 3: 1 }
---
# 一覧

データ源は一次イベント `api_request`（[CONTRACT §3.1](../../CONTRACT.md)）＋
`user_prompt`。旧 `prompt_summary`（テイラーの last-wins サマリ）は**コストを
過小計上する**ため使わない。`api_request` を `prompt_id` で集計し（コスト＝
`cost_recalc` 優先の合計＝**セッション/ワークスペースの総コストと一致**、
実効tok合計、`cache_creation / output` で取り込み比、`observed_timestamp` の
min/max で開始/終了）、`user_prompt` から prompt 本文を結合する。
窓は Today のバークリックから `var-wfrom / var-wto`（ms）で渡り、**窓内に入った
API 呼び出しのみを集計**する（未指定＝全量）。ページの時間レンジ（既定 now-7d）は
「検索対象範囲」であって窓そのものではない。

## 1. 窓開始

### 表示
- 渡された窓の開始時刻（wfrom）。直接開いた場合は「(未指定)」

### 活用
- どの5分窓を見ているかの確認（時間ピッカーは検索範囲を示すため、窓はここで読む）

## 2. 窓終了

### 表示
- 渡された窓の終了時刻（wto）

### 活用
- 同上

## 3. プロンプト一覧（session/workspace のプロンプト・実コスト）

### 表示
- `api_request` を `prompt_id` で集計したプロンプト一覧（コスト降順）。
  カラムは **開始 / 終了 / 実行時間 / コスト / 実効tok / 取り込み比 / prompt / prompt_id**
- 開始・終了 = そのプロンプトの API 呼び出しの `observed_timestamp` の min/max
  （最初/最後の API 呼び出し）。**実行時間** = 終了 − 開始
- コスト = `cost_recalc` 優先の合計。総和は**セッション/ワークスペースの総コストと一致**
  （旧 summary はコストを過小計上したためこの方式に変更）。実効tok は合計、
  取り込み比 = `cache_creation` 合計 / `output` 合計
- prompt 本文は `user_prompt` から `prompt_id` で結合（`lastNotNull`）
- 窓（wfrom/wto）指定時は **窓内に入った API 呼び出しだけ**を集計する。
  開始/終了/コスト等は窓で切られた寄与分になる（未指定＝全量）
- フッタの合計 = 表示中プロンプトの総コスト・総実効tok
- 制約: API 呼び出しが1件も無い依頼（即中断等・コスト0）は `api_request` が
  無いため載らない

### 活用
- Today バーン推移のスパイク → バークリック → ここで犯人特定 →
  prompt_id クリックで Prompt明細（トークン内訳・経路・ツール）へ
- コストがセッション総額と突き合う（過小計上しない）ので、犯人プロンプトの
  実額をそのまま信頼できる。実行時間で「長引いた依頼」も切り分けられる
- 時間ピッカーを動かすと検索対象範囲が変わるだけで窓は不変
  （窓を変えたいときは Today から別のバーをクリックする）

# Context（診断ドリルダウン）ページ要件

## 背景 — なぜこのページがあるのか

診断の背骨は **workspace（タスク）→ session（持ち方）→ prompt（依頼）→
API（生リクエスト）** の4層ドリルダウン。このうち prompt → API は Prompt明細が
1ページで畳んでおり、本ページが **workspace → session の2層**を畳む。
「このタスクなんか高い → どのセッションが → 持ち方の何が → どの依頼が」を
リンクだけで降りられるようにする。

## 目的

1. **workspace の一次切り分け**: どのセッションが問題かをコスト・context・
   リクエスト数の一覧で即断する
2. **セッションの持ち方の診断**: 開きっぱなし・肥大・compaction 癖・
   キャッシュの谷を時系列で特定する
3. **prompt への橋渡し**: 原因の依頼を top10 から特定して Prompt明細へ降ろす

## 設計思想

- **2段変数**: `workspace`（タスク絞り込み）と `session_id`
  （セッション詳細ビュー。既定 `.*` = 全セッション）。全パネルが両変数で絞られる
- **リンク駆動（事象駆動ページ）**: 自発的に開かず、上流からのリンクで入る
  - 入口: Today の context推移（点クリック → session_id 付き）/ Usage の
    ws別コスト推移・ws別コスト円グラフ / Cost Opt タスク別サマリの workspace 列
    （いずれも var-workspace 付き遷移）
  - ページ内: セッション一覧・セッション別最大 top10 → 自遷移で session_id 絞り込み /
    prompt別 top10 ×3 → Prompt明細
- **セッション詳細ビュー**: session_id を1つに絞ると、セッション単体の累計コスト・
  context 推移・キャッシュの谷が単独で見える
- 旧「Context Usage」の週次診断系パネル（model別・query_source別cacheRead・
  tool_name別）は Cost Opt と重複のため削除（2026-07-13 ゼロベース見直し）

## セクション構成（表示順＝読み順＝ドリルの順）

| # | セクション | 問い | ファイル |
|---|---|---|---|
| 1 | workspace サマリ | どのセッションが問題か | [01-workspace.md](01-workspace.md) |
| 2 | セッション詳細 | 持ち方の何が問題か | [02-session.md](02-session.md) |
| 3 | prompt 入口 | どの依頼が原因か | [03-prompt-entry.md](03-prompt-entry.md) |

セクション横断の前提は [../README.md](../README.md) / [../TEMPLATE.md](../TEMPLATE.md)。
深掘り手順は [RUNBOOK §3](../../RUNBOOK.md)。dashboard uid は `claude-code-context` の
まま不変（行リンク維持のため。表示タイトルのみ「診断ドリルダウン」）。

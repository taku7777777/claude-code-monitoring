---
title: "明細（生リクエスト）"
id: claude-code-prompt-api-detail
dashboard: claude-code-prompt
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
panels: { 1: 11 }
---
# 明細（生リクエスト）

集計で結論が出ない時の最終手段。1リクエストずつ因果を追う。

## 1. API呼び出し明細

### 表示
- この往復を構成する生 API リクエストの時系列
  （cost_recalc / cost_usd・トークン4種・context_tokens・duration・モデル）

### 活用
- コスト発生の順序・context の伸び方を1行ずつ追う
- 会話の全文が必要なら `scripts/show-session.sh <session_id>` でローカル履歴から
  サルベージする（telemetry は本文までしか持たない）

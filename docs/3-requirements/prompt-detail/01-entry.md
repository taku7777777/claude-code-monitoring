---
title: "入口"
id: claude-code-prompt-entry
dashboard: claude-code-prompt
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
panels: { 1: 12 }
---
# 入口

prompt_id 未指定でも機能する唯一のセクション。以降のセクションは prompt_id 指定が前提。

## 1. 直近のプロンプト一覧（入口）

### 表示
- 表示期間内の user_prompt を新しい順に一覧（Time / prompt_id / コスト / 本文）
- コスト列はプロンプト単位の期間合計（api_request の cost_recalc を prompt_id で結合）
- prompt_id 未指定でも表示される唯一のパネル

### 活用
- このページを直接開いた時の入口。prompt_id リンクで変数に反映して明細を開く
- 「さっきの依頼いくらだった？」の即答用
- コスト列ヘッダのクリックで高額順に並べ替えて、高い依頼から確認する
- 通常の入口は Cost Opt「1. コストドライバ prompt別 top15」/ Context の
  prompt テーブルの行リンク（高い順に潰す動線）

---
title: "prompt 入口"
id: claude-code-context-prompt-entry
dashboard: claude-code-context
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
# 欠番: 旧 13(tool_name別top15)・14(query_source別cacheRead)・15(model別cost) は
# 週次診断（Cost Opt）と重複のため削除（2026-07-13）
panels: { 1: 12, 2: 16, 3: 17 }
---
# prompt 入口 — どの依頼が原因か

ドリルダウンの3層目への橋渡し。原因の依頼を特定して Prompt明細
（prompt → API の下2層）へ降ろす。3表とも行クリックで Prompt明細へ遷移する。

## 1. prompt別cost top10

### 表示
- 絞り込み範囲の prompt 単位コスト上位10

### 活用
- 高額往復の特定 → Prompt明細で「なぜ高い」（本体/委任/背景 → トークン → 経路）

## 2. prompt別取り込み/生成比 top10

### 表示
- prompt 単位の cache_creation ÷ output 上位10

### 活用
- 15超 = 読み込み過多の依頼 → 次から委任・対象限定（O4/O10）の学習

## 3. prompt別turn数 top10

### 表示
- prompt 単位の turn 数上位10

### 活用
- turn 数過多 = 1プロンプトに詰め込みすぎ / 手戻りの多い依頼の検知

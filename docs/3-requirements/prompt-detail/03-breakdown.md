---
title: "内訳"
id: claude-code-prompt-breakdown
dashboard: claude-code-prompt
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
panels: { 1: 7, 2: 8, 3: 9, 4: 10 }
---
# 内訳（本文・トークン・経路・ツール）

「なぜその値段になったか」を本文・トークン構成・経路・ツールの4面から特定する。

## 1. プロンプト本文

### 表示
- user_prompt の実文（OTEL_LOG_USER_PROMPTS=1 で収集）

### 活用
- 「何をどんな前提で頼んだか」の想起（UC1 の核。数日後の振り返りで必須）
- 誤診防止: 本文が妥当でも高額なら要因は文面以外にある
  （実例: $20.2 の高額往復の主因は away_summary だった）

## 2. トークン種別内訳

### 表示
- この往復の input / output / cache_read / cache_creation 構成比

### 活用
- cache_read 支配 ＝ 文脈引きずり型（セッションの重さの問題）
- cache_creation 大 ＝ 新規取り込み型（読み込み過多・委任不足の疑い）

## 3. モデル × query_source 別コスト

### 表示
- この往復のコストをモデル × 発生経路（repl / agent:* / away_summary 等）で分解

### 活用
- 「どのモデル・どの経路で」発生したかの確定
- 背景機能（away_summary / prompt_suggestion）混入の発見場所

## 4. 使用ツール回数

### 表示
- この往復の tool_decision 内訳（tool_name 別回数）

### 活用
- Read 連打 ＝ 探索過多（委任候補）の確認
- Agent の有無 ＝ 委任判断の振り返り

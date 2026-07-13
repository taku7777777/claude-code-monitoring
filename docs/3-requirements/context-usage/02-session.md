---
title: "セッション詳細"
id: claude-code-context-session
dashboard: claude-code-context
# 見出し連番 → Grafana panel id（連番は追記専用・振り直し禁止）
panels: { 1: 6, 2: 7, 3: 8, 4: 9, 5: 11, 6: 2, 7: 3, 8: 10, 9: 18 }
---
# セッション詳細 — 持ち方の診断

ドリルダウンの2層目。セッションの「持ち方」（開きっぱなし・肥大・compaction 癖・
キャッシュの途切れ）を時系列で診断する。

## 1. セッション別context量推移

### 表示
- session ごとの context 推移（疎な点も常時表示）

### 活用
- 「開きっぱなしで太った」セッションの特定 → セッション切りの判断
- Today の同型パネル（直近3h）より長いレンジで癖を見る

## 2. セッション別最大context量 top10

### 表示
- session ごとの最大 context ランキング
- **クリックで自遷移し、そのセッションに絞り込む**

### 活用
- 太ったセッションの即答 → 絞り込んで原因の時間帯を特定

## 3. compaction直前のコンテキスト量 top10

### 表示
- compaction 発生時の pre_tokens 上位

### 活用
- 上限近くまで引きずってから compaction する癖の検知 → 早めのセッション切り・
  手動 /compact（O1）へ

## 4. cacheRead比率

### 表示
- キャッシュヒット率の時系列

### 活用
- 谷の発生時刻分析（設定変更・放置・新セッションのどれが原因かを時刻で突合 —
  判断基準は today/judge-and-change-scenario.md「キャッシュ有効率の判断基準」）

## 5. 取り込み/生成比 推移

### 表示
- cache_creation ÷ output（1hビン）。15超は読み込み過多の目安

### 活用
- 「読んでばかりで書いていない」時間帯の検知 → 対象限定・委任（O4/O10）

## 6. auto-compact発生回数

### 表示
- trigger=auto の件数（0 は 0 と表示 — vector(0) 補完）

### 活用
- 頻発＝取り込み過多のワークフロー。「3. compaction直前 top10」とセットで読む

## 7. manual /compact発生回数

### 表示
- trigger=manual の件数（0 は 0 と表示）

### 活用
- 意図的操作の記録。直後の cache 再構築コストとの突合基準点

## 8. compaction duration (p95 / avg)

### 表示
- compaction 処理時間の分位点

### 活用
- 長時間化はセッション肥大の副作用（負荷面の監視）

## 9. 直近のcompactionイベント (生ログ)

### 表示
- compaction イベントの生ログ（最終手段）

### 活用
- 個別 compaction の trigger / pre/post トークンの確認

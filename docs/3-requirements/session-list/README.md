# Session List ページ要件

dashboard uid: `claude-code-session-list` / 既定レンジ: now-7d / 生成器: `scripts/gen-session-list.py`

## 背景・目的

エンティティ階層 **Workspace → Session → Prompt → Api Request** の「Session を選ぶ」層。
filtered-Today（workspace 絞り込み）から「▸ Session List」ボタンで着地し、その workspace の
セッションを一覧・ソート・検索して、深掘り対象の1セッションを選んで Session Detail へ渡す。

## 設計思想

- **List に徹する**: 分析はしない。選ぶための表（コスト降順・列ソート・session_id 検索）に限定
- **`api_request` を `sum by (session_id)` で集計**（コスト＝`cost_recalc` 優先の合計 /
  最大context / リクエスト数 / 実効tok、`observed_timestamp` の min/max で開始日時・最終更新）。
  コストは workspace 総額と整合し、上部の「合計コスト」タイルはその総和
- **7d 既定**: セッションは複数日にまたがるため、当日より広めに一覧する
- 変数は `workspace` のみ（session は選ぶ対象なので変数にしない）

## パネル

- **セッション数（期間内）**: 一覧の分母
- **合計コスト（期間内）**: 選択期間・workspace 内の全 `api_request` のコスト合計
  （`cost_recalc` 優先）。一覧のコスト列の総和
- **セッション一覧**: session_id / 開始日時 / 最終更新 / 継続時間 / コスト / **$/1M実効** /
  リクエスト数 / 最大context（列ヘッダでソート、session_id 列は検索フィルタ可）。
  開始日時・最終更新でいつ動いていたか、継続時間で長さ、$/1M実効で単価効率
  （高い＝割高／要最適化）、最大context で圧迫度を読む。
  **行クリックで Session Detail（`claude-code-session`・`var-workspace` + `var-session_id`
  付き）へ遷移**

## 入口 / 出口

- 入口: 全画面共通ナビの「Sessions」ボタン（`includeVars` で現 workspace を引き継ぐ。正本 `scripts/apply-nav-links.py`）
- 出口: 行クリック → [Session Detail](../session-detail/README.md)
- 関連: 数式・uid の正本 [../../CONTRACT.md](../../CONTRACT.md) §8
